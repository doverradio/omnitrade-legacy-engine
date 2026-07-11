from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
from pathlib import Path
import time
import urllib.parse

import httpx

from app.config import Settings, get_settings
from app.services.exchange_connections.providers.registry import get_exchange_provider


def _classify_kraken_error(errors: list[str]) -> str:
    lowered = " ".join(item.lower() for item in errors)
    if "invalid signature" in lowered:
        return "invalid_signature"
    if "invalid nonce" in lowered:
        return "invalid_nonce"
    if "invalid key" in lowered:
        return "invalid_key"
    if "permission" in lowered or "denied" in lowered:
        return "permission_denied"
    return "provider_error"


def _independent_signature(*, url_path: str, nonce: str, encoded_body: str, secret_b64: str) -> str:
    decoded_secret = base64.standard_b64decode(secret_b64.encode("utf-8"))
    sha_input = (nonce + encoded_body).encode("utf-8")
    sha_digest = hashlib.sha256(sha_input).digest()
    message = url_path.encode("utf-8") + sha_digest
    digest = hmac.new(decoded_secret, message, hashlib.sha512).digest()
    return base64.standard_b64encode(digest).decode("utf-8")


async def _load_production_credentials() -> tuple[dict[str, str] | None, dict[str, object], str | None]:
    diagnostics: dict[str, object] = {
        "credential_configuration_loaded": False,
        "kraken_api_key_configured": False,
        "kraken_api_secret_configured": False,
        "credential_source": "other_safe_category",
        "dotenv_file_loaded": False,
        "credentials_loaded": False,
    }

    settings = None
    try:
        settings = get_settings()
        diagnostics["credential_configuration_loaded"] = True
        diagnostics["credential_source"] = "application_settings"
    except Exception:
        try:
            dotenv_path = Path(__file__).resolve().parents[1] / ".env"
            diagnostics["dotenv_file_loaded"] = dotenv_path.exists()
            settings = Settings(_env_file=str(dotenv_path), _env_file_encoding="utf-8")
            diagnostics["credential_configuration_loaded"] = True
            diagnostics["credential_source"] = "application_settings"
        except Exception:
            return None, diagnostics, "configuration_not_loaded"

    if settings is None:
        return None, diagnostics, "configuration_not_loaded"

    api_key_secret = getattr(settings, "kraken_api_key", None)
    api_secret_secret = getattr(settings, "kraken_api_secret", None)
    otp_secret = getattr(settings, "kraken_otp", None)

    api_key = "" if api_key_secret is None else str(api_key_secret.get_secret_value()).strip()
    api_secret = "" if api_secret_secret is None else str(api_secret_secret.get_secret_value()).strip()
    otp = "" if otp_secret is None else str(otp_secret.get_secret_value()).strip()

    diagnostics["kraken_api_key_configured"] = bool(api_key)
    diagnostics["kraken_api_secret_configured"] = bool(api_secret)

    if not api_key and not api_secret:
        return None, diagnostics, "both_credentials_missing"
    if not api_key:
        return None, diagnostics, "api_key_missing"
    if not api_secret:
        return None, diagnostics, "api_secret_missing"

    diagnostics["credentials_loaded"] = True
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "passphrase": otp,
    }, diagnostics, None


def _print_loader_diagnostics(*, diagnostics: dict[str, object], provider_initialized: bool) -> None:
    print(f"credential_configuration_loaded={str(bool(diagnostics.get('credential_configuration_loaded'))).lower()}")
    print(f"kraken_api_key_configured={str(bool(diagnostics.get('kraken_api_key_configured'))).lower()}")
    print(f"kraken_api_secret_configured={str(bool(diagnostics.get('kraken_api_secret_configured'))).lower()}")
    print(f"credential_source={diagnostics.get('credential_source')}")
    print(f"dotenv_file_loaded={str(bool(diagnostics.get('dotenv_file_loaded'))).lower()}")
    print(f"credentials_loaded={str(bool(diagnostics.get('credentials_loaded'))).lower()}")
    print(f"provider_initialized={str(provider_initialized).lower()}")


async def _run(*, timeout_seconds: float) -> int:
    try:
        _ = get_exchange_provider("kraken_spot")
        provider_initialized = True
    except Exception:
        provider_initialized = False
        print("success=false")
        print("http_status=0")
        print("kraken_error_category=provider_initialization_failed")
        print("safe_provider_error=provider_initialization_failed")
        print("authentication_succeeded=false")
        print("credential_configuration_loaded=false")
        print("kraken_api_key_configured=false")
        print("kraken_api_secret_configured=false")
        print("credential_source=other_safe_category")
        print("dotenv_file_loaded=false")
        print("credentials_loaded=false")
        print("provider_initialized=false")
        return 2

    credentials, diagnostics, credential_error = await _load_production_credentials()
    _print_loader_diagnostics(diagnostics=diagnostics, provider_initialized=provider_initialized)
    if credential_error is not None or credentials is None:
        print("success=false")
        print("http_status=0")
        print(f"kraken_error_category={credential_error or 'configuration_error'}")
        print(f"safe_provider_error={credential_error or 'configuration_error'}")
        print("authentication_succeeded=false")
        return 2

    nonce = str(int(time.time() * 1000))
    payload: dict[str, str] = {"nonce": nonce}
    otp = str(credentials.get("passphrase") or "").strip()
    if otp:
        payload["otp"] = otp

    encoded_body = urllib.parse.urlencode(payload)
    request_path = "/0/private/Balance"
    signature = _independent_signature(
        url_path=request_path,
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

    try:
        async with httpx.AsyncClient(base_url="https://api.kraken.com", timeout=timeout_seconds) as client:
            response = await client.post(request_path, content=encoded_body, headers=headers)
    except httpx.HTTPError:
        print("success=false")
        print("http_status=0")
        print("kraken_error_category=configuration_error")
        print("safe_provider_error=transport_error")
        print("authentication_succeeded=false")
        return 1

    status = int(response.status_code)
    try:
        payload_json = response.json()
    except ValueError:
        print("success=false")
        print(f"http_status={status}")
        print("kraken_error_category=configuration_error")
        print("safe_provider_error=invalid_json")
        print("authentication_succeeded=false")
        return 1

    errors = payload_json.get("error") if isinstance(payload_json, dict) and isinstance(payload_json.get("error"), list) else []
    if errors:
        safe_provider_error = str(errors[0]) if errors else "none"
        category = _classify_kraken_error([str(item) for item in errors])
        print("success=false")
        print(f"http_status={status}")
        print(f"kraken_error_category={category}")
        print(f"safe_provider_error={safe_provider_error}")
        print("authentication_succeeded=false")
        return 1

    print("success=true")
    print(f"http_status={status}")
    print("kraken_error_category=none")
    print("safe_provider_error=none")
    print("authentication_succeeded=true")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent read-only Kraken Balance authentication verifier")
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(timeout_seconds=float(args.timeout_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
