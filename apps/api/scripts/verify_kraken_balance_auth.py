from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import os
import time
import urllib.parse

import httpx


def _read_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


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


async def _run(*, timeout_seconds: float) -> int:
    api_key = _read_env(("KRAKEN_API_KEY", "OT_KRAKEN_API_KEY"))
    api_secret = _read_env(("KRAKEN_API_SECRET", "OT_KRAKEN_API_SECRET"))
    otp = _read_env(("KRAKEN_OTP", "OT_KRAKEN_OTP"))

    if not api_key or not api_secret:
        print("success=false")
        print("http_status=0")
        print("kraken_error_category=missing_credentials")
        print("authentication_succeeded=false")
        return 2

    nonce = str(int(time.time() * 1000))
    payload: dict[str, str] = {"nonce": nonce}
    if otp:
        payload["otp"] = otp

    encoded_body = urllib.parse.urlencode(payload)
    request_path = "/0/private/Balance"
    signature = _independent_signature(
        url_path=request_path,
        nonce=nonce,
        encoded_body=encoded_body,
        secret_b64=api_secret,
    )

    headers = {
        "API-Key": api_key,
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
        print("kraken_error_category=transport_error")
        print("authentication_succeeded=false")
        return 1

    status = int(response.status_code)
    try:
        payload_json = response.json()
    except ValueError:
        print("success=false")
        print(f"http_status={status}")
        print("kraken_error_category=invalid_json")
        print("authentication_succeeded=false")
        return 1

    errors = payload_json.get("error") if isinstance(payload_json, dict) and isinstance(payload_json.get("error"), list) else []
    if errors:
        category = _classify_kraken_error([str(item) for item in errors])
        print("success=false")
        print(f"http_status={status}")
        print(f"kraken_error_category={category}")
        print("authentication_succeeded=false")
        return 1

    print("success=true")
    print(f"http_status={status}")
    print("kraken_error_category=none")
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
