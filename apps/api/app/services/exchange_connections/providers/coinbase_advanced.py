from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
import email.utils
import os
import secrets
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization

from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.services.exchange_connections.providers.base import (
    ExchangeOrderSubmissionRequest,
    ExchangeOrderSubmissionResult,
    ExchangeAccountSnapshot,
    ExchangeAuthResult,
    ExchangeBalanceItem,
    ExchangeBalanceSnapshot,
    ExchangeProviderAmbiguousResponse,
    ExchangeProviderFee,
    ExchangeProviderFill,
    ExchangeProviderHealth,
    ExchangeProviderMetadata,
    ExchangeProviderOrder,
    ExchangeProviderRejection,
    ExchangeProductSnapshot,
    ExchangePreviewResult,
    ExchangePermissionSnapshot,
    ProviderCapability,
)

JWT_EXP_SECONDS = 120
CLOCK_SKEW_FAIL_SECONDS = 30


def _sandbox_mock_mode_enabled() -> bool:
    return str(os.getenv("OT_COINBASE_SANDBOX_MOCK_MODE", "false")).strip().lower() in {"1", "true", "yes", "on"}


def sandbox_mock_mode_enabled() -> bool:
    return _sandbox_mock_mode_enabled()


def _to_decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def normalize_private_key(raw_key: str) -> str:
    key = raw_key.strip()
    if "\\n" in key:
        key = key.replace("\\n", "\n")
    return key


def _jwt_algorithm_for_private_key(private_key_pem: str) -> str:
    normalized = private_key_pem.upper()
    if "BEGIN EC PRIVATE KEY" in normalized or "BEGIN PRIVATE KEY" in normalized:
        # PKCS8 private keys may still be Ed25519. We detect by parsing below.
        pass

    key_obj = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    class_name = key_obj.__class__.__name__.lower()
    if "ed25519" in class_name:
        return "EdDSA"
    return "ES256"


def build_coinbase_jwt(
    *,
    api_key_name: str,
    private_key: str,
    request_method: str,
    request_host: str,
    request_path: str,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    nbf = int(current.timestamp())
    exp = nbf + JWT_EXP_SECONDS
    uri = f"{request_method.upper()} {request_host}{request_path}"
    normalized_key = normalize_private_key(private_key)
    alg = _jwt_algorithm_for_private_key(normalized_key)

    headers = {
        "typ": "JWT",
        "kid": api_key_name,
        "nonce": secrets.token_hex(16),
        "alg": alg,
    }
    payload = {
        "sub": api_key_name,
        "iss": "cdp",
        "aud": ["cdp_service"],
        "nbf": nbf,
        "exp": exp,
        "uri": uri,
    }
    return jwt.encode(payload, normalized_key, algorithm=alg, headers=headers)


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


def _permission_flags(permissions: list[str]) -> tuple[bool, bool]:
    lowered = [item.lower() for item in permissions]
    withdrawal = any("withdraw" in item or "transfer" in item for item in lowered)
    trade = any("trade" in item or "order" in item for item in lowered)
    return withdrawal, trade


def _decimal_field(payload: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = payload.get(name)
        if value is None or value == "":
            continue
        try:
            return Decimal(str(value))
        except Exception:
            continue
    return None


def _provider_timestamp(payload: dict[str, Any]) -> datetime | None:
    for key in ("completion_time", "last_fill_time", "created_time", "created_at"):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            text = raw.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def normalize_coinbase_preview_response(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in (
        "preview_id",
        "product_id",
        "side",
        "order_type",
        "success",
    ):
        if key in payload:
            normalized[key] = payload[key]

    numeric_fields = {
        "estimated_average_price": ("estimated_average_price", "average_filled_price", "price"),
        "estimated_total_value": ("estimated_total_value", "estimated_quote_size", "quote_size", "total_value"),
        "estimated_base_size": ("estimated_base_size", "base_size"),
        "estimated_quote_size": ("estimated_quote_size", "quote_size"),
        "estimated_fee": ("estimated_fee", "commission_total", "fee", "total_fees"),
        "estimated_slippage": ("estimated_slippage",),
        "estimated_commission_total": ("estimated_commission_total", "commission_total", "total_fees"),
        "best_bid": ("best_bid", "best_bid_price"),
        "best_ask": ("best_ask", "best_ask_price"),
    }
    for target, candidates in numeric_fields.items():
        value = _decimal_field(payload, *candidates)
        if value is not None:
            normalized[target] = format(value, "f")

    warning_messages = payload.get("warning_messages") or payload.get("warnings") or []
    if isinstance(warning_messages, list):
        normalized["warning_messages"] = [str(item) for item in warning_messages if str(item).strip()]
    else:
        normalized["warning_messages"] = []

    failure_reason = payload.get("failure_reason") or payload.get("preview_failure_reason") or payload.get("new_order_failure_reason")
    if failure_reason is not None:
        normalized["failure_reason"] = str(failure_reason)

    for key in ("message", "error", "status"):
        if key in payload and key not in normalized:
            normalized[key] = payload[key]

    return normalized


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
    _metadata = ExchangeProviderMetadata(
        provider_key="coinbase_advanced",
        display_name="Coinbase Advanced",
        supported_environments=("production", "sandbox"),
        supported_asset_classes=("crypto",),
        capabilities=(
            "authentication",
            "permissions",
            "account_readiness",
            "balance_read",
            "product_lookup",
            "price_evidence",
            "preview_market_order",
            "create_order",
            "stable_client_order_id",
            "order_lookup_provider_id",
            "order_lookup_client_id",
            "order_lookup_history",
            "fill_lookup",
            "fee_reporting",
            "sandbox",
            "controlled_mock",
            "health_observability",
        ),
    )

    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._last_successful_call_at: datetime | None = None
        self._last_error_classification: str | None = None
        self._last_error_message: str | None = None

    @property
    def metadata(self) -> ExchangeProviderMetadata:
        return self._metadata

    def supports_capability(self, capability: ProviderCapability) -> bool:
        return capability in self._metadata.capabilities

    def mock_mode_enabled(self) -> bool:
        return _sandbox_mock_mode_enabled()

    async def current_health(self, *, environment: str) -> ExchangeProviderHealth:
        capability_status = {capability: "supported" for capability in self._metadata.capabilities}
        if environment not in self._metadata.supported_environments:
            capability_status["environment"] = "unsupported"
        return ExchangeProviderHealth(
            provider_key=self._metadata.provider_key,
            environment=environment,
            last_successful_call_at=self._last_successful_call_at,
            last_error_classification=self._last_error_classification,
            last_error_message=self._last_error_message,
            supports_latency=False,
            capability_status=capability_status,
        )

    async def test_authentication(self, *, credentials: dict[str, str], environment: str) -> ExchangeAuthResult:
        heartbeat_at = datetime.now(timezone.utc)
        try:
            accounts_payload, accounts_headers = await self._request_json(
                method="GET",
                path="/api/v3/brokerage/accounts",
                credentials=credentials,
                environment=environment,
            )
            permissions_payload, _perm_headers = await self._request_json(
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
                clock_skew_seconds=None,
                withdrawals_permission_granted=False,
                trade_permission_present=False,
                error=str(exc),
            )

        permissions = parse_coinbase_permissions(permissions_payload)
        withdrawals_granted, trade_permission_present = _permission_flags(permissions)
        clock_skew = self._clock_skew_seconds(accounts_headers)

        return ExchangeAuthResult(
            reachable=True,
            authenticated=True,
            account_status=parse_coinbase_account_status(accounts_payload),
            permissions=permissions,
            heartbeat_at=heartbeat_at,
            clock_skew_seconds=clock_skew,
            withdrawals_permission_granted=withdrawals_granted,
            trade_permission_present=trade_permission_present,
            error=None,
        )

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        payload, _headers = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/accounts",
            credentials=credentials,
            environment=environment,
        )
        return parse_coinbase_balances(payload)

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        payload, _headers = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/accounts",
            credentials=credentials,
            environment=environment,
        )
        return ExchangeAccountSnapshot(account_status=parse_coinbase_account_status(payload))

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        payload, _headers = await self._request_json(
            method="GET",
            path="/api/v3/brokerage/key_permissions",
            credentials=credentials,
            environment=environment,
            swallow_404=True,
        )
        permissions = parse_coinbase_permissions(payload)
        return ExchangePermissionSnapshot(permissions=permissions, verified=len(permissions) > 0)

    async def fetch_product(self, *, credentials: dict[str, str], environment: str, product_id: str) -> ExchangeProductSnapshot:
        payload, _headers = await self._request_json(
            method="GET",
            path=f"/api/v3/brokerage/products/{product_id}",
            credentials=credentials,
            environment=environment,
            swallow_404=True,
        )

        available = False
        trading_enabled = False
        if isinstance(payload, dict):
            if payload.get("product_id") == product_id:
                available = True
            if bool(payload.get("is_disabled")) is False and bool(payload.get("trading_disabled")) is False:
                trading_enabled = True

        return ExchangeProductSnapshot(
            product_id=product_id,
            available=available,
            trading_enabled=trading_enabled,
        )

    async def preview_market_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        product_id: str,
        side: str,
        quote_size: Decimal | None,
        base_size: Decimal | None,
        client_order_id: str | None = None,
    ) -> ExchangePreviewResult:
        order_configuration: dict[str, Any]
        if quote_size is not None:
            order_configuration = {"market_market_ioc": {"quote_size": format(quote_size, "f")}}
        else:
            order_configuration = {"market_market_ioc": {"base_size": format(base_size or Decimal("0"), "f")}}

        request_payload: dict[str, Any] = {
            "product_id": product_id,
            "side": side,
            "order_configuration": order_configuration,
        }
        if client_order_id:
            request_payload["client_order_id"] = client_order_id

        payload, _headers = await self._request_json(
            method="POST",
            path="/api/v3/brokerage/orders/preview",
            credentials=credentials,
            environment=environment,
            json_payload=request_payload,
        )
        normalized = normalize_coinbase_preview_response(payload)

        return ExchangePreviewResult(
            preview_id=str(normalized.get("preview_id")) if normalized.get("preview_id") is not None else None,
            success=bool(payload.get("success", normalized.get("success", True))) if isinstance(payload, dict) else True,
            failure_reason=str(normalized.get("failure_reason")) if normalized.get("failure_reason") is not None else None,
            warning_messages=list(normalized.get("warning_messages", [])),
            estimated_average_price=_decimal_field(normalized, "estimated_average_price"),
            estimated_total_value=_decimal_field(normalized, "estimated_total_value"),
            estimated_base_size=_decimal_field(normalized, "estimated_base_size"),
            estimated_quote_size=_decimal_field(normalized, "estimated_quote_size"),
            estimated_fee=_decimal_field(normalized, "estimated_fee"),
            estimated_fee_currency=str(payload.get("estimated_fee_currency")) if payload.get("estimated_fee_currency") is not None else None,
            estimated_slippage=_decimal_field(normalized, "estimated_slippage"),
            estimated_commission_total=_decimal_field(normalized, "estimated_commission_total"),
            best_bid=_decimal_field(normalized, "best_bid"),
            best_ask=_decimal_field(normalized, "best_ask"),
            exchange_response_summary=normalized,
        )

    async def create_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        request_payload: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[dict[str, object], dict[str, str]]:
        return await self._request_json(
            method="POST",
            path="/api/v3/brokerage/orders",
            credentials=credentials,
            environment=environment,
            json_payload=request_payload,
            extra_headers={"X-Idempotency-Key": idempotency_key},
        )

    async def submit_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        request: ExchangeOrderSubmissionRequest,
    ) -> ExchangeOrderSubmissionResult:
        try:
            provider_response, safe_headers = await self.create_order(
                credentials=credentials,
                environment=environment,
                request_payload=request.raw_payload,
                idempotency_key=request.idempotency_key,
            )
        except InvalidRequestError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="provider_rejected",
                    message=str(exc),
                    retryable=False,
                    provider_status=None,
                    safe_details={"error": details, "message": str(exc)},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )
        except Exception as exc:
            return ExchangeOrderSubmissionResult(
                classification="ambiguous",
                order=None,
                rejection=None,
                ambiguous=ExchangeProviderAmbiguousResponse(
                    reason="provider_exception_before_classification",
                    safe_details={"error_type": exc.__class__.__name__, "message": str(exc)},
                ),
                raw_response={},
                safe_headers={},
            )

        order_payload = provider_response.get("order") if isinstance(provider_response.get("order"), dict) else {}
        provider_order_id = order_payload.get("order_id") if isinstance(order_payload.get("order_id"), str) else None
        provider_status = order_payload.get("status") if isinstance(order_payload.get("status"), str) else None
        success = bool(provider_response.get("success", False))

        order = ExchangeProviderOrder(
            provider_order_id=provider_order_id,
            client_order_id=order_payload.get("client_order_id") if isinstance(order_payload.get("client_order_id"), str) else request.client_order_id,
            product_id=order_payload.get("product_id") if isinstance(order_payload.get("product_id"), str) else request.product_id,
            side=order_payload.get("side") if isinstance(order_payload.get("side"), str) else request.side,
            status=provider_status,
            submitted_at=_provider_timestamp(order_payload),
            acknowledged_at=_provider_timestamp(order_payload),
            raw=order_payload,
        )

        if success and provider_order_id is not None:
            return ExchangeOrderSubmissionResult(
                classification="success",
                order=order,
                rejection=None,
                ambiguous=None,
                raw_response=provider_response,
                safe_headers=safe_headers,
            )

        return ExchangeOrderSubmissionResult(
            classification="ambiguous",
            order=order,
            rejection=None,
            ambiguous=ExchangeProviderAmbiguousResponse(
                reason="provider_response_ambiguous",
                safe_details={"success": success, "provider_order_id": provider_order_id, "provider_status": provider_status},
            ),
            raw_response=provider_response,
            safe_headers=safe_headers,
        )

    async def lookup_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str | None,
        client_order_id: str | None,
        product_id: str | None,
    ) -> ExchangeProviderOrder | None:
        payload: dict[str, object] | None = None
        if provider_order_id is not None:
            order_payload, _headers = await self.get_historical_order(
                credentials=credentials,
                environment=environment,
                order_id=provider_order_id,
                client_order_id=client_order_id,
            )
            payload = order_payload.get("order") if isinstance(order_payload.get("order"), dict) else None
        else:
            list_payload, _headers = await self.list_historical_orders(
                credentials=credentials,
                environment=environment,
                product_ids=None if product_id is None else [product_id],
                order_status=["PENDING", "OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED", "FILLED", "FAILED", "CANCELLED", "EXPIRED"],
            )
            rows = list_payload.get("orders") if isinstance(list_payload.get("orders"), list) else []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if client_order_id is not None and str(item.get("client_order_id", "")) != client_order_id:
                    continue
                if product_id is not None and str(item.get("product_id", "")) != product_id:
                    continue
                payload = item
                break

        if not isinstance(payload, dict):
            return None

        return ExchangeProviderOrder(
            provider_order_id=payload.get("order_id") if isinstance(payload.get("order_id"), str) else provider_order_id,
            client_order_id=payload.get("client_order_id") if isinstance(payload.get("client_order_id"), str) else client_order_id,
            product_id=payload.get("product_id") if isinstance(payload.get("product_id"), str) else product_id,
            side=payload.get("side") if isinstance(payload.get("side"), str) else None,
            status=payload.get("status") if isinstance(payload.get("status"), str) else None,
            submitted_at=_provider_timestamp(payload),
            acknowledged_at=_provider_timestamp(payload),
            raw=payload,
        )

    async def list_fills(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str,
    ) -> list[ExchangeProviderFill]:
        payload, _headers = await self.list_historical_fills(
            credentials=credentials,
            environment=environment,
            order_id=provider_order_id,
        )
        fills_payload = payload.get("fills") if isinstance(payload.get("fills"), list) else []
        fills: list[ExchangeProviderFill] = []
        for item in fills_payload:
            if not isinstance(item, dict):
                continue
            size = _decimal_field(item, "size") or Decimal("0")
            price = _decimal_field(item, "price") or Decimal("0")
            if size <= Decimal("0") or price <= Decimal("0"):
                continue
            fee_amount = _decimal_field(item, "commission")
            fee = None
            if fee_amount is not None:
                fee = ExchangeProviderFee(
                    amount=fee_amount,
                    currency=str(item.get("commission_currency") or "USD"),
                )
            fills.append(
                ExchangeProviderFill(
                    provider_fill_id=item.get("trade_id") if isinstance(item.get("trade_id"), str) else None,
                    provider_order_id=item.get("order_id") if isinstance(item.get("order_id"), str) else provider_order_id,
                    product_id=item.get("product_id") if isinstance(item.get("product_id"), str) else None,
                    size=size,
                    price=price,
                    fee=fee,
                    occurred_at=_provider_timestamp(item),
                    raw=item,
                )
            )
        return fills

    async def list_historical_orders(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        product_ids: list[str] | None = None,
        order_status: list[str] | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        query_params: dict[str, Any] = {}
        if product_ids:
            query_params["product_ids"] = product_ids
        if order_status:
            query_params["order_status"] = order_status
        return await self._request_json(
            method="GET",
            path="/api/v3/brokerage/orders/historical/batch",
            credentials=credentials,
            environment=environment,
            query_params=query_params or None,
        )

    async def get_historical_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        order_id: str,
        client_order_id: str | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        query_params = None if client_order_id is None else {"client_order_id": client_order_id}
        return await self._request_json(
            method="GET",
            path=f"/api/v3/brokerage/orders/historical/{order_id}",
            credentials=credentials,
            environment=environment,
            query_params=query_params,
        )

    async def list_historical_fills(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        order_id: str,
    ) -> tuple[dict[str, object], dict[str, str]]:
        return await self._request_json(
            method="GET",
            path="/api/v3/brokerage/orders/historical/fills",
            credentials=credentials,
            environment=environment,
            query_params={"order_id": order_id},
        )

    async def cancel_orders(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        order_ids: list[str],
        idempotency_key: str,
    ) -> tuple[dict[str, object], dict[str, str]]:
        payload = {"order_ids": order_ids}
        return await self._request_json(
            method="POST",
            path="/api/v3/brokerage/orders/batch_cancel",
            credentials=credentials,
            environment=environment,
            json_payload=payload,
            extra_headers={"X-Idempotency-Key": idempotency_key},
        )

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        credentials: dict[str, str],
        environment: str,
        swallow_404: bool = False,
        json_payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        if _sandbox_mock_mode_enabled():
            if environment.strip().lower() == "production":
                raise InvalidRequestError(
                    message="Sandbox mock mode is forbidden for production environment",
                    details={"environment": environment},
                )
            return self._mock_sandbox_response(path=path, method=method, json_payload=json_payload)

        base_url = self._base_url(environment)
        body = json.dumps(json_payload) if json_payload is not None else ""
        request_host = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        token = build_coinbase_jwt(
            api_key_name=credentials["api_key"],
            private_key=credentials["api_secret"],
            request_method=method,
            request_host=request_host,
            request_path=path,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        if json_payload is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.request(method, path, content=body, headers=headers, params=query_params)
        except httpx.HTTPError as exc:
            self._last_error_classification = "network_error"
            self._last_error_message = str(exc)
            raise ServiceUnavailableError(message="Coinbase API is unreachable", details={"provider": self.provider}) from exc

        if swallow_404 and response.status_code == 404:
            return {}, dict(response.headers)

        if response.status_code >= 400:
            self._last_error_classification = "http_error"
            self._last_error_message = f"status={response.status_code} path={path}"
            response_payload: dict[str, object] | None = None
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    response_payload = parsed
            except ValueError:
                response_payload = None
            raise InvalidRequestError(
                message="Coinbase API request failed",
                details={
                    "status_code": response.status_code,
                    "path": path,
                    "response": response_payload,
                    "response_text": response.text[:500],
                },
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidRequestError(message="Coinbase API returned invalid JSON", details={"path": path}) from exc

        if not isinstance(payload, dict):
            self._last_error_classification = "protocol_error"
            self._last_error_message = "unexpected_payload"
            raise InvalidRequestError(message="Coinbase API returned unexpected payload", details={"path": path})

        self._last_successful_call_at = datetime.now(timezone.utc)
        self._last_error_classification = None
        self._last_error_message = None

        return payload, dict(response.headers)

    def _base_url(self, environment: str) -> str:
        normalized = environment.strip().lower()
        if normalized == "sandbox":
            return "https://api-public.sandbox.exchange.coinbase.com"
        return "https://api.coinbase.com"

    def _mock_sandbox_response(
        self,
        *,
        path: str,
        method: str,
        json_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        now = datetime.now(timezone.utc)
        headers = {"Date": email.utils.format_datetime(now)}
        method_u = method.upper()

        if method_u == "GET" and path == "/api/v3/brokerage/accounts":
            return (
                {
                    "accounts": [
                        {
                            "available_balance": {"currency": "USD", "value": "100.00"},
                            "hold": {"value": "0.00"},
                            "status": "active",
                        },
                        {
                            "available_balance": {"currency": "BTC", "value": "0.002"},
                            "hold": {"value": "0.000"},
                            "status": "active",
                        },
                    ]
                },
                headers,
            )
        if method_u == "GET" and path == "/api/v3/brokerage/key_permissions":
            return ({"permissions": ["view", "trade"]}, headers)
        if method_u == "GET" and path == "/api/v3/brokerage/products/BTC-USD":
            return (
                {
                    "product_id": "BTC-USD",
                    "is_disabled": False,
                    "trading_disabled": False,
                },
                headers,
            )
        if method_u == "POST" and path == "/api/v3/brokerage/orders/preview":
            side = "BUY"
            if json_payload and "side" in json_payload:
                side = str(json_payload.get("side"))
            return (
                {
                    "success": True,
                    "preview_id": f"sandbox-preview-{int(now.timestamp())}",
                    "product_id": "BTC-USD",
                    "side": side,
                    "estimated_average_price": "50000",
                    "estimated_quote_size": "5",
                    "estimated_base_size": "0.0001",
                    "estimated_fee": "0.01",
                    "best_bid": "49999",
                    "best_ask": "50001",
                    "warning_messages": [],
                },
                headers,
            )
        if method_u == "GET" and path == "/api/v3/brokerage/orders/historical/batch":
            return (
                {
                    "orders": [
                        {
                            "order_id": "sandbox-mock-order-1",
                            "client_order_id": "sandbox-mock-client-order-1",
                            "product_id": "BTC-USD",
                            "status": "FILLED",
                        }
                    ]
                },
                headers,
            )
        if method_u == "GET" and path == "/api/v3/brokerage/orders/historical/fills":
            return (
                {
                    "fills": [
                        {
                            "order_id": "sandbox-mock-order-1",
                            "trade_id": "sandbox-mock-fill-1",
                            "product_id": "BTC-USD",
                            "size": "0.0001",
                            "price": "50000",
                        }
                    ]
                },
                headers,
            )
        if method_u == "GET" and path.startswith("/api/v3/brokerage/orders/historical/"):
            order_id = path.rsplit("/", 1)[-1]
            return (
                {
                    "order": {
                        "order_id": order_id,
                        "client_order_id": "sandbox-mock-client-order-1",
                        "product_id": "BTC-USD",
                        "status": "FILLED",
                    }
                },
                headers,
            )

        raise InvalidRequestError(
            message="Sandbox mock mode does not implement requested Coinbase endpoint",
            details={"method": method_u, "path": path},
        )

    def _clock_skew_seconds(self, response_headers: dict[str, str]) -> int | None:
        raw_date = response_headers.get("Date") or response_headers.get("date")
        if raw_date is None:
            return None
        try:
            parsed = email.utils.parsedate_to_datetime(raw_date)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return int(abs((now - parsed.astimezone(timezone.utc)).total_seconds()))
        except Exception:
            return None
