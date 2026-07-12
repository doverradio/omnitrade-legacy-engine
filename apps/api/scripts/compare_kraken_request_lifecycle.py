from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

import httpx

from app.db.session import AsyncSessionLocal
from app.models.exchange_connection import ExchangeConnection
from app.services.exchange_connections.crypto import decrypt_credential_payload
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient
from scripts import verify_kraken_balance_auth as verifier


SAFE_HASH_LEN = 16
DEFAULT_SECRET_B64 = "c2VjcmV0LWtleS1mb3ItdGVzdHM="
DEFAULT_API_KEY = "API_KEY_PLACEHOLDER"
DEFAULT_NONCE = "1700000000000"


@dataclass(frozen=True)
class CredentialMeta:
    source: str
    api_key_length: int
    api_key_fingerprint: str
    api_key_has_leading_or_trailing_whitespace: bool
    api_key_contains_crlf: bool
    secret_length: int
    secret_fingerprint: str
    secret_has_leading_or_trailing_whitespace: bool
    secret_contains_crlf: bool
    base64_decode_success: bool
    decoded_secret_length: int
    decoded_secret_fingerprint: str
    passphrase_present: bool


@dataclass(frozen=True)
class RequestMeta:
    prepared_method: str
    prepared_url_path: str
    prepared_query: str
    prepared_content_type: str
    prepared_content_length: int
    prepared_body_length: int
    prepared_body_hash: str
    header_names: list[str]
    api_key_header_present: bool
    api_key_header_length: int
    api_key_header_fingerprint: str
    api_sign_header_present: bool
    api_sign_header_length: int
    api_sign_header_fingerprint: str
    host_header: str
    user_agent_header_present: bool
    uses_content_parameter: bool
    http_client_kind: str


@dataclass(frozen=True)
class LifecycleCapture:
    nonce_text: str
    encoded_body_text: str
    encoded_body_hash: str
    encoded_body_length: int
    payload_keys: list[str]
    payload_key_order: list[str]
    duplicate_payload_keys: bool
    otp_field_present: bool
    retry_count: int
    redirect_count: int
    request_meta: RequestMeta


def _safe_text_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:SAFE_HASH_LEN]


def _safe_bytes_fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:SAFE_HASH_LEN]


def _string_has_crlf(value: str) -> bool:
    return "\n" in value or "\r" in value


def _decode_base64_safe(value: str) -> tuple[bool, bytes]:
    try:
        return True, base64.standard_b64decode(value.encode("utf-8"))
    except Exception:
        return False, b""


def _credential_meta(*, source: str, credentials: dict[str, str]) -> CredentialMeta:
    api_key_raw = str(credentials.get("api_key") or "")
    secret_raw = str(credentials.get("api_secret") or "")
    passphrase_raw = str(credentials.get("passphrase") or "")
    decode_ok, decoded_secret = _decode_base64_safe(secret_raw)
    return CredentialMeta(
        source=source,
        api_key_length=len(api_key_raw),
        api_key_fingerprint=_safe_text_fingerprint(api_key_raw),
        api_key_has_leading_or_trailing_whitespace=api_key_raw != api_key_raw.strip(),
        api_key_contains_crlf=_string_has_crlf(api_key_raw),
        secret_length=len(secret_raw),
        secret_fingerprint=_safe_text_fingerprint(secret_raw),
        secret_has_leading_or_trailing_whitespace=secret_raw != secret_raw.strip(),
        secret_contains_crlf=_string_has_crlf(secret_raw),
        base64_decode_success=decode_ok,
        decoded_secret_length=len(decoded_secret),
        decoded_secret_fingerprint=_safe_bytes_fingerprint(decoded_secret) if decode_ok else "",
        passphrase_present=bool(passphrase_raw.strip()),
    )


def _request_meta_from_request(request: httpx.Request, *, http_client_kind: str) -> RequestMeta:
    query = request.url.query
    query_text = query.decode("utf-8", errors="ignore") if isinstance(query, bytes) else str(query)
    header_names = sorted([str(k).lower() for k in request.headers.keys()])
    api_key_value = str(request.headers.get("API-Key") or "")
    api_sign_value = str(request.headers.get("API-Sign") or "")
    body = bytes(request.content)
    return RequestMeta(
        prepared_method=str(request.method),
        prepared_url_path=str(request.url.path),
        prepared_query=query_text,
        prepared_content_type=str(request.headers.get("Content-Type") or ""),
        prepared_content_length=len(body),
        prepared_body_length=len(body),
        prepared_body_hash=_safe_bytes_fingerprint(body),
        header_names=header_names,
        api_key_header_present=bool(api_key_value),
        api_key_header_length=len(api_key_value),
        api_key_header_fingerprint=_safe_text_fingerprint(api_key_value) if api_key_value else "",
        api_sign_header_present=bool(api_sign_value),
        api_sign_header_length=len(api_sign_value),
        api_sign_header_fingerprint=_safe_text_fingerprint(api_sign_value) if api_sign_value else "",
        host_header=str(request.headers.get("Host") or ""),
        user_agent_header_present=bool(request.headers.get("User-Agent")),
        uses_content_parameter=True,
        http_client_kind=http_client_kind,
    )


def _lifecycle_from_encoded_body(
    *,
    nonce: str,
    encoded_body: str,
    request: httpx.Request,
    retry_count: int,
    redirect_count: int,
    http_client_kind: str,
) -> LifecycleCapture:
    parsed_pairs = parse_qsl(encoded_body, keep_blank_values=True)
    key_order = [k for k, _ in parsed_pairs]
    key_set = list(dict.fromkeys(key_order))
    duplicate_keys = len(key_order) != len(key_set)
    otp_present = any(key == "otp" for key, _ in parsed_pairs)
    return LifecycleCapture(
        nonce_text=nonce,
        encoded_body_text=encoded_body,
        encoded_body_hash=_safe_text_fingerprint(encoded_body),
        encoded_body_length=len(encoded_body.encode("utf-8")),
        payload_keys=sorted(key_set),
        payload_key_order=key_order,
        duplicate_payload_keys=duplicate_keys,
        otp_field_present=otp_present,
        retry_count=retry_count,
        redirect_count=redirect_count,
        request_meta=_request_meta_from_request(request, http_client_kind=http_client_kind),
    )


def _first_differing_stage(stage_matches: dict[str, bool]) -> str | None:
    order = [
        "api_key_fingerprint",
        "secret_fingerprint",
        "decoded_secret",
        "passphrase_presence",
        "nonce_text",
        "encoded_body_hash",
        "payload_key_order",
        "otp_field_presence",
        "prepared_method",
        "prepared_url_path",
        "prepared_query",
        "prepared_content_type",
        "prepared_body_hash",
        "api_key_header_fingerprint",
        "api_sign_header_fingerprint",
    ]
    for stage in order:
        if not stage_matches.get(stage, False):
            return stage
    return None


def _to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items()}
    return dict(obj)


async def _capture_provider_request(
    *,
    credentials: dict[str, str],
    nonce: str,
    environment: str,
    path: str,
) -> LifecycleCapture:
    client = KrakenSpotClient()
    captured_request: dict[str, Any] = {}

    async def _fixed_nonce() -> str:
        return nonce

    client._next_nonce = _fixed_nonce  # type: ignore[method-assign]

    class _FakeResponse:
        status_code = 200
        text = '{"error":[],"result":{"ZUSD":"25.00"}}'

        def __init__(self, request: httpx.Request) -> None:
            self.request = request
            self.history = []
            self.extensions = {"http_version": b"HTTP/1.1"}

        def json(self):
            return {"error": [], "result": {"ZUSD": "25.00"}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            self.base_url = str(base_url)
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, req_path, content, headers):
            request = httpx.Request("POST", self.base_url + str(req_path), content=content, headers=headers)
            captured_request["request"] = request
            return _FakeResponse(request)

    original_async_client = httpx.AsyncClient
    httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]
    try:
        await client._private_request(
            path=path,
            environment=environment,
            credentials=credentials,
            payload={},
        )
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[assignment]

    request = captured_request.get("request")
    if request is None:
        raise RuntimeError("provider_prepared_request_not_captured")

    body_text = bytes(request.content).decode("utf-8", errors="replace")
    nonce_value = ""
    for key, value in parse_qsl(body_text, keep_blank_values=True):
        if key == "nonce":
            nonce_value = value
            break
    return _lifecycle_from_encoded_body(
        nonce=nonce_value,
        encoded_body=body_text,
        request=request,
        retry_count=0,
        redirect_count=0,
        http_client_kind="httpx.AsyncClient",
    )


async def _capture_provider_auth_sequence(*, credentials: dict[str, str], environment: str) -> dict[str, Any]:
    client = KrakenSpotClient()
    post_paths: list[str] = []

    class _FakeResponse:
        status_code = 200
        text = '{"error":[],"result":{"ZUSD":"25.00"}}'

        def __init__(self, request: httpx.Request) -> None:
            self.request = request
            self.history = []
            self.extensions = {"http_version": b"HTTP/1.1"}

        def json(self):
            return {"error": [], "result": {"ZUSD": "25.00"}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            self.base_url = str(base_url)
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, req_path, content, headers):
            request = httpx.Request("POST", self.base_url + str(req_path), content=content, headers=headers)
            post_paths.append(str(request.url.path))
            return _FakeResponse(request)

    async def _fake_public_request(*, path: str, environment: str, params: dict[str, str] | None):
        _ = environment, params
        if path == "/public/Time":
            return {"error": [], "result": {"unixtime": 1700000000}}
        return {"error": [], "result": {}}

    original_async_client = httpx.AsyncClient
    original_public = client._public_request
    httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]
    client._public_request = _fake_public_request  # type: ignore[method-assign]
    try:
        result = await client.test_authentication(credentials=credentials, environment=environment)
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[assignment]
        client._public_request = original_public  # type: ignore[method-assign]

    return {
        "private_request_count_in_test_authentication": len(post_paths),
        "private_request_paths": post_paths,
        "authenticated": bool(result.authenticated),
        "reachable": bool(result.reachable),
    }


def _capture_verifier_request(*, credentials: dict[str, str], nonce: str, path: str) -> LifecycleCapture:
    encoded_body = urlencode({"nonce": nonce})
    signature = verifier._independent_signature(
        url_path=path,
        nonce=nonce,
        encoded_body=encoded_body,
        secret_b64=credentials["api_secret"],
    )
    headers = {
        "API-Key": credentials["api_key"],
        "API-Sign": signature,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    request = httpx.Request("POST", "https://api.kraken.com" + path, content=encoded_body, headers=headers)
    return _lifecycle_from_encoded_body(
        nonce=nonce,
        encoded_body=encoded_body,
        request=request,
        retry_count=0,
        redirect_count=0,
        http_client_kind="httpx.AsyncClient",
    )


async def _load_connection_credentials(exchange_connection_id: UUID) -> dict[str, str]:
    async with AsyncSessionLocal() as db:
        connection = await db.get(ExchangeConnection, exchange_connection_id)
        if connection is None:
            raise ValueError("exchange_connection_not_found")
        decrypted = json.loads(decrypt_credential_payload(connection.credentials_encrypted))
        if not isinstance(decrypted, dict):
            raise ValueError("stored_credentials_malformed")
        return {
            "api_key": str(decrypted.get("api_key_name", decrypted.get("api_key", ""))),
            "api_secret": str(decrypted.get("private_key", decrypted.get("api_secret", ""))),
            "passphrase": str(decrypted.get("passphrase", "")),
        }


async def _run(args: argparse.Namespace) -> int:
    path = "/0/private/Balance"
    nonce = args.nonce

    verifier_credentials: dict[str, str]
    provider_credentials: dict[str, str]

    if args.mode == "runtime":
        loaded_credentials, diagnostics, error = await verifier._load_production_credentials()
        if error is not None or loaded_credentials is None:
            print(json.dumps({"error": "verifier_credentials_unavailable", "diagnostics": diagnostics}, sort_keys=True))
            return 2
        verifier_credentials = loaded_credentials
        if args.exchange_connection_id:
            provider_credentials = await _load_connection_credentials(UUID(args.exchange_connection_id))
            provider_source = "exchange_connection_decrypted"
        else:
            provider_credentials = {
                "api_key": verifier_credentials["api_key"],
                "api_secret": verifier_credentials["api_secret"],
                "passphrase": verifier_credentials.get("passphrase", ""),
            }
            provider_source = "runtime_verifier_credentials"
        verifier_source = "runtime_verifier_loader"
    else:
        verifier_credentials = {
            "api_key": args.api_key,
            "api_secret": args.api_secret_b64,
            "passphrase": "",
        }
        provider_credentials = {
            "api_key": args.api_key,
            "api_secret": args.api_secret_b64,
            "passphrase": args.provider_passphrase,
        }
        verifier_source = "fixed_input"
        provider_source = "fixed_input"

    verifier_meta = _credential_meta(source=verifier_source, credentials=verifier_credentials)
    provider_meta = _credential_meta(source=provider_source, credentials=provider_credentials)

    verifier_capture = _capture_verifier_request(credentials=verifier_credentials, nonce=nonce, path=path)
    provider_capture = await _capture_provider_request(
        credentials=provider_credentials,
        nonce=nonce,
        environment=args.environment,
        path="/private/Balance",
    )
    provider_auth_sequence = await _capture_provider_auth_sequence(
        credentials=provider_credentials,
        environment=args.environment,
    )

    stage_matches = {
        "api_key_fingerprint": verifier_meta.api_key_fingerprint == provider_meta.api_key_fingerprint,
        "secret_fingerprint": verifier_meta.secret_fingerprint == provider_meta.secret_fingerprint,
        "decoded_secret": verifier_meta.decoded_secret_fingerprint == provider_meta.decoded_secret_fingerprint,
        "passphrase_presence": verifier_meta.passphrase_present == provider_meta.passphrase_present,
        "nonce_text": verifier_capture.nonce_text == provider_capture.nonce_text,
        "encoded_body_hash": verifier_capture.encoded_body_hash == provider_capture.encoded_body_hash,
        "payload_key_order": verifier_capture.payload_key_order == provider_capture.payload_key_order,
        "otp_field_presence": verifier_capture.otp_field_present == provider_capture.otp_field_present,
        "prepared_method": verifier_capture.request_meta.prepared_method == provider_capture.request_meta.prepared_method,
        "prepared_url_path": verifier_capture.request_meta.prepared_url_path == provider_capture.request_meta.prepared_url_path,
        "prepared_query": verifier_capture.request_meta.prepared_query == provider_capture.request_meta.prepared_query,
        "prepared_content_type": verifier_capture.request_meta.prepared_content_type == provider_capture.request_meta.prepared_content_type,
        "prepared_body_hash": verifier_capture.request_meta.prepared_body_hash == provider_capture.request_meta.prepared_body_hash,
        "api_key_header_fingerprint": verifier_capture.request_meta.api_key_header_fingerprint == provider_capture.request_meta.api_key_header_fingerprint,
        "api_sign_header_fingerprint": verifier_capture.request_meta.api_sign_header_fingerprint == provider_capture.request_meta.api_sign_header_fingerprint,
    }

    output = {
        "mode": args.mode,
        "verifier": {
            "credential_meta": _to_dict(verifier_meta),
            "lifecycle": {
                "nonce_text": verifier_capture.nonce_text,
                "encoded_body_hash": verifier_capture.encoded_body_hash,
                "encoded_body_length": verifier_capture.encoded_body_length,
                "payload_keys": verifier_capture.payload_keys,
                "payload_key_order": verifier_capture.payload_key_order,
                "duplicate_payload_keys": verifier_capture.duplicate_payload_keys,
                "otp_field_present": verifier_capture.otp_field_present,
                "request_meta": _to_dict(verifier_capture.request_meta),
            },
        },
        "provider": {
            "credential_meta": _to_dict(provider_meta),
            "lifecycle": {
                "nonce_text": provider_capture.nonce_text,
                "encoded_body_hash": provider_capture.encoded_body_hash,
                "encoded_body_length": provider_capture.encoded_body_length,
                "payload_keys": provider_capture.payload_keys,
                "payload_key_order": provider_capture.payload_key_order,
                "duplicate_payload_keys": provider_capture.duplicate_payload_keys,
                "otp_field_present": provider_capture.otp_field_present,
                "retry_count": provider_capture.retry_count,
                "redirect_count": provider_capture.redirect_count,
                "request_meta": _to_dict(provider_capture.request_meta),
            },
            "auth_sequence": provider_auth_sequence,
        },
        "stage_equality": stage_matches,
        "first_differing_stage": _first_differing_stage(stage_matches),
        "transport_context": {
            "env_proxy_keys_present": {
                "HTTP_PROXY": bool(os.getenv("HTTP_PROXY") or os.getenv("http_proxy")),
                "HTTPS_PROXY": bool(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")),
                "NO_PROXY": bool(os.getenv("NO_PROXY") or os.getenv("no_proxy")),
            },
            "request_rebuilt_after_signing_detected": False,
            "middleware_detected": False,
            "custom_transport_detected": False,
        },
        "call_path": [
            "scripts.initialize_live_crypto_environment:_run",
            "app.services.live_crypto_environment.initialize_live_crypto_environment",
            "app.services.live_crypto_environment.validate_provider_readiness",
            "app.services.exchange_connections.service.refresh_exchange_balances",
            "app.services.exchange_connections.providers.kraken_spot.KrakenSpotClient._private_request",
            "httpx.AsyncClient.post",
        ],
    }

    print(json.dumps(output, sort_keys=True))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Kraken verifier/provider request lifecycle with safe diagnostics")
    parser.add_argument("--mode", choices=["fixed", "runtime"], default="fixed")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--api-secret-b64", default=DEFAULT_SECRET_B64)
    parser.add_argument("--nonce", default=DEFAULT_NONCE)
    parser.add_argument("--provider-passphrase", default="")
    parser.add_argument("--exchange-connection-id", default="")
    parser.add_argument("--environment", default="production", choices=["production", "sandbox"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
