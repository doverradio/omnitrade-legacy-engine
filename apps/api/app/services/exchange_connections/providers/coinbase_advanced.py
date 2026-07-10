from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac

import httpx

from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.services.exchange_connections.providers.base import (
    ExchangeAccountSnapshot,
    ExchangeAuthResult,
    ExchangeBalanceItem,
    ExchangeBalanceSnapshot,
    ExchangePermissionSnapshot,
)


def _to_decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _mask_permissions(raw_payload: dict[str, object]) -> list[str]:
    direct = raw_payload.get("permissions")
    if isinstance(direct, list):
        return sorted({str(item) for item in direct if str(item).strip()})

    data = raw_payload.get("data")
    if isinstance(data, dict):
        nested_permissions = data.get("permissions")
        if isinstance(nested_permissions, list):
            return sorted({str(item) for item in nested_permissions if str(item).strip()})

    return []


def parse_coinbase_permissions(payload: dict[str, object]) -> list[str]:
    return _mask_permissions(payload)


def parse_coinbase_balances(payload: dict[str, object]) -> ExchangeBalanceSnapshot:
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        accounts = []

    by_currency: dict[str, ExchangeBalanceItem] = {
        "USD": ExchangeBalanceItem(currency="USD", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
        "BTC": ExchangeBalanceItem(currency="BTC", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
        "ETH": ExchangeBalanceItem(currency="ETH", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
    }

    for row in accounts:
        if not isinstance(row, dict):
            continue

        available_block = row.get("available_balance") if isinstance(row.get("available_balance"), dict) else {}
        hold_block = row.get("hold") if isinstance(row.get("hold"), dict) else {}

        currency = str(available_block.get("currency") or row.get("currency") or "").upper()
        if currency not in by_currency:
            continue

        available = _to_decimal(available_block.get("value"))
        reserved = _to_decimal(hold_block.get("value"))
        total = available + reserved

        prior = by_currency[currency]
        by_currency[currency] = ExchangeBalanceItem(
            currency=currency,
            available=prior.available + available,
            reserved=prior.reserved + reserved,
            total=prior.total + total,
        )

    balances = [by_currency["USD"], by_currency["BTC"], by_currency["ETH"]]
    total_equity_usd = by_currency["USD"].total
    return ExchangeBalanceSnapshot(balances=balances, total_equity_usd=total_equity_usd)


def parse_coinbase_account_status(payload: dict[str, object]) -> str | None:
    accounts = payload.get("accounts")
    if isinstance(accounts, list) and accounts:
        first = accounts[0]
        if isinstance(first, dict):
            status = first.get("status")
            if status is not None and str(status).strip():
                return str(status)
    return None


class CoinbaseAdvancedClient:
    provider = "coinbase_advanced"

    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def test_authentication(self, *, credentials: dict[str, str], environment: str) -> ExchangeAuthResult:
        heartbeat_at = datetime.now(timezone.utc)
        try:
            accounts_payload = await self._request_json(
                method="GET",
                path="/api/v3/brokerage/accounts",
                credentials=credentials,
                environment=environment,
            )
            permissions_payload = await self._request_json(
                method="GET",
                path="/api/v3/brokerage/key_permissions",
                credentials=credentials,
                environment=environment,
                swallow_404=True,
            )
        except Exception as exc:
            return ExchangeAuthResult(
                reachable=False,
                authenticated=False,
                account_status=None,
                permissions=[],
                heartbeat_at=heartbeat_at,
                error=str(exc),
            )

        return ExchangeAuthResult(
            reachable=True,
            authenticated=True,
            account_status=parse_coinbase_account_status(accounts_payload),
            permissions=parse_coinbase_permissions(permissions_payload),
            heartbeat_at=heartbeat_at,
            error=None,
        )

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        payload = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/accounts",
            credentials=credentials,
            environment=environment,
        )
        return parse_coinbase_balances(payload)

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        payload = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/accounts",
            credentials=credentials,
            environment=environment,
        )
        return ExchangeAccountSnapshot(account_status=parse_coinbase_account_status(payload))

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        payload = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/key_permissions",
            credentials=credentials,
            environment=environment,
            swallow_404=True,
        )
        permissions = parse_coinbase_permissions(payload)
        return ExchangePermissionSnapshot(permissions=permissions, verified=len(permissions) > 0)

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        credentials: dict[str, str],
        environment: str,
        swallow_404: bool = False,
    ) -> dict[str, object]:
        base_url = self._base_url(environment)
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        body = ""
        signature = self._sign_request(
            secret=credentials["api_secret"],
            timestamp=timestamp,
            method=method,
            path=path,
            body=body,
        )
        headers = {
            "CB-ACCESS-KEY": credentials["api_key"],
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-ACCESS-PASSPHRASE": credentials.get("passphrase", ""),
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.request(method, path, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(message="Coinbase API is unreachable", details={"provider": self.provider}) from exc

        if swallow_404 and response.status_code == 404:
            return {}

        if response.status_code >= 400:
            raise InvalidRequestError(
                message="Coinbase API request failed",
                details={"status_code": response.status_code, "path": path},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidRequestError(message="Coinbase API returned invalid JSON", details={"path": path}) from exc

        if not isinstance(payload, dict):
            raise InvalidRequestError(message="Coinbase API returned unexpected payload", details={"path": path})

        return payload

    def _base_url(self, environment: str) -> str:
        normalized = environment.strip().lower()
        if normalized == "sandbox":
            return "https://api-public.sandbox.exchange.coinbase.com"
        return "https://api.coinbase.com"

    def _sign_request(self, *, secret: str, timestamp: str, method: str, path: str, body: str) -> str:
        message = f"{timestamp}{method.upper()}{path}{body}".encode("utf-8")
        key = base64.b64decode(secret.encode("utf-8"))
        digest = hmac.new(key, message, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")
