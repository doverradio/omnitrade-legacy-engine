from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import verify_kraken_balance_auth as verifier


def _settings(*, api_key: str | None, api_secret: str | None, otp: str | None = None):
    def _secret(value: str | None):
        if value is None:
            return None
        return SimpleNamespace(get_secret_value=lambda: value)

    return SimpleNamespace(
        kraken_api_key=_secret(api_key),
        kraken_api_secret=_secret(api_secret),
        kraken_otp=_secret(otp),
    )


@pytest.mark.asyncio
async def test_load_credentials_from_application_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_settings", lambda: _settings(api_key="k", api_secret="s", otp=""))

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert error is None
    assert credentials is not None
    assert credentials["api_key"] == "k"
    assert credentials["api_secret"] == "s"
    assert diagnostics["credential_configuration_loaded"] is True
    assert diagnostics["kraken_api_key_configured"] is True
    assert diagnostics["kraken_api_secret_configured"] is True
    assert diagnostics["credential_source"] == "application_settings"


@pytest.mark.asyncio
async def test_load_credentials_uses_dotenv_fallback_when_get_settings_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_get_settings():
        raise RuntimeError("settings cache unavailable")

    monkeypatch.setattr(verifier, "get_settings", _fail_get_settings)
    monkeypatch.setattr(verifier, "Settings", lambda **_kwargs: _settings(api_key="k", api_secret="s", otp=""))

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert error is None
    assert credentials is not None
    assert diagnostics["credential_configuration_loaded"] is True
    assert diagnostics["credential_source"] == "application_settings"


@pytest.mark.asyncio
async def test_missing_api_key_has_precise_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_settings", lambda: _settings(api_key=None, api_secret="s", otp=None))

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert credentials is None
    assert error == "api_key_missing"
    assert diagnostics["kraken_api_key_configured"] is False
    assert diagnostics["kraken_api_secret_configured"] is True


@pytest.mark.asyncio
async def test_missing_secret_has_precise_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_settings", lambda: _settings(api_key="k", api_secret=None, otp=None))

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert credentials is None
    assert error == "api_secret_missing"
    assert diagnostics["kraken_api_key_configured"] is True
    assert diagnostics["kraken_api_secret_configured"] is False


@pytest.mark.asyncio
async def test_both_missing_have_precise_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_settings", lambda: _settings(api_key=None, api_secret=None, otp=None))

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert credentials is None
    assert error == "both_credentials_missing"
    assert diagnostics["kraken_api_key_configured"] is False
    assert diagnostics["kraken_api_secret_configured"] is False


@pytest.mark.asyncio
async def test_configuration_error_has_precise_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_get_settings():
        raise RuntimeError("missing configuration")

    def _fail_settings(**_kwargs):
        raise RuntimeError("dotenv load failed")

    monkeypatch.setattr(verifier, "get_settings", _fail_get_settings)
    monkeypatch.setattr(verifier, "Settings", _fail_settings)

    credentials, diagnostics, error = await verifier._load_production_credentials()

    assert credentials is None
    assert error == "configuration_not_loaded"
    assert diagnostics["credential_configuration_loaded"] is False


@pytest.mark.asyncio
async def test_verifier_success_reaches_balance_request_with_safe_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(verifier, "get_exchange_provider", lambda _name: object())

    async def _loader():
        return (
            {"api_key": "public-key", "api_secret": "secret-b64", "passphrase": ""},
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

    monkeypatch.setattr(verifier, "_load_production_credentials", _loader)
    monkeypatch.setattr(verifier, "_independent_signature", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            assert str(base_url) == "https://api.kraken.com"
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, path, content, headers):
            assert path == "/0/private/Balance"
            assert "AddOrder" not in path
            assert "nonce=" in str(content)
            assert headers.get("Content-Type") == "application/x-www-form-urlencoded"
            return _FakeResponse()

    monkeypatch.setattr(verifier.httpx, "AsyncClient", _FakeAsyncClient)

    result = await verifier._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 0
    assert "success=true" in out
    assert "http_status=200" in out
    assert "kraken_error_category=none" in out
    assert "safe_provider_error=none" in out
    assert "authentication_succeeded=true" in out
    assert "credential_configuration_loaded=true" in out
    assert "kraken_api_key_configured=true" in out
    assert "kraken_api_secret_configured=true" in out
    assert "credential_source=application_settings" in out
    assert "credentials_loaded=true" in out
    assert "public-key" not in out
    assert "secret-b64" not in out
    assert "signature-value" not in out


@pytest.mark.asyncio
async def test_verifier_invalid_signature_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(verifier, "get_exchange_provider", lambda _name: object())

    async def _loader():
        return (
            {"api_key": "public-key", "api_secret": "secret-b64", "passphrase": ""},
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

    monkeypatch.setattr(verifier, "_load_production_credentials", _loader)
    monkeypatch.setattr(verifier, "_independent_signature", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": ["EAPI:Invalid signature"], "result": {}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            _ = base_url, timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, path, content, headers):
            _ = path, content, headers
            return _FakeResponse()

    monkeypatch.setattr(verifier.httpx, "AsyncClient", _FakeAsyncClient)

    result = await verifier._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=invalid_signature" in out
    assert "safe_provider_error=EAPI:Invalid signature" in out
    assert "authentication_succeeded=false" in out


@pytest.mark.asyncio
async def test_verifier_uses_production_loader_and_not_env_duplicate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(verifier, "get_exchange_provider", lambda _name: object())
    calls = {"loader": 0}

    async def _loader():
        calls["loader"] += 1
        return None, {
            "credential_configuration_loaded": True,
            "kraken_api_key_configured": False,
            "kraken_api_secret_configured": False,
            "credential_source": "application_settings",
            "dotenv_file_loaded": True,
            "credentials_loaded": False,
        }, "both_credentials_missing"

    monkeypatch.setattr(verifier, "_load_production_credentials", _loader)

    result = await verifier._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 2
    assert calls["loader"] == 1
    assert "kraken_error_category=both_credentials_missing" in out
    assert not hasattr(verifier, "_read_env")


@pytest.mark.asyncio
async def test_verifier_provider_initialization_failure_category(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def _raise(_name: str):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(verifier, "get_exchange_provider", _raise)
    result = await verifier._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 2
    assert "kraken_error_category=provider_initialization_failed" in out
    assert "provider_initialized=false" in out


def test_main_runs_with_stubbed_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "parse_args", lambda _argv=None: SimpleNamespace(timeout_seconds=1.0))
    monkeypatch.setattr(verifier, "_run", lambda **_kwargs: 0)
    monkeypatch.setattr(verifier.asyncio, "run", lambda value: value)
    result = verifier.main([])
    assert result == 0
