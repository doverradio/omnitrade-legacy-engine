from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import os
import urllib.parse
from typing import Any

import httpx

from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.services.exchange_connections.providers.base import (
    ExchangeAccountSnapshot,
    ExchangeAuthResult,
    ExchangeBalanceItem,
    ExchangeBalanceSnapshot,
    ExchangeOrderSubmissionRequest,
    ExchangeOrderSubmissionResult,
    ExchangePermissionSnapshot,
    ExchangePreviewResult,
    ExchangeProductSnapshot,
    ExchangeProviderAmbiguousResponse,
    ExchangeProviderHealth,
    ExchangeProviderMetadata,
    ExchangeProviderOrder,
    ExchangeProviderRejection,
    ExchangeProviderFill,
    ProviderCapability,
)


def _kraken_mock_mode_enabled() -> bool:
    return str(os.getenv("OT_KRAKEN_SANDBOX_MOCK_MODE", "false")).strip().lower() in {"1", "true", "yes", "on"}


def _to_decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quantize(value: Decimal, places: int) -> Decimal:
    quantum = Decimal("1") if places <= 0 else Decimal("1").scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _parse_kraken_timestamp(payload: dict[str, Any]) -> datetime | None:
    candidates = [
        payload.get("opentm"),
        payload.get("closetm"),
        payload.get("time"),
        payload.get("timestamp"),
    ]
    for raw in candidates:
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except Exception:
            continue
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    return None


def _strip_kraken_asset_suffix(asset: str) -> str:
    if "." in asset:
        return asset.split(".", 1)[0]
    return asset


def _canonical_asset(asset: str) -> str:
    base = _strip_kraken_asset_suffix(asset.strip().upper())
    mapping = {
        "ZUSD": "USD",
        "USD": "USD",
        "XXBT": "BTC",
        "XBT": "BTC",
        "BTC": "BTC",
        "XETH": "ETH",
        "ETH": "ETH",
    }
    return mapping.get(base, base)


def _looks_like_permission_denied(exc: Exception) -> bool:
    if not isinstance(exc, InvalidRequestError):
        return False
    details = getattr(exc, "details", {}) or {}
    errors = details.get("errors") if isinstance(details, dict) else None
    if not isinstance(errors, list):
        return False
    lowered = " ".join(str(item).lower() for item in errors)
    return "permission" in lowered or "denied" in lowered


def _normalize_intent_product(product_id: str) -> tuple[str, str]:
    normalized = product_id.strip().upper()
    if normalized in {"BTC-USD", "XBT-USD", "BTC/USD", "XBT/USD", "XBTUSD"}:
        return "BTC-USD", "XBT/USD"
    if normalized == "ETH-USD":
        return "ETH-USD", "ETH/USD"
    raise InvalidRequestError(
        message="Unsupported Kraken product mapping",
        details={"product_id": product_id},
    )


# Kraken Spot REST auth contract:
# docs.kraken.com/api/docs/guides/spot-rest-auth/
# API-Sign = base64(HMAC-SHA512(url_path + SHA256(nonce + postdata), base64_decode(secret)))
def build_kraken_signature(*, url_path: str, payload: dict[str, str], secret_b64: str) -> str:
    postdata = urllib.parse.urlencode(payload)
    encoded = (str(payload["nonce"]) + postdata).encode("utf-8")
    message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("utf-8")


class KrakenSpotClient:
    provider = "kraken_spot"
    _metadata = ExchangeProviderMetadata(
        provider_key="kraken_spot",
        display_name="Kraken Spot",
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
        return _kraken_mock_mode_enabled()

    async def current_health(self, *, environment: str) -> ExchangeProviderHealth:
        capability_status = {capability: "supported" for capability in self._metadata.capabilities}
        if environment not in self._metadata.supported_environments:
            capability_status["environment"] = "unsupported"
        if environment == "sandbox" and not _kraken_mock_mode_enabled():
            capability_status["sandbox"] = "mock_required"
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
            time_payload = await self._public_request(path="/public/Time", environment=environment, params=None)
            _ = await self._private_request(path="/private/Balance", environment=environment, credentials=credentials, payload={})
            permission_snapshot = await self.fetch_permissions(credentials=credentials, environment=environment)
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

        server_unix = None
        if isinstance(time_payload.get("result"), dict):
            server_unix = time_payload["result"].get("unixtime")
        clock_skew = None
        if server_unix is not None:
            try:
                server_dt = datetime.fromtimestamp(int(server_unix), tz=timezone.utc)
                clock_skew = int(abs((heartbeat_at - server_dt).total_seconds()))
            except Exception:
                clock_skew = None

        return ExchangeAuthResult(
            reachable=True,
            authenticated=True,
            account_status="active",
            permissions=permission_snapshot.permissions,
            heartbeat_at=heartbeat_at,
            clock_skew_seconds=clock_skew,
            withdrawals_permission_granted=False,
            trade_permission_present=(
                "open_order_query" in permission_snapshot.permissions
                or "closed_order_query" in permission_snapshot.permissions
            ),
            error=None,
        )

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        payload = await self._private_request(path="/private/Balance", environment=environment, credentials=credentials, payload={})
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        by_currency: dict[str, ExchangeBalanceItem] = {
            "USD": ExchangeBalanceItem(currency="USD", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
            "BTC": ExchangeBalanceItem(currency="BTC", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
            "ETH": ExchangeBalanceItem(currency="ETH", available=Decimal("0"), reserved=Decimal("0"), total=Decimal("0")),
        }

        if isinstance(result, dict):
            for asset, raw_amount in result.items():
                canonical = _canonical_asset(str(asset))
                if canonical not in by_currency:
                    continue
                available = _to_decimal(str(raw_amount))
                prior = by_currency[canonical]
                by_currency[canonical] = ExchangeBalanceItem(
                    currency=canonical,
                    available=prior.available + available,
                    reserved=prior.reserved,
                    total=prior.total + available,
                )

        balances = [by_currency["USD"], by_currency["BTC"], by_currency["ETH"]]
        return ExchangeBalanceSnapshot(balances=balances, total_equity_usd=by_currency["USD"].total)

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        _ = await self._private_request(path="/private/Balance", environment=environment, credentials=credentials, payload={})
        return ExchangeAccountSnapshot(account_status="active")

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        permissions = ["funds_query"]
        _ = await self._private_request(path="/private/Balance", environment=environment, credentials=credentials, payload={})

        probes = (
            ("/private/OpenOrders", "open_order_query"),
            ("/private/ClosedOrders", "closed_order_query"),
            ("/private/Ledgers", "ledger_query"),
        )
        for path, permission_name in probes:
            try:
                await self._private_request(path=path, environment=environment, credentials=credentials, payload={})
            except Exception as exc:
                if _looks_like_permission_denied(exc):
                    continue
                raise
            permissions.append(permission_name)

        return ExchangePermissionSnapshot(permissions=permissions, verified=True)

    async def fetch_product(self, *, credentials: dict[str, str], environment: str, product_id: str) -> ExchangeProductSnapshot:
        _ = credentials
        normalized_product, target_pair = _normalize_intent_product(product_id)
        pair_info = await self._load_pair_info(environment=environment, normalized_pair=target_pair)
        if pair_info is None:
            return ExchangeProductSnapshot(product_id=normalized_product, available=False, trading_enabled=False)

        status = str(pair_info.get("status") or "").lower()
        available = status != ""
        trading_enabled = status in {"online", "post_only", "limit_only", "reduce_only"}
        return ExchangeProductSnapshot(
            product_id=normalized_product,
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
        _ = credentials
        _ = client_order_id
        if side.upper() != "BUY":
            return ExchangePreviewResult(
                preview_id=None,
                success=False,
                failure_reason="unsupported_side",
                warning_messages=[],
                estimated_average_price=None,
                estimated_total_value=None,
                estimated_base_size=None,
                estimated_quote_size=quote_size,
                estimated_fee=None,
                estimated_fee_currency=None,
                estimated_slippage=None,
                estimated_commission_total=None,
                best_bid=None,
                best_ask=None,
                exchange_response_summary={"reason": "only_buy_supported"},
            )
        if quote_size is None and base_size is None:
            raise InvalidRequestError(
                message="Kraken preview requires quote_size or base_size",
                details={"product_id": product_id},
            )

        normalized_product, target_pair = _normalize_intent_product(product_id)
        pair_info = await self._load_pair_info(environment=environment, normalized_pair=target_pair)
        if pair_info is None:
            return ExchangePreviewResult(
                preview_id=None,
                success=False,
                failure_reason="product_unavailable",
                warning_messages=[],
                estimated_average_price=None,
                estimated_total_value=None,
                estimated_base_size=None,
                estimated_quote_size=quote_size,
                estimated_fee=None,
                estimated_fee_currency="USD",
                estimated_slippage=None,
                estimated_commission_total=None,
                best_bid=None,
                best_ask=None,
                exchange_response_summary={"product_id": normalized_product},
            )

        altname = str(pair_info.get("altname") or "XBTUSD")
        ticker_payload = await self._public_request(path="/public/Ticker", environment=environment, params={"pair": altname})
        ticker_result = ticker_payload.get("result") if isinstance(ticker_payload.get("result"), dict) else {}
        ticker_row = None
        if isinstance(ticker_result, dict) and ticker_result:
            ticker_row = next(iter(ticker_result.values()))
        if not isinstance(ticker_row, dict):
            raise InvalidRequestError(
                message="Kraken ticker payload unavailable",
                details={"pair": altname},
            )

        ask_arr = ticker_row.get("a") if isinstance(ticker_row.get("a"), list) else []
        bid_arr = ticker_row.get("b") if isinstance(ticker_row.get("b"), list) else []
        best_ask = _to_decimal(ask_arr[0] if len(ask_arr) > 0 else None)
        best_bid = _to_decimal(bid_arr[0] if len(bid_arr) > 0 else None)
        if best_ask <= Decimal("0"):
            raise InvalidRequestError(message="Kraken best ask unavailable", details={"pair": altname})

        pair_decimals = int(pair_info.get("pair_decimals") or 1)
        lot_decimals = int(pair_info.get("lot_decimals") or 8)
        ordermin = _to_decimal(pair_info.get("ordermin"))
        costmin = _to_decimal(pair_info.get("costmin"))

        if quote_size is not None:
            estimated_quote_size = quote_size
            estimated_base_size = _quantize(quote_size / best_ask, lot_decimals)
        else:
            estimated_base_size = _quantize(base_size or Decimal("0"), lot_decimals)
            estimated_quote_size = _quantize(estimated_base_size * best_ask, pair_decimals)

        if ordermin > Decimal("0") and estimated_base_size < ordermin:
            return ExchangePreviewResult(
                preview_id=None,
                success=False,
                failure_reason="below_min_order_size",
                warning_messages=[],
                estimated_average_price=best_ask,
                estimated_total_value=estimated_quote_size,
                estimated_base_size=estimated_base_size,
                estimated_quote_size=estimated_quote_size,
                estimated_fee=None,
                estimated_fee_currency="USD",
                estimated_slippage=None,
                estimated_commission_total=None,
                best_bid=best_bid,
                best_ask=best_ask,
                exchange_response_summary={
                    "ordermin": format(ordermin, "f"),
                    "lot_decimals": lot_decimals,
                    "pair_decimals": pair_decimals,
                    "costmin": format(costmin, "f"),
                    "pair": altname,
                },
            )

        if costmin > Decimal("0") and estimated_quote_size < costmin:
            return ExchangePreviewResult(
                preview_id=None,
                success=False,
                failure_reason="below_min_order_cost",
                warning_messages=[],
                estimated_average_price=best_ask,
                estimated_total_value=estimated_quote_size,
                estimated_base_size=estimated_base_size,
                estimated_quote_size=estimated_quote_size,
                estimated_fee=None,
                estimated_fee_currency="USD",
                estimated_slippage=None,
                estimated_commission_total=None,
                best_bid=best_bid,
                best_ask=best_ask,
                exchange_response_summary={
                    "ordermin": format(ordermin, "f"),
                    "lot_decimals": lot_decimals,
                    "pair_decimals": pair_decimals,
                    "costmin": format(costmin, "f"),
                    "pair": altname,
                },
            )

        return ExchangePreviewResult(
            preview_id=None,
            success=True,
            failure_reason=None,
            warning_messages=[],
            estimated_average_price=best_ask,
            estimated_total_value=estimated_quote_size,
            estimated_base_size=estimated_base_size,
            estimated_quote_size=estimated_quote_size,
            estimated_fee=None,
            estimated_fee_currency="USD",
            estimated_slippage=None if best_bid <= Decimal("0") else abs(best_ask - best_bid) / best_ask,
            estimated_commission_total=None,
            best_bid=best_bid,
            best_ask=best_ask,
            exchange_response_summary={
                "pair": altname,
                "ordermin": format(ordermin, "f"),
                "costmin": format(costmin, "f"),
                "pair_decimals": pair_decimals,
                "lot_decimals": lot_decimals,
                "source": "kraken_public_assetpairs_ticker",
            },
        )

    async def submit_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        request: ExchangeOrderSubmissionRequest,
    ) -> ExchangeOrderSubmissionResult:
        _ = credentials
        _ = environment
        _ = request
        return ExchangeOrderSubmissionResult(
            classification="rejected",
            order=None,
            rejection=ExchangeProviderRejection(
                code="unsupported_capability",
                message="Kraken order submission is not enabled in EP-2",
                retryable=False,
                provider_status=None,
                safe_details={"provider": self.provider, "capability": "create_order"},
            ),
            ambiguous=None,
            raw_response={},
            safe_headers={},
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
        _ = credentials
        _ = environment
        _ = provider_order_id
        _ = client_order_id
        _ = product_id
        return None

    async def list_fills(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str,
    ) -> list[ExchangeProviderFill]:
        _ = credentials
        _ = environment
        _ = provider_order_id
        return []

    async def _load_pair_info(self, *, environment: str, normalized_pair: str) -> dict[str, Any] | None:
        payload = await self._public_request(path="/public/AssetPairs", environment=environment, params={"assetVersion": "1"})
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if not isinstance(result, dict):
            return None

        normalized_targets = {normalized_pair.upper(), normalized_pair.upper().replace("-", "/")}
        for _pair_key, row in result.items():
            if not isinstance(row, dict):
                continue
            candidates = {
                str(row.get("altname") or "").upper(),
                str(row.get("wsname") or "").upper(),
            }
            if isinstance(row.get("base"), str) and isinstance(row.get("quote"), str):
                candidates.add(f"{str(row.get('base')).upper()}/{str(row.get('quote')).upper()}")
                candidates.add(f"{str(row.get('base')).upper()}-{str(row.get('quote')).upper()}")
            if candidates.intersection(normalized_targets):
                return row
        return None

    async def _public_request(self, *, path: str, environment: str, params: dict[str, str] | None) -> dict[str, Any]:
        if environment == "sandbox":
            if not _kraken_mock_mode_enabled():
                raise InvalidRequestError(
                    message="Kraken sandbox requests require controlled mock mode",
                    details={"environment": environment},
                )
            return self._mock_sandbox_response(path=path, method="GET", payload=params)

        base_url = "https://api.kraken.com/0"
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            self._last_error_classification = "network_error"
            self._last_error_message = str(exc)
            raise ServiceUnavailableError(message="Kraken API is unreachable", details={"provider": self.provider}) from exc

        if response.status_code >= 400:
            self._last_error_classification = "http_error"
            self._last_error_message = f"status={response.status_code} path={path}"
            raise InvalidRequestError(
                message="Kraken API request failed",
                details={"status_code": response.status_code, "path": path, "response_text": response.text[:500]},
            )

        payload = self._parse_json_response(response=response, path=path)
        self._last_successful_call_at = datetime.now(timezone.utc)
        self._last_error_classification = None
        self._last_error_message = None
        return payload

    async def _private_request(
        self,
        *,
        path: str,
        environment: str,
        credentials: dict[str, str],
        payload: dict[str, str],
    ) -> dict[str, Any]:
        if environment == "sandbox":
            if not _kraken_mock_mode_enabled():
                raise InvalidRequestError(
                    message="Kraken sandbox requests require controlled mock mode",
                    details={"environment": environment},
                )
            return self._mock_sandbox_response(path=path, method="POST", payload=payload)

        base_url = "https://api.kraken.com/0"
        nonce = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        body_payload = {"nonce": nonce, **payload}
        otp = str(credentials.get("passphrase") or "").strip()
        if otp:
            body_payload["otp"] = otp

        signature = build_kraken_signature(
            url_path=f"/0{path}",
            payload=body_payload,
            secret_b64=credentials["api_secret"],
        )
        headers = {
            "API-Key": credentials["api_key"],
            "API-Sign": signature,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.post(path, content=urllib.parse.urlencode(body_payload), headers=headers)
        except httpx.HTTPError as exc:
            self._last_error_classification = "network_error"
            self._last_error_message = str(exc)
            raise ServiceUnavailableError(message="Kraken API is unreachable", details={"provider": self.provider}) from exc

        if response.status_code >= 400:
            self._last_error_classification = "http_error"
            self._last_error_message = f"status={response.status_code} path={path}"
            raise InvalidRequestError(
                message="Kraken API request failed",
                details={"status_code": response.status_code, "path": path, "response_text": response.text[:500]},
            )

        parsed = self._parse_json_response(response=response, path=path)
        errors = parsed.get("error") if isinstance(parsed.get("error"), list) else []
        if errors:
            self._last_error_classification = "provider_error"
            self._last_error_message = str(errors[:1])
            raise InvalidRequestError(
                message="Kraken API returned errors",
                details={"path": path, "errors": [str(item) for item in errors[:5]]},
            )

        self._last_successful_call_at = datetime.now(timezone.utc)
        self._last_error_classification = None
        self._last_error_message = None
        return parsed

    def _parse_json_response(self, *, response: httpx.Response, path: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidRequestError(message="Kraken API returned invalid JSON", details={"path": path}) from exc
        if not isinstance(payload, dict):
            raise InvalidRequestError(message="Kraken API returned unexpected payload", details={"path": path})
        return payload

    def _mock_sandbox_response(self, *, path: str, method: str, payload: dict[str, str] | None) -> dict[str, Any]:
        _ = method
        _ = payload
        if path == "/public/Time":
            return {"error": [], "result": {"unixtime": int(datetime.now(timezone.utc).timestamp())}}
        if path == "/private/Balance":
            return {"error": [], "result": {"ZUSD": "100.00", "XXBT": "0.001"}}
        if path == "/private/OpenOrders":
            return {"error": [], "result": {"open": {}}}
        if path == "/private/ClosedOrders":
            return {"error": [], "result": {"closed": {}, "count": 0}}
        if path == "/private/Ledgers":
            return {"error": [], "result": {"ledger": {}, "count": 0}}
        if path == "/public/AssetPairs":
            return {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "altname": "XBTUSD",
                        "wsname": "XBT/USD",
                        "base": "BTC",
                        "quote": "USD",
                        "pair_decimals": 1,
                        "lot_decimals": 8,
                        "ordermin": "0.0001",
                        "costmin": "0.5",
                        "status": "online",
                    }
                },
            }
        if path == "/public/Ticker":
            return {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "a": ["50001.0", "1", "1.000"],
                        "b": ["49999.0", "1", "1.000"],
                    }
                },
            }
        raise InvalidRequestError(
            message="Kraken sandbox mock does not implement requested endpoint",
            details={"path": path},
        )
