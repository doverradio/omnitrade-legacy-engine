from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import verify_kraken_balance_auth as verifier


@pytest.mark.asyncio
async def test_verifier_success_classifies_authenticated_without_secret_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("KRAKEN_API_KEY", "public-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "secret-b64")
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
            assert headers.get("Content-Type") == "application/x-www-form-urlencoded"
            assert "nonce=" in str(content)
            return _FakeResponse()

    monkeypatch.setattr(verifier.httpx, "AsyncClient", _FakeAsyncClient)

    result = await verifier._run(timeout_seconds=1.0)

    out = capsys.readouterr().out
    assert result == 0
    assert "success=true" in out
    assert "http_status=200" in out
    assert "kraken_error_category=none" in out
    assert "authentication_succeeded=true" in out
    assert "public-key" not in out
    assert "signature-value" not in out
    assert "nonce=" not in out


@pytest.mark.asyncio
async def test_verifier_invalid_signature_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("KRAKEN_API_KEY", "public-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "secret-b64")
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
    assert "authentication_succeeded=false" in out


@pytest.mark.asyncio
async def test_verifier_no_db_writes_no_feature_flag_mutation(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("KRAKEN_API_KEY", "public-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "secret-b64")
    monkeypatch.setattr(verifier, "_independent_signature", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

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

    # Script is intentionally env-only and should not mutate settings/db state.
    assert not hasattr(verifier, "AsyncSessionLocal")
    assert not hasattr(verifier, "get_settings")

    result = await verifier._run(timeout_seconds=1.0)
    _ = capsys.readouterr()
    assert result == 0


def test_parse_args_defaults() -> None:
    args = verifier.parse_args([])
    assert args.timeout_seconds == 12.0


def test_main_runs_with_stubbed_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "parse_args", lambda _argv=None: SimpleNamespace(timeout_seconds=1.0))
    monkeypatch.setattr(verifier, "_run", lambda **_kwargs: 0)
    monkeypatch.setattr(verifier.asyncio, "run", lambda value: value)

    result = verifier.main([])
    assert result == 0
