from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
from pathlib import Path
import time
import urllib.parse

import httpx

from app.config import Settings


def _classify_kraken_error(errors: list[str]) -> str:
    lowered = " ".join(item.lower() for item in errors)
    if "invalid signature" in lowered:
        return "invalid_signature"
    if "invalid key" in lowered:
        return "invalid_key"
    if "invalid nonce" in lowered:
        return "invalid_nonce"
    if "permission" in lowered or "denied" in lowered:
        return "permission_denied"
    return "kraken_error"


def _load_kraken_credentials() -> tuple[dict[str, str] | None, bool, bool, bool]:
    try:
        settings = Settings(_env_file=str(Path(__file__).resolve().parents[1] / ".env"), _env_file_encoding="utf-8")
    except Exception:
        return None, False, False, False

    api_key_value = getattr(settings, "kraken_api_key", None)
    api_secret_value = getattr(settings, "kraken_api_secret", None)
    api_key = "" if api_key_value is None else str(api_key_value.get_secret_value()).strip()
    api_secret = "" if api_secret_value is None else str(api_secret_value.get_secret_value()).strip()
    return {"api_key": api_key, "api_secret": api_secret}, True, bool(api_key), bool(api_secret)


def _build_form_body(*, nonce: str) -> str:
    return urllib.parse.urlencode([("nonce", nonce)])


def _build_api_sign(*, request_path: str, nonce: str, body: str, api_secret_b64: str) -> str:
    secret = base64.b64decode(api_secret_b64.encode("utf-8"))
    sha_digest = hashlib.sha256((nonce + body).encode("utf-8")).digest()
    digest = hmac.new(secret, request_path.encode("utf-8") + sha_digest, hashlib.sha512).digest()
    return base64.b64encode(digest).decode("utf-8")


def _emit_result(
    *,
    credential_configuration_loaded: bool,
    kraken_api_key_configured: bool,
    kraken_api_secret_configured: bool,
    authentication_attempted: bool,
    authentication_succeeded: bool,
    http_status: int,
    kraken_error_category: str,
    safe_provider_error: str,
    endpoint: str,
    method: str,
    content_type: str,
    request_duration_ms: int,
) -> None:
    print(f"credential_configuration_loaded={str(credential_configuration_loaded).lower()}")
    print(f"kraken_api_key_configured={str(kraken_api_key_configured).lower()}")
    print(f"kraken_api_secret_configured={str(kraken_api_secret_configured).lower()}")
    print(f"authentication_attempted={str(authentication_attempted).lower()}")
    print(f"authentication_succeeded={str(authentication_succeeded).lower()}")
    print(f"http_status={http_status}")
    print(f"kraken_error_category={kraken_error_category}")
    print(f"safe_provider_error={safe_provider_error}")
    print(f"endpoint={endpoint}")
    print(f"method={method}")
    print(f"content_type={content_type}")
    print(f"request_duration_ms={request_duration_ms}")


def _run(*, timeout_seconds: float) -> int:
    credentials, credential_configuration_loaded, kraken_api_key_configured, kraken_api_secret_configured = _load_kraken_credentials()
    endpoint = "/0/private/Balance"
    method = "POST"
    content_type = "application/x-www-form-urlencoded"

    if not credential_configuration_loaded or credentials is None:
        _emit_result(
            credential_configuration_loaded=False,
            kraken_api_key_configured=False,
            kraken_api_secret_configured=False,
            authentication_attempted=False,
            authentication_succeeded=False,
            http_status=0,
            kraken_error_category="configuration_error",
            safe_provider_error="configuration_not_loaded",
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=0,
        )
        return 2

    if not kraken_api_key_configured or not kraken_api_secret_configured:
        _emit_result(
            credential_configuration_loaded=True,
            kraken_api_key_configured=kraken_api_key_configured,
            kraken_api_secret_configured=kraken_api_secret_configured,
            authentication_attempted=False,
            authentication_succeeded=False,
            http_status=0,
            kraken_error_category="configuration_error",
            safe_provider_error="credential_missing",
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=0,
        )
        return 2

    nonce = str(int(time.time() * 1000))
    body = _build_form_body(nonce=nonce)
    api_sign = _build_api_sign(request_path=endpoint, nonce=nonce, body=body, api_secret_b64=credentials["api_secret"])
    headers = {
        "API-Key": credentials["api_key"],
        "API-Sign": api_sign,
        "Content-Type": content_type,
        "Accept": "application/json",
    }

    start = time.perf_counter()
    try:
        with httpx.Client(base_url="https://api.kraken.com", timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.post(endpoint, content=body, headers=headers)
    except httpx.TimeoutException:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _emit_result(
            credential_configuration_loaded=True,
            kraken_api_key_configured=True,
            kraken_api_secret_configured=True,
            authentication_attempted=True,
            authentication_succeeded=False,
            http_status=0,
            kraken_error_category="transport_timeout",
            safe_provider_error="transport_timeout",
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=duration_ms,
        )
        return 1
    except httpx.RequestError:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _emit_result(
            credential_configuration_loaded=True,
            kraken_api_key_configured=True,
            kraken_api_secret_configured=True,
            authentication_attempted=True,
            authentication_succeeded=False,
            http_status=0,
            kraken_error_category="transport_error",
            safe_provider_error="transport_error",
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=duration_ms,
        )
        return 1

    duration_ms = int((time.perf_counter() - start) * 1000)
    http_status = int(response.status_code)

    try:
        payload = response.json()
    except ValueError:
        _emit_result(
            credential_configuration_loaded=True,
            kraken_api_key_configured=True,
            kraken_api_secret_configured=True,
            authentication_attempted=True,
            authentication_succeeded=False,
            http_status=http_status,
            kraken_error_category="http_error" if http_status != 200 else "parse_error",
            safe_provider_error="invalid_json" if http_status == 200 else f"http_{http_status}",
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=duration_ms,
        )
        return 1

    errors = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), list) else []
    if errors:
        error_text = str(errors[0]) if errors else "kraken_error"
        _emit_result(
            credential_configuration_loaded=True,
            kraken_api_key_configured=True,
            kraken_api_secret_configured=True,
            authentication_attempted=True,
            authentication_succeeded=False,
            http_status=http_status,
            kraken_error_category=_classify_kraken_error([str(item) for item in errors]),
            safe_provider_error=error_text,
            endpoint=endpoint,
            method=method,
            content_type=content_type,
            request_duration_ms=duration_ms,
        )
        return 1

    _emit_result(
        credential_configuration_loaded=True,
        kraken_api_key_configured=True,
        kraken_api_secret_configured=True,
        authentication_attempted=True,
        authentication_succeeded=True,
        http_status=http_status,
        kraken_error_category="none",
        safe_provider_error="none",
        endpoint=endpoint,
        method=method,
        content_type=content_type,
        request_duration_ms=duration_ms,
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean-room Kraken Balance authentication proof")
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return _run(timeout_seconds=float(args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
