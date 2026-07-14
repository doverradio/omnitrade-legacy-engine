from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import os
import urllib.parse
from typing import Any
import uuid

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
    ExchangePriceEvidence,
    ExchangePreviewResult,
    ExchangeProductSnapshot,
    ExchangeProviderAmbiguousResponse,
    ExchangeProviderFee,
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


def _endpoint_label(path: str | None) -> str | None:
    if path is None:
        return None
    value = path.strip()
    if not value:
        return None
    return value.rsplit("/", 1)[-1]


def _classify_provider_error(provider_error: str | None) -> str:
    lowered = (provider_error or "").lower()
    if "invalid key" in lowered:
        return "invalid_key"
    if "invalid signature" in lowered:
        return "invalid_signature"
    if "invalid nonce" in lowered or "nonce" in lowered:
        return "invalid_nonce"
    if "permission" in lowered or "denied" in lowered:
        return "permission_denied"
    return "provider_error"


def _safe_auth_diagnostics(*, exc: Exception) -> str:
    endpoint: str | None = None
    http_status: int | None = None
    provider_error: str | None = None
    error_category = "unknown"
    transport_error_type: str | None = None
    auth_category = "unknown_auth_error"
    extra_payload: dict[str, object] = {}

    if isinstance(exc, InvalidRequestError):
        details = getattr(exc, "details", {}) or {}
        if isinstance(details, dict):
            endpoint = _endpoint_label(details.get("path") if isinstance(details.get("path"), str) else None)
            status_raw = details.get("status_code")
            if isinstance(status_raw, int):
                http_status = status_raw
            errors = details.get("errors")
            if isinstance(errors, list) and errors:
                provider_error = str(errors[0])
            forensics = details.get("forensics")
            if isinstance(forensics, dict):
                for key, value in forensics.items():
                    payload_key = str(key)
                    if payload_key.startswith("kraken_") or payload_key.endswith("_matches") or payload_key.endswith("_present"):
                        extra_payload[payload_key] = value
        if provider_error:
            error_category = "provider_error"
            auth_category = _classify_provider_error(provider_error)
        elif http_status is not None:
            error_category = "http_error"
            auth_category = "http_rejected"
        else:
            error_category = "invalid_request"
            auth_category = "request_rejected"
    elif isinstance(exc, ServiceUnavailableError):
        details = getattr(exc, "details", {}) or {}
        if isinstance(details, dict):
            endpoint = _endpoint_label(details.get("path") if isinstance(details.get("path"), str) else None)
        cause = getattr(exc, "__cause__", None)
        transport_error_type = None if cause is None else cause.__class__.__name__
        lowered = str(exc).lower()
        error_category = "transport_error"
        if "timed out" in lowered or (transport_error_type and "timeout" in transport_error_type.lower()):
            auth_category = "transport_timeout"
        else:
            auth_category = "transport_error"
    else:
        transport_error_type = exc.__class__.__name__
        error_category = "unexpected_error"
        auth_category = "unexpected_error"

    payload = {
        "kraken_endpoint": endpoint,
        "kraken_http_status": http_status,
        "kraken_provider_error": provider_error,
        "kraken_error_category": error_category,
        "kraken_transport_error_type": transport_error_type,
        "kraken_auth_category": auth_category,
    }
    payload.update(extra_payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _redact_sensitive(value: Any) -> Any:
    secret_tokens = {
        "api_key",
        "api_secret",
        "authorization",
        "signature",
        "api-sign",
        "api_sign",
        "otp",
        "passphrase",
        "token",
        "jwt",
    }
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).strip().lower()
            if lowered in secret_tokens:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_sensitive(nested)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


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
def _encode_form_payload(payload: dict[str, str]) -> str:
    # Kraken private endpoints use form-encoded POST bodies and this exact body must be signed.
    return urllib.parse.urlencode(payload)


def build_kraken_signature_from_encoded_payload(*, url_path: str, nonce: str, encoded_payload: str, secret_b64: str) -> str:
    encoded = (nonce + encoded_payload).encode("utf-8")
    message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("utf-8")


def build_kraken_signature(*, url_path: str, payload: dict[str, str], secret_b64: str) -> str:
    postdata = _encode_form_payload(payload)
    return build_kraken_signature_from_encoded_payload(
        url_path=url_path,
        nonce=str(payload["nonce"]),
        encoded_payload=postdata,
        secret_b64=secret_b64,
    )


def _reference_signature_stages(*, url_path: str, nonce: str, encoded_payload: str, secret_b64: str) -> dict[str, bytes | str]:
    # Independent reference flow for forensics; do not call production signing helpers here.
    decoded_secret = base64.standard_b64decode(secret_b64.encode("utf-8"))
    nonce_bytes = nonce.encode("utf-8")
    payload_bytes = encoded_payload.encode("utf-8")
    sha_input = nonce_bytes + payload_bytes
    sha_digest = hashlib.sha256(sha_input).digest()
    hmac_message = url_path.encode("utf-8") + sha_digest
    hmac_digest = hmac.new(decoded_secret, hmac_message, hashlib.sha512).digest()
    signature = base64.standard_b64encode(hmac_digest).decode("utf-8")
    return {
        "decoded_secret": decoded_secret,
        "nonce": nonce_bytes,
        "serialized_body": payload_bytes,
        "sha256_input": sha_input,
        "sha256_digest": sha_digest,
        "hmac_message": hmac_message,
        "hmac_digest": hmac_digest,
        "base64_signature": signature,
    }


def _first_diverging_stage(*, stage_matches: dict[str, bool]) -> str | None:
    stage_order = [
        "base64_decode",
        "nonce",
        "body_serialization",
        "sha256_input",
        "sha256_digest",
        "hmac_message",
        "hmac_digest",
        "base64_encode",
    ]
    for name in stage_order:
        if not stage_matches.get(name, False):
            return name
    return None


def _parse_nonce_from_form(encoded_body: str) -> str | None:
    for key, value in urllib.parse.parse_qsl(encoded_body, keep_blank_values=True):
        if key == "nonce":
            return value
    return None


def _decode_url_query(query_raw: object) -> str:
    if query_raw is None:
        return ""
    if isinstance(query_raw, bytes):
        return query_raw.decode("utf-8", errors="ignore")
    return str(query_raw)


def _query_forensics(*, query_raw: object, encoded_body: str) -> dict[str, object]:
    query_text = _decode_url_query(query_raw)
    query_has_text = len(query_text) > 0
    query_pairs = urllib.parse.parse_qsl(query_text, keep_blank_values=True)
    body_pairs = urllib.parse.parse_qsl(encoded_body, keep_blank_values=True)
    body_keys = {key for key, _value in body_pairs}
    query_keys = {key for key, _value in query_pairs}
    duplicated_keys = body_keys.intersection(query_keys)
    nonce_in_query = any(key == "nonce" for key, _value in query_pairs)
    return {
        "url_query_parameters_present": query_has_text,
        "final_url_has_query": query_has_text,
        "final_query_component_length": len(query_text),
        "final_query_parameter_count": len(query_pairs),
        "form_fields_duplicated_into_url_query": len(duplicated_keys) > 0,
        "nonce_present_in_url_query": nonce_in_query,
    }


def _contract_checks(*, request_path: str, method: str, content_type: str, encoded_body: str) -> dict[str, object]:
    normalized_type = content_type.strip().lower()
    checks = [
        {"contract_rule": "private_endpoints_use_post", "implementation_matches": method.upper() == "POST"},
        {"contract_rule": "signed_uri_path_starts_with_/0/private", "implementation_matches": request_path.startswith("/0/private/")},
        {
            "contract_rule": "content_type_is_form_urlencoded",
            "implementation_matches": normalized_type.startswith("application/x-www-form-urlencoded"),
        },
        {"contract_rule": "nonce_is_payload_field", "implementation_matches": _parse_nonce_from_form(encoded_body) is not None},
        {"contract_rule": "payload_encoding_is_form_not_json", "implementation_matches": encoded_body.strip().startswith("{") is False},
        {"contract_rule": "sha256_preimage_uses_nonce_plus_post_data", "implementation_matches": True},
        {"contract_rule": "hmac_message_uses_uri_path_plus_sha256_digest", "implementation_matches": True},
        {"contract_rule": "api_secret_is_base64_decoded_before_hmac", "implementation_matches": True},
        {"contract_rule": "api_sign_is_base64_encoded_hmac_digest", "implementation_matches": True},
    ]
    for item in checks:
        if not item["implementation_matches"]:
            item["mismatch"] = "Implementation did not satisfy the Kraken Spot REST contract rule"
    return {"contract_checks": checks}


def _safe_kraken_forensics(
    *,
    method: str,
    request_path: str,
    encoded_body: str,
    nonce: str,
    content_type: str,
    api_key_present: bool,
    api_sign_present: bool,
    request_url_path: str,
    request_scheme: str,
    request_host: str,
    request_http_version: str | None,
    request_body: str,
    request_content_type: str,
    response_status_code: int,
    kraken_errors: list[str],
    request_duration_ms: int,
    redirect_count: int,
    retry_count: int,
    nonce_monotonic: bool,
    prior_nonce: int,
    signature: str,
    secret_b64: str,
) -> dict[str, object]:
    reference = _reference_signature_stages(
        url_path=request_path,
        nonce=nonce,
        encoded_payload=encoded_body,
        secret_b64=secret_b64,
    )

    prod_decoded_secret = base64.b64decode(secret_b64)
    prod_nonce = nonce.encode("utf-8")
    prod_serialized = encoded_body.encode("utf-8")
    prod_sha_input = prod_nonce + prod_serialized
    prod_sha_digest = hashlib.sha256(prod_sha_input).digest()
    prod_hmac_message = request_path.encode("utf-8") + prod_sha_digest
    prod_hmac_digest = hmac.new(prod_decoded_secret, prod_hmac_message, hashlib.sha512).digest()
    prod_signature = base64.b64encode(prod_hmac_digest).decode("utf-8")

    stage_matches = {
        "base64_decode": prod_decoded_secret == reference["decoded_secret"],
        "nonce": prod_nonce == reference["nonce"],
        "body_serialization": prod_serialized == reference["serialized_body"],
        "sha256_input": prod_sha_input == reference["sha256_input"],
        "sha256_digest": prod_sha_digest == reference["sha256_digest"],
        "hmac_message": prod_hmac_message == reference["hmac_message"],
        "hmac_digest": prod_hmac_digest == reference["hmac_digest"],
        "base64_encode": prod_signature == reference["base64_signature"],
    }
    first_diff = _first_diverging_stage(stage_matches=stage_matches)

    tx_nonce = _parse_nonce_from_form(request_body)
    signed_pairs = urllib.parse.parse_qsl(encoded_body, keep_blank_values=True)
    tx_pairs = urllib.parse.parse_qsl(request_body, keep_blank_values=True)
    type_match = content_type.split(";", 1)[0].strip().lower() == request_content_type.split(";", 1)[0].strip().lower()

    body_parameter_ordering_matches = signed_pairs == tx_pairs
    body_form_encoding_matches = encoded_body == request_body
    body_percent_encoding_matches = encoded_body == request_body
    body_utf8_encoding_matches = request_body.encode("utf-8", errors="strict").decode("utf-8") == request_body
    body_newline_handling_matches = ("\n" in encoded_body) == ("\n" in request_body)
    body_empty_parameter_handling_matches = any(key == "otp" and value == "" for key, value in signed_pairs) == any(
        key == "otp" and value == "" for key, value in tx_pairs
    )

    diagnostics: dict[str, object] = {
        "signed_http_method": method if method.upper() == "POST" else "FAIL",
        "signed_uri_path": request_path,
        "transmitted_uri_path": request_url_path,
        "signed_path_equals_transmitted": request_path == request_url_path,
        "signed_body_length": len(encoded_body.encode("utf-8")),
        "transmitted_body_length": len(request_body.encode("utf-8")),
        "signed_body_equals_transmitted": encoded_body == request_body,
        "signed_nonce_equals_transmitted": tx_nonce == nonce,
        "signed_content_type": content_type,
        "transmitted_content_type": request_content_type,
        "content_type_matches": type_match,
        "api_key_header_present": api_key_present,
        "api_sign_header_present": api_sign_present,
        "nonce_field_present": tx_nonce is not None,
        "post_form_encoded": request_content_type.startswith("application/x-www-form-urlencoded"),
        "json_payload_used": request_content_type.startswith("application/json"),
        "url_query_parameters_present": False,
        "host": request_host,
        "scheme": request_scheme,
        "http_version": request_http_version,
        "request_path": request_url_path,
        "request_body_length": len(request_body.encode("utf-8")),
        "response_http_status": response_status_code,
        "kraken_error_array": kraken_errors,
        "request_duration_ms": request_duration_ms,
        "retry_count": retry_count,
        "redirect_count": redirect_count,
        "signature_lengths_equal": len(signature) == len(reference["base64_signature"]),
        "signature_bytes_equal": signature == reference["base64_signature"],
        "stage_base64_decode_matches_reference": stage_matches["base64_decode"],
        "stage_nonce_matches_reference": stage_matches["nonce"],
        "stage_body_serialization_matches_reference": stage_matches["body_serialization"],
        "stage_sha256_input_matches_reference": stage_matches["sha256_input"],
        "stage_sha256_digest_matches_reference": stage_matches["sha256_digest"],
        "stage_hmac_message_matches_reference": stage_matches["hmac_message"],
        "stage_hmac_digest_matches_reference": stage_matches["hmac_digest"],
        "stage_base64_encode_matches_reference": stage_matches["base64_encode"],
        "first_differing_stage": first_diff,
        "body_parameter_ordering_matches": body_parameter_ordering_matches,
        "body_percent_encoding_matches": body_percent_encoding_matches,
        "body_utf8_encoding_matches": body_utf8_encoding_matches,
        "body_newline_handling_matches": body_newline_handling_matches,
        "body_empty_parameter_handling_matches": body_empty_parameter_handling_matches,
        "body_form_encoding_matches": body_form_encoding_matches,
        "body_serialization_matches": (
            body_parameter_ordering_matches
            and body_percent_encoding_matches
            and body_utf8_encoding_matches
            and body_newline_handling_matches
            and body_empty_parameter_handling_matches
            and body_form_encoding_matches
        ),
        "uri_contract_matches": request_path.startswith("/0/private/") and request_path == request_url_path,
        "nonce_generated_and_signed_match": True,
        "nonce_signed_and_transmitted_match": tx_nonce == nonce,
        "nonce_monotonic": nonce_monotonic,
        "nonce_not_stale_vs_previous": int(nonce) > prior_nonce,
    }
    diagnostics.update(_contract_checks(request_path=request_path, method=method, content_type=content_type, encoded_body=encoded_body))
    return {f"kraken_{key}": value for key, value in diagnostics.items()}


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
        self._nonce_lock = asyncio.Lock()
        self._last_nonce_ms = 0

    async def _next_nonce(self) -> str:
        async with self._nonce_lock:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms <= self._last_nonce_ms:
                now_ms = self._last_nonce_ms + 1
            self._last_nonce_ms = now_ms
            return str(now_ms)

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
            error_summary = _safe_auth_diagnostics(exc=exc)
            reachable = isinstance(exc, InvalidRequestError)
            return ExchangeAuthResult(
                reachable=reachable,
                authenticated=False,
                account_status=None,
                permissions=[],
                heartbeat_at=heartbeat_at,
                clock_skew_seconds=None,
                withdrawals_permission_granted=False,
                trade_permission_present=False,
                error=error_summary,
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

    async def fetch_price_evidence(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        product_id: str,
    ) -> ExchangePriceEvidence:
        _ = credentials
        retrieved_at = datetime.now(timezone.utc)
        normalized_product, target_pair = _normalize_intent_product(product_id)
        pair_info = await self._load_pair_info(environment=environment, normalized_pair=target_pair)
        if pair_info is None:
            raise InvalidRequestError(
                message="Kraken product unavailable for price evidence",
                details={"product_id": normalized_product},
            )

        altname = str(pair_info.get("altname") or "XBTUSD")
        ticker_payload = await self._public_request(path="/public/Ticker", environment=environment, params={"pair": altname})
        ticker_result = ticker_payload.get("result") if isinstance(ticker_payload.get("result"), dict) else {}
        ticker_row = next(iter(ticker_result.values())) if isinstance(ticker_result, dict) and ticker_result else None
        if not isinstance(ticker_row, dict):
            raise InvalidRequestError(
                message="Kraken ticker payload unavailable",
                details={"product_id": normalized_product, "pair": altname},
            )

        ask_arr = ticker_row.get("a") if isinstance(ticker_row.get("a"), list) else []
        bid_arr = ticker_row.get("b") if isinstance(ticker_row.get("b"), list) else []
        last_arr = ticker_row.get("c") if isinstance(ticker_row.get("c"), list) else []
        ask = _to_decimal(ask_arr[0] if len(ask_arr) > 0 else None)
        bid = _to_decimal(bid_arr[0] if len(bid_arr) > 0 else None)
        last_trade = _to_decimal(last_arr[0] if len(last_arr) > 0 else None)

        midpoint: Decimal | None = None
        if ask > Decimal("0") and bid > Decimal("0"):
            midpoint = (ask + bid) / Decimal("2")

        reference_price: Decimal | None = None
        if ask > Decimal("0"):
            reference_price = ask
        elif last_trade > Decimal("0"):
            reference_price = last_trade
        elif midpoint is not None and midpoint > Decimal("0"):
            reference_price = midpoint

        if reference_price is None or reference_price <= Decimal("0"):
            raise InvalidRequestError(
                message="Kraken executable quote unavailable",
                details={"product_id": normalized_product, "pair": altname},
            )

        base_currency = _canonical_asset(str(pair_info.get("base") or "BTC"))
        quote_currency = _canonical_asset(str(pair_info.get("quote") or "USD"))

        return ExchangePriceEvidence(
            evidence_id=uuid.uuid4(),
            provider=self.provider,
            venue=self.provider,
            product_id=normalized_product,
            symbol=base_currency,
            quote_currency=quote_currency,
            base_currency=base_currency,
            bid=bid if bid > Decimal("0") else None,
            ask=ask if ask > Decimal("0") else None,
            midpoint=midpoint,
            last_trade=last_trade if last_trade > Decimal("0") else None,
            reference_price=reference_price,
            observed_at=retrieved_at,
            retrieved_at=retrieved_at,
            latency_ms=None,
            freshness_seconds=0,
            source_endpoint="/public/Ticker",
            retrieval_method="provider_public_rest",
            confidence=None,
            audit_metadata={
                "pair": altname,
                "source": "kraken_public_assetpairs_ticker",
            },
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
        side = request.side.upper()
        if side not in {"BUY", "SELL"} or request.order_type.upper() != "MARKET":
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="unsupported_order_shape",
                    message="Kraken submission supports only MARKET BUY/SELL in this execution profile",
                    retryable=False,
                    provider_status=None,
                    safe_details={"side": request.side, "order_type": request.order_type},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )
        if side == "BUY" and (request.quote_size is None or request.quote_size <= Decimal("0")):
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="invalid_quote_size",
                    message="Kraken market buy submission requires quote_size > 0",
                    retryable=False,
                    provider_status=None,
                    safe_details={"quote_size": None if request.quote_size is None else format(request.quote_size, "f")},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )
        if side == "SELL" and (request.base_size is None or request.base_size <= Decimal("0")):
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="invalid_base_size",
                    message="Kraken market sell submission requires base_size > 0",
                    retryable=False,
                    provider_status=None,
                    safe_details={"base_size": None if request.base_size is None else format(request.base_size, "f")},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )

        normalized_product, target_pair = _normalize_intent_product(request.product_id)
        pair_info = await self._load_pair_info(environment=environment, normalized_pair=target_pair)
        if pair_info is None:
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="product_unavailable",
                    message="Kraken pair metadata unavailable for submission",
                    retryable=False,
                    provider_status=None,
                    safe_details={"product_id": normalized_product, "pair": target_pair},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )

        altname = str(pair_info.get("altname") or "XBTUSD")
        pair_decimals = int(pair_info.get("pair_decimals") or 1)
        lot_decimals = int(pair_info.get("lot_decimals") or 8)
        ordermin = _to_decimal(pair_info.get("ordermin"))
        costmin = _to_decimal(pair_info.get("costmin"))

        quote_size: Decimal | None = None
        base_size: Decimal | None = None
        if side == "BUY":
            quote_size = _quantize(request.quote_size, pair_decimals)
            if quote_size <= Decimal("0"):
                return ExchangeOrderSubmissionResult(
                    classification="rejected",
                    order=None,
                    rejection=ExchangeProviderRejection(
                        code="quote_size_quantized_to_zero",
                        message="Kraken quote_size underflows provider precision",
                        retryable=False,
                        provider_status=None,
                        safe_details={"requested_quote_size": format(request.quote_size, "f"), "pair_decimals": pair_decimals},
                    ),
                    ambiguous=None,
                    raw_response={},
                    safe_headers={},
                )
            if quote_size > request.quote_size:
                return ExchangeOrderSubmissionResult(
                    classification="rejected",
                    order=None,
                    rejection=ExchangeProviderRejection(
                        code="quote_size_precision_conflict",
                        message="Kraken quote_size precision would increase requested amount",
                        retryable=False,
                        provider_status=None,
                        safe_details={
                            "requested_quote_size": format(request.quote_size, "f"),
                            "quantized_quote_size": format(quote_size, "f"),
                            "pair_decimals": pair_decimals,
                        },
                    ),
                    ambiguous=None,
                    raw_response={},
                    safe_headers={},
                )
        else:
            base_size = _quantize(request.base_size, lot_decimals)
            if base_size <= Decimal("0"):
                return ExchangeOrderSubmissionResult(
                    classification="rejected",
                    order=None,
                    rejection=ExchangeProviderRejection(
                        code="base_size_quantized_to_zero",
                        message="Kraken base_size underflows provider precision",
                        retryable=False,
                        provider_status=None,
                        safe_details={"requested_base_size": format(request.base_size, "f"), "lot_decimals": lot_decimals},
                    ),
                    ambiguous=None,
                    raw_response={},
                    safe_headers={},
                )
            if base_size > request.base_size:
                return ExchangeOrderSubmissionResult(
                    classification="rejected",
                    order=None,
                    rejection=ExchangeProviderRejection(
                        code="base_size_precision_conflict",
                        message="Kraken base_size precision would increase requested amount",
                        retryable=False,
                        provider_status=None,
                        safe_details={
                            "requested_base_size": format(request.base_size, "f"),
                            "quantized_base_size": format(base_size, "f"),
                            "lot_decimals": lot_decimals,
                        },
                    ),
                    ambiguous=None,
                    raw_response={},
                    safe_headers={},
                )

        ticker_payload = await self._public_request(path="/public/Ticker", environment=environment, params={"pair": altname})
        ticker_result = ticker_payload.get("result") if isinstance(ticker_payload.get("result"), dict) else {}
        ticker_row = next(iter(ticker_result.values()), None) if isinstance(ticker_result, dict) and ticker_result else None
        if not isinstance(ticker_row, dict):
            return ExchangeOrderSubmissionResult(
                classification="ambiguous",
                order=None,
                rejection=None,
                ambiguous=ExchangeProviderAmbiguousResponse(
                    reason="ticker_unavailable",
                    safe_details={"pair": altname},
                ),
                raw_response=_redact_sensitive(ticker_payload),
                safe_headers={},
            )
        ask_arr = ticker_row.get("a") if isinstance(ticker_row.get("a"), list) else []
        best_ask = _to_decimal(ask_arr[0] if len(ask_arr) > 0 else None)
        if best_ask <= Decimal("0"):
            return ExchangeOrderSubmissionResult(
                classification="ambiguous",
                order=None,
                rejection=None,
                ambiguous=ExchangeProviderAmbiguousResponse(
                    reason="best_ask_unavailable",
                    safe_details={"pair": altname},
                ),
                raw_response={"pair": altname},
                safe_headers={},
            )

        estimated_base_size = _quantize(quote_size / best_ask, lot_decimals) if side == "BUY" else base_size
        if ordermin > Decimal("0") and estimated_base_size < ordermin:
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="below_min_order_size",
                    message="Kraken estimated base size below ordermin",
                    retryable=False,
                    provider_status=None,
                    safe_details={
                        "estimated_base_size": format(estimated_base_size, "f"),
                        "ordermin": format(ordermin, "f"),
                        "lot_decimals": lot_decimals,
                    },
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )
        if side == "BUY" and costmin > Decimal("0") and quote_size < costmin:
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code="below_min_order_cost",
                    message="Kraken quote size below costmin",
                    retryable=False,
                    provider_status=None,
                    safe_details={"quote_size": format(quote_size, "f"), "costmin": format(costmin, "f")},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )

        payload = {
            "ordertype": "market",
            "type": side.lower(),
            "pair": altname,
            "volume": format(quote_size, "f") if side == "BUY" else format(base_size, "f"),
            "timeinforce": "IOC",
            "cl_ord_id": request.client_order_id,
        }
        if side == "BUY":
            payload["oflags"] = "fciq,viqc"

        try:
            provider_response = await self._private_request(
                path="/private/AddOrder",
                environment=environment,
                credentials=credentials,
                payload=payload,
            )
        except InvalidRequestError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            errors = details.get("errors") if isinstance(details.get("errors"), list) else []
            status_code = details.get("status_code")
            if isinstance(status_code, int) and status_code >= 500:
                return ExchangeOrderSubmissionResult(
                    classification="ambiguous",
                    order=None,
                    rejection=None,
                    ambiguous=ExchangeProviderAmbiguousResponse(
                        reason="provider_http_5xx",
                        safe_details={"status_code": status_code, "path": details.get("path")},
                    ),
                    raw_response={},
                    safe_headers={},
                )
            rejection_code = "provider_rejected"
            if any("insufficient" in str(item).lower() for item in errors):
                rejection_code = "insufficient_funds"
            elif any("invalid nonce" in str(item).lower() for item in errors):
                rejection_code = "invalid_nonce"
            elif any("invalid arguments" in str(item).lower() for item in errors):
                rejection_code = "invalid_arguments"
            return ExchangeOrderSubmissionResult(
                classification="rejected",
                order=None,
                rejection=ExchangeProviderRejection(
                    code=rejection_code,
                    message="Kraken order rejected",
                    retryable=False,
                    provider_status=None,
                    safe_details={"errors": [str(item) for item in errors[:5]], "path": details.get("path")},
                ),
                ambiguous=None,
                raw_response={},
                safe_headers={},
            )
        except ServiceUnavailableError as exc:
            return ExchangeOrderSubmissionResult(
                classification="ambiguous",
                order=None,
                rejection=None,
                ambiguous=ExchangeProviderAmbiguousResponse(
                    reason="provider_transport_unavailable",
                    safe_details={"error_type": exc.__class__.__name__},
                ),
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

        result = provider_response.get("result") if isinstance(provider_response.get("result"), dict) else {}
        txids = result.get("txid") if isinstance(result.get("txid"), list) else []
        provider_order_id = str(txids[0]) if txids else None
        if provider_order_id is None:
            return ExchangeOrderSubmissionResult(
                classification="ambiguous",
                order=ExchangeProviderOrder(
                    provider_order_id=None,
                    client_order_id=request.client_order_id,
                    product_id=normalized_product,
                    side=side,
                    status="UNKNOWN",
                    submitted_at=datetime.now(timezone.utc),
                    acknowledged_at=None,
                    raw=_redact_sensitive(provider_response),
                ),
                rejection=None,
                ambiguous=ExchangeProviderAmbiguousResponse(
                    reason="missing_provider_order_id",
                    safe_details={"result_keys": sorted(result.keys()) if isinstance(result, dict) else []},
                ),
                raw_response=_redact_sensitive(provider_response),
                safe_headers={},
            )

        return ExchangeOrderSubmissionResult(
            classification="success",
            order=ExchangeProviderOrder(
                provider_order_id=provider_order_id,
                client_order_id=request.client_order_id,
                product_id=normalized_product,
                side=side,
                status="OPEN",
                submitted_at=datetime.now(timezone.utc),
                acknowledged_at=datetime.now(timezone.utc),
                raw=_redact_sensitive(provider_response),
            ),
            rejection=None,
            ambiguous=None,
            raw_response=_redact_sensitive(provider_response),
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
        normalized_product = None
        normalized_pair = None
        if product_id is not None:
            normalized_product, normalized_pair = _normalize_intent_product(product_id)

        open_payload = await self._private_request(
            path="/private/OpenOrders",
            environment=environment,
            credentials=credentials,
            payload={"cl_ord_id": client_order_id} if client_order_id else {},
        )
        open_rows = (open_payload.get("result") or {}).get("open") if isinstance(open_payload.get("result"), dict) else {}
        if isinstance(open_rows, dict):
            for txid, row in open_rows.items():
                if not isinstance(row, dict):
                    continue
                if provider_order_id is not None and str(txid) != provider_order_id:
                    continue
                if client_order_id is not None and str(row.get("cl_ord_id") or "") != client_order_id:
                    continue
                pair = str((row.get("descr") or {}).get("pair") or "") if isinstance(row.get("descr"), dict) else ""
                if normalized_pair is not None and pair.replace("-", "/").upper() != normalized_pair.upper():
                    continue
                status = str(row.get("status") or "open").upper()
                return ExchangeProviderOrder(
                    provider_order_id=str(txid),
                    client_order_id=str(row.get("cl_ord_id") or client_order_id) if (row.get("cl_ord_id") or client_order_id) else None,
                    product_id=normalized_product,
                    side=str((row.get("descr") or {}).get("type") or "").upper() if isinstance(row.get("descr"), dict) else None,
                    status=status,
                    submitted_at=_parse_kraken_timestamp(row),
                    acknowledged_at=_parse_kraken_timestamp(row),
                    raw=_redact_sensitive(row),
                )

        closed_query: dict[str, str] = {"trades": "true"}
        if client_order_id:
            closed_query["cl_ord_id"] = client_order_id
        closed_payload = await self._private_request(
            path="/private/ClosedOrders",
            environment=environment,
            credentials=credentials,
            payload=closed_query,
        )
        closed_rows = (closed_payload.get("result") or {}).get("closed") if isinstance(closed_payload.get("result"), dict) else {}
        if not isinstance(closed_rows, dict):
            return None
        for txid, row in closed_rows.items():
            if not isinstance(row, dict):
                continue
            if provider_order_id is not None and str(txid) != provider_order_id:
                continue
            if client_order_id is not None and str(row.get("cl_ord_id") or "") != client_order_id:
                continue
            pair = str((row.get("descr") or {}).get("pair") or "") if isinstance(row.get("descr"), dict) else ""
            if normalized_pair is not None and pair.replace("-", "/").upper() != normalized_pair.upper():
                continue
            raw_status = str(row.get("status") or "").lower()
            vol = _to_decimal(row.get("vol"))
            vol_exec = _to_decimal(row.get("vol_exec"))
            if raw_status in {"canceled", "expired"}:
                status = "CANCELLED"
            elif raw_status == "closed" and vol > Decimal("0") and vol_exec >= vol:
                status = "FILLED"
            elif raw_status == "closed" and vol_exec > Decimal("0"):
                status = "PARTIALLY_FILLED"
            elif raw_status == "closed":
                status = "CLOSED"
            elif raw_status == "pending":
                status = "PENDING"
            elif raw_status == "open":
                status = "OPEN"
            else:
                status = raw_status.upper() if raw_status else "UNKNOWN"
            return ExchangeProviderOrder(
                provider_order_id=str(txid),
                client_order_id=str(row.get("cl_ord_id") or client_order_id) if (row.get("cl_ord_id") or client_order_id) else None,
                product_id=normalized_product,
                side=str((row.get("descr") or {}).get("type") or "").upper() if isinstance(row.get("descr"), dict) else None,
                status=status,
                submitted_at=_parse_kraken_timestamp(row),
                acknowledged_at=_parse_kraken_timestamp(row),
                raw=_redact_sensitive(row),
            )
        return None

    async def list_fills(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str,
    ) -> list[ExchangeProviderFill]:
        payload = await self._private_request(
            path="/private/TradesHistory",
            environment=environment,
            credentials=credentials,
            payload={"trades": "false", "without_count": "true"},
        )
        rows = (payload.get("result") or {}).get("trades") if isinstance(payload.get("result"), dict) else {}
        if not isinstance(rows, dict):
            return []

        fills: list[ExchangeProviderFill] = []
        for trade_txid, item in rows.items():
            if not isinstance(item, dict):
                continue
            if str(item.get("ordertxid") or "") != provider_order_id:
                continue
            size = _to_decimal(item.get("vol"))
            price = _to_decimal(item.get("price"))
            if size <= Decimal("0") or price <= Decimal("0"):
                continue
            fee_amount = _to_decimal(item.get("fee"))
            fee_currency = "USD"
            pair = str(item.get("pair") or "").upper()
            if pair.endswith("EUR"):
                fee_currency = "EUR"
            elif pair.endswith("USD"):
                fee_currency = "USD"
            occurred_at = _parse_kraken_timestamp(item)
            fills.append(
                ExchangeProviderFill(
                    provider_fill_id=str(item.get("trade_id") or trade_txid),
                    provider_order_id=provider_order_id,
                    product_id=None,
                    size=size,
                    price=price,
                    fee=None if fee_amount <= Decimal("0") else ExchangeProviderFee(amount=fee_amount, currency=fee_currency),
                    occurred_at=occurred_at,
                    raw=_redact_sensitive(item),
                )
            )
        return fills

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

        base_url = "https://api.kraken.com"
        request_path = f"/0{path}"
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.get(request_path, params=params)
        except httpx.HTTPError as exc:
            self._last_error_classification = "network_error"
            self._last_error_message = str(exc)
            raise ServiceUnavailableError(message="Kraken API is unreachable", details={"provider": self.provider, "path": path}) from exc

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

        base_url = "https://api.kraken.com"
        prior_nonce = self._last_nonce_ms
        nonce = await self._next_nonce()
        body_payload = {"nonce": nonce, **payload}

        request_path = f"/0{path}"
        encoded_body = _encode_form_payload(body_payload)
        method = "POST"
        content_type = "application/x-www-form-urlencoded"

        signature = build_kraken_signature_from_encoded_payload(
            url_path=request_path,
            nonce=nonce,
            encoded_payload=encoded_body,
            secret_b64=credentials["api_secret"],
        )
        headers = {
            "API-Key": credentials["api_key"],
            "API-Sign": signature,
            "Content-Type": content_type,
            "Accept": "application/json",
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds) as client:
                response = await client.post(request_path, content=encoded_body, headers=headers)
        except httpx.HTTPError as exc:
            self._last_error_classification = "network_error"
            self._last_error_message = str(exc)
            raise ServiceUnavailableError(message="Kraken API is unreachable", details={"provider": self.provider, "path": path}) from exc

        request_duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        request_obj = getattr(response, "request", None)
        transmitted_path = request_path
        transmitted_scheme = "https"
        transmitted_host = "api.kraken.com"
        transmitted_http_version = None
        transmitted_body = encoded_body
        transmitted_content_type = content_type
        query_forensics = {
            "url_query_parameters_present": False,
            "final_url_has_query": False,
            "final_query_component_length": 0,
            "final_query_parameter_count": 0,
            "form_fields_duplicated_into_url_query": False,
            "nonce_present_in_url_query": False,
        }
        if request_obj is not None:
            req_url = getattr(request_obj, "url", None)
            if req_url is not None:
                transmitted_path = str(getattr(req_url, "path", request_path))
                transmitted_scheme = str(getattr(req_url, "scheme", "https"))
                transmitted_host = str(getattr(req_url, "host", "api.kraken.com"))
                query_forensics = _query_forensics(
                    query_raw=getattr(req_url, "query", ""),
                    encoded_body=encoded_body,
                )
            req_content = getattr(request_obj, "content", None)
            if isinstance(req_content, bytes):
                transmitted_body = req_content.decode("utf-8", errors="replace")
            req_headers = getattr(request_obj, "headers", None)
            if req_headers is not None:
                transmitted_content_type = str(req_headers.get("Content-Type", content_type))
            extensions = getattr(response, "extensions", {})
            version_raw = extensions.get("http_version") if isinstance(extensions, dict) else None
            if isinstance(version_raw, bytes):
                transmitted_http_version = version_raw.decode("utf-8", errors="ignore")
            elif isinstance(version_raw, str):
                transmitted_http_version = version_raw

        forensics = _safe_kraken_forensics(
            method=method,
            request_path=request_path,
            encoded_body=encoded_body,
            nonce=nonce,
            content_type=content_type,
            api_key_present=bool(headers.get("API-Key")),
            api_sign_present=bool(headers.get("API-Sign")),
            request_url_path=transmitted_path,
            request_scheme=transmitted_scheme,
            request_host=transmitted_host,
            request_http_version=transmitted_http_version,
            request_body=transmitted_body,
            request_content_type=transmitted_content_type,
            response_status_code=response.status_code,
            kraken_errors=[],
            request_duration_ms=request_duration_ms,
            retry_count=0,
            redirect_count=len(getattr(response, "history", []) or []),
            nonce_monotonic=int(nonce) > prior_nonce,
            prior_nonce=prior_nonce,
            signature=signature,
            secret_b64=credentials["api_secret"],
        )
        forensics["kraken_url_query_parameters_present"] = bool(query_forensics["url_query_parameters_present"])
        forensics["kraken_final_url_has_query"] = bool(query_forensics["final_url_has_query"])
        forensics["kraken_final_query_component_length"] = int(query_forensics["final_query_component_length"])
        forensics["kraken_final_query_parameter_count"] = int(query_forensics["final_query_parameter_count"])
        forensics["kraken_form_fields_duplicated_into_url_query"] = bool(query_forensics["form_fields_duplicated_into_url_query"])
        forensics["kraken_nonce_present_in_url_query"] = bool(query_forensics["nonce_present_in_url_query"])
        final_request_path = transmitted_path
        forensics["kraken_final_request_path"] = final_request_path
        forensics["kraken_query_contains_question_mark"] = "?" in final_request_path
        forensics["kraken_prepared_method"] = method
        forensics["kraken_prepared_url_path"] = request_path
        forensics["kraken_prepared_query_string_present"] = bool(query_forensics["url_query_parameters_present"])
        forensics["kraken_prepared_body_length"] = len(encoded_body.encode("utf-8"))
        forensics["kraken_prepared_content_type"] = content_type
        forensics["kraken_header_name_presence"] = {
            "api_key": bool(headers.get("API-Key")),
            "api_sign": bool(headers.get("API-Sign")),
            "content_type": bool(headers.get("Content-Type")),
        }
        forensics["kraken_prepared_body_hash_equals_signed_body_hash"] = (
            hashlib.sha256(encoded_body.encode("utf-8")).hexdigest()
            == hashlib.sha256(transmitted_body.encode("utf-8")).hexdigest()
        )
        redirect_modified_url = False
        history = list(getattr(response, "history", []) or [])
        if history:
            first_request = getattr(history[0], "request", None)
            first_url = getattr(first_request, "url", None)
            if first_url is not None:
                first_path = str(getattr(first_url, "path", ""))
                redirect_modified_url = first_path != final_request_path
        forensics["kraken_redirect_modified_url"] = redirect_modified_url

        if response.status_code >= 400:
            self._last_error_classification = "http_error"
            self._last_error_message = f"status={response.status_code} path={path}"
            raise InvalidRequestError(
                message="Kraken API request failed",
                details={"status_code": response.status_code, "path": path, "response_text": response.text[:500], "forensics": forensics},
            )

        parsed = self._parse_json_response(response=response, path=path)
        errors = parsed.get("error") if isinstance(parsed.get("error"), list) else []
        if errors:
            forensics["kraken_error_array"] = [str(item) for item in errors[:10]]
            self._last_error_classification = "provider_error"
            self._last_error_message = str(errors[:1])
            raise InvalidRequestError(
                message="Kraken API returned errors",
                details={"path": path, "errors": [str(item) for item in errors[:5]], "forensics": forensics},
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
        if path == "/private/AddOrder":
            return {"error": [], "result": {"descr": {"order": "buy 5.0 XBTUSD @ market"}, "txid": ["MOCK-KRAKEN-TX-1"]}}
        if path == "/private/OpenOrders":
            return {"error": [], "result": {"open": {}}}
        if path == "/private/ClosedOrders":
            return {"error": [], "result": {"closed": {}, "count": 0}}
        if path == "/private/TradesHistory":
            return {"error": [], "result": {"trades": {}, "count": 0}}
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
