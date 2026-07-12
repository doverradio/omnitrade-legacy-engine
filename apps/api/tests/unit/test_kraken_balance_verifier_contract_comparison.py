from __future__ import annotations

import httpx
import pytest

from app.services.exchange_connections.providers.kraken_spot import build_kraken_signature_from_encoded_payload
from scripts import verify_kraken_balance_auth as existing
from scripts import verify_kraken_balance_auth_cleanroom as cleanroom


def _first_differing_stage(stage_matches: dict[str, bool], order: list[str]) -> str | None:
    for stage in order:
        if not stage_matches.get(stage, False):
            return stage
    return None


def test_fixed_input_signature_stages_match_between_cleanroom_and_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_b64 = "c2VjcmV0LWtleS1mb3ItdGVzdHM="
    nonce = "1700000000000"
    path = "/0/private/Balance"
    body = "nonce=1700000000000"

    clean: dict[str, object] = {"normalized_secret_input": None}
    existing_stages: dict[str, object] = {"normalized_secret_input": None}

    clean_b64decode = cleanroom.base64.b64decode
    clean_sha256 = cleanroom.hashlib.sha256
    clean_hmac_new = cleanroom.hmac.new
    clean_b64encode = cleanroom.base64.b64encode

    existing_b64decode = existing.base64.standard_b64decode
    existing_sha256 = existing.hashlib.sha256
    existing_hmac_new = existing.hmac.new
    existing_b64encode = existing.base64.standard_b64encode

    def _clean_decode(raw, *args, **kwargs):
        clean["normalized_secret_input"] = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        decoded = clean_b64decode(raw, *args, **kwargs)
        clean["decoded_secret_bytes"] = bytes(decoded)
        return decoded

    def _existing_decode(raw, *args, **kwargs):
        existing_stages["normalized_secret_input"] = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        decoded = existing_b64decode(raw, *args, **kwargs)
        existing_stages["decoded_secret_bytes"] = bytes(decoded)
        return decoded

    def _clean_sha(data=b""):
        clean["sha256_preimage"] = bytes(data)
        digest_impl = clean_sha256(data)

        class _Wrap:
            def digest(self):
                digest = digest_impl.digest()
                clean["sha256_digest"] = digest
                return digest

        return _Wrap()

    def _existing_sha(data=b""):
        existing_stages["sha256_preimage"] = bytes(data)
        digest_impl = existing_sha256(data)

        class _Wrap:
            def digest(self):
                digest = digest_impl.digest()
                existing_stages["sha256_digest"] = digest
                return digest

        return _Wrap()

    def _clean_hmac(key, msg=None, digestmod=""):
        clean["hmac_key"] = bytes(key)
        clean["hmac_message"] = b"" if msg is None else bytes(msg)
        hmac_impl = clean_hmac_new(key, msg, digestmod)

        class _Wrap:
            def digest(self):
                digest = hmac_impl.digest()
                clean["hmac_digest"] = digest
                return digest

        return _Wrap()

    def _existing_hmac(key, msg=None, digestmod=""):
        existing_stages["hmac_key"] = bytes(key)
        existing_stages["hmac_message"] = b"" if msg is None else bytes(msg)
        hmac_impl = existing_hmac_new(key, msg, digestmod)

        class _Wrap:
            def digest(self):
                digest = hmac_impl.digest()
                existing_stages["hmac_digest"] = digest
                return digest

        return _Wrap()

    def _clean_encode(raw):
        clean["hmac_digest_for_base64"] = bytes(raw)
        encoded = clean_b64encode(raw)
        clean["final_api_sign"] = encoded.decode("utf-8")
        return encoded

    def _existing_encode(raw):
        existing_stages["hmac_digest_for_base64"] = bytes(raw)
        encoded = existing_b64encode(raw)
        existing_stages["final_api_sign"] = encoded.decode("utf-8")
        return encoded

    with monkeypatch.context() as clean_ctx:
        clean_ctx.setattr(cleanroom.base64, "b64decode", _clean_decode)
        clean_ctx.setattr(cleanroom.hashlib, "sha256", _clean_sha)
        clean_ctx.setattr(cleanroom.hmac, "new", _clean_hmac)
        clean_ctx.setattr(cleanroom.base64, "b64encode", _clean_encode)
        clean_signature = cleanroom._build_api_sign(
            request_path=path,
            nonce=nonce,
            body=body,
            api_secret_b64=secret_b64,
        )

    with monkeypatch.context() as existing_ctx:
        existing_ctx.setattr(existing.base64, "standard_b64decode", _existing_decode)
        existing_ctx.setattr(existing.hashlib, "sha256", _existing_sha)
        existing_ctx.setattr(existing.hmac, "new", _existing_hmac)
        existing_ctx.setattr(existing.base64, "standard_b64encode", _existing_encode)
        existing_signature = existing._independent_signature(
            url_path=path,
            nonce=nonce,
            encoded_body=body,
            secret_b64=secret_b64,
        )

    clean["nonce_text"] = nonce
    existing_stages["nonce_text"] = nonce
    clean["serialized_body_text"] = body
    existing_stages["serialized_body_text"] = body
    clean["serialized_body_bytes"] = body.encode("utf-8")
    existing_stages["serialized_body_bytes"] = body.encode("utf-8")

    stage_matches = {
        "normalized_secret_input": clean["normalized_secret_input"] == existing_stages["normalized_secret_input"],
        "decoded_secret_bytes": clean["decoded_secret_bytes"] == existing_stages["decoded_secret_bytes"],
        "nonce_text": clean["nonce_text"] == existing_stages["nonce_text"],
        "serialized_body_text": clean["serialized_body_text"] == existing_stages["serialized_body_text"],
        "serialized_body_bytes": clean["serialized_body_bytes"] == existing_stages["serialized_body_bytes"],
        "sha256_preimage": clean["sha256_preimage"] == existing_stages["sha256_preimage"],
        "sha256_digest": clean["sha256_digest"] == existing_stages["sha256_digest"],
        "hmac_message": clean["hmac_message"] == existing_stages["hmac_message"],
        "hmac_digest": clean["hmac_digest"] == existing_stages["hmac_digest"],
        "final_api_sign": clean_signature == existing_signature,
    }
    first_differing_stage = _first_differing_stage(
        stage_matches,
        [
            "normalized_secret_input",
            "decoded_secret_bytes",
            "nonce_text",
            "serialized_body_text",
            "serialized_body_bytes",
            "sha256_preimage",
            "sha256_digest",
            "hmac_message",
            "hmac_digest",
            "final_api_sign",
        ],
    )

    assert clean_signature == existing_signature
    assert all(stage_matches.values())
    assert first_differing_stage is None


@pytest.mark.asyncio
async def test_fixed_input_prepared_request_matches_between_cleanroom_and_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, dict[str, object]] = {}

    class _CleanResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

    class _ExistingResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

    class _CleanClient:
        def __init__(self, *, base_url, timeout, follow_redirects=False):
            self.base_url = str(base_url)
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, path, content, headers):
            request = httpx.Request("POST", self.base_url + str(path), content=content, headers=headers)
            captured["cleanroom"] = {
                "request_count": 1,
                "prepared_method": request.method,
                "prepared_url_path": request.url.path,
                "prepared_query": request.url.query.decode("utf-8") if isinstance(request.url.query, bytes) else str(request.url.query),
                "prepared_content_type": request.headers.get("Content-Type"),
                "prepared_body_bytes": bytes(request.content),
                "api_sign_header_present": bool(request.headers.get("API-Sign")),
                "follow_redirects": self.follow_redirects,
            }
            return _CleanResponse()

    class _ExistingClient:
        def __init__(self, *, base_url, timeout):
            self.base_url = str(base_url)
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, path, content, headers):
            request = httpx.Request("POST", self.base_url + str(path), content=content, headers=headers)
            captured["existing"] = {
                "request_count": 1,
                "prepared_method": request.method,
                "prepared_url_path": request.url.path,
                "prepared_query": request.url.query.decode("utf-8") if isinstance(request.url.query, bytes) else str(request.url.query),
                "prepared_content_type": request.headers.get("Content-Type"),
                "prepared_body_bytes": bytes(request.content),
                "api_sign_header_present": bool(request.headers.get("API-Sign")),
            }
            return _ExistingResponse()

    monkeypatch.setattr(cleanroom.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(cleanroom.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(cleanroom, "_load_kraken_credentials", lambda: ({"api_key": "public", "api_secret": "c2VjcmV0LWtleS1mb3ItdGVzdHM="}, True, True, True))
    monkeypatch.setattr(cleanroom.httpx, "Client", _CleanClient)

    monkeypatch.setattr(existing.time, "time", lambda: 1700000000.0)

    async def _load_existing():
        return (
            {"api_key": "public", "api_secret": "c2VjcmV0LWtleS1mb3ItdGVzdHM=", "passphrase": "SHOULD_NOT_BE_USED"},
            {
                "credential_configuration_loaded": True,
                "kraken_api_key_configured": True,
                "kraken_api_secret_configured": True,
                "credential_source": "application_settings",
                "dotenv_file_loaded": True,
                "credentials_loaded": True,
            },
            None,
        )

    monkeypatch.setattr(existing, "_load_production_credentials", _load_existing)
    monkeypatch.setattr(existing.httpx, "AsyncClient", _ExistingClient)

    assert cleanroom._run(timeout_seconds=1.0) == 0
    assert await existing._run(timeout_seconds=1.0) == 0

    stage_matches = {
        "prepared_method": captured["cleanroom"]["prepared_method"] == captured["existing"]["prepared_method"],
        "prepared_url_path": captured["cleanroom"]["prepared_url_path"] == captured["existing"]["prepared_url_path"],
        "prepared_query": captured["cleanroom"]["prepared_query"] == captured["existing"]["prepared_query"],
        "prepared_content_type": captured["cleanroom"]["prepared_content_type"] == captured["existing"]["prepared_content_type"],
        "prepared_body_bytes": captured["cleanroom"]["prepared_body_bytes"] == captured["existing"]["prepared_body_bytes"],
        "api_sign_header_present": captured["cleanroom"]["api_sign_header_present"] == captured["existing"]["api_sign_header_present"],
    }
    first_differing_stage = _first_differing_stage(
        stage_matches,
        [
            "prepared_method",
            "prepared_url_path",
            "prepared_query",
            "prepared_content_type",
            "prepared_body_bytes",
            "api_sign_header_present",
        ],
    )

    assert captured["cleanroom"]["request_count"] == 1
    assert captured["existing"]["request_count"] == 1
    assert captured["cleanroom"]["follow_redirects"] is False
    assert b"otp=" not in captured["existing"]["prepared_body_bytes"]
    assert b"nonce=" in captured["existing"]["prepared_body_bytes"]
    assert all(stage_matches.values())
    assert first_differing_stage is None


def test_provider_signature_helper_matches_cleanroom_signature_contract() -> None:
    secret_b64 = "c2VjcmV0LWtleS1mb3ItdGVzdHM="
    nonce = "1700000000000"
    path = "/0/private/Balance"
    body = "nonce=1700000000000"

    clean_signature = cleanroom._build_api_sign(
        request_path=path,
        nonce=nonce,
        body=body,
        api_secret_b64=secret_b64,
    )
    provider_signature = build_kraken_signature_from_encoded_payload(
        url_path=path,
        nonce=nonce,
        encoded_payload=body,
        secret_b64=secret_b64,
    )

    assert provider_signature == clean_signature
