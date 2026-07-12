from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_kraken_balance_auth_cleanroom as script


def _source_text() -> str:
    return Path(script.__file__).read_text()


def test_cleanroom_script_imports_are_isolated() -> None:
    source = _source_text()
    module = ast.parse(source)

    forbidden_imports = {
        "app.services.exchange_connections.providers.kraken_spot",
        "scripts.verify_kraken_balance_auth",
        "scripts.initialize_live_crypto_environment",
        "app.services.exchange_connections.service",
        "app.services.exchange_connections.readiness",
        "app.services.exchange_connections.crypto",
        "app.db.session",
        "sqlalchemy",
    }

    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module not in forbidden_imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_imports


def test_cleanroom_script_has_no_add_order_or_feature_flag_mutation() -> None:
    source = _source_text()

    assert "AddOrder" not in source
    assert "LIVE_CRYPTO_" not in source
    assert "GLOBAL_KILL_SWITCH" not in source
    assert "os.environ" not in source


def test_fixed_input_signature_vector_matches_official_formula() -> None:
    signature = script._build_api_sign(
        request_path="/0/private/Balance",
        nonce="1700000000000",
        body="nonce=1700000000000",
        api_secret_b64="c2VjcmV0LWtleS1mb3ItdGVzdHM=",
    )

    assert signature == "evMsW5Z70wUSyZGt+QrxAqNtxwN8whuUI1f4xid8i4P30bP7/fBzym/Gh3F0TA6nMklfLNJ7Ni0cBERRexRRmw=="


def test_successful_mocked_balance_response(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    calls = {"count": 0, "body": None, "headers": None}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            calls["count"] += 1
            calls["body"] = content
            calls["headers"] = headers
            assert endpoint == "/0/private/Balance"
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 0
    assert calls["count"] == 1
    assert calls["body"] == "nonce=1700000000000"
    assert calls["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert calls["headers"]["API-Key"] == "k"
    assert "authentication_succeeded=true" in out
    assert "kraken_error_category=none" in out


@pytest.mark.parametrize(
    ("error_message", "expected_category"),
    [
        ("EAPI:Invalid signature", "invalid_signature"),
        ("EAPI:Invalid key", "invalid_key"),
        ("EAPI:Invalid nonce", "invalid_nonce"),
        ("EGeneral:Permission denied", "permission_denied"),
    ],
)
def test_kraken_error_classification(error_message: str, expected_category: str) -> None:
    assert script._classify_kraken_error([error_message]) == expected_category


def test_invalid_signature_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": ["EAPI:Invalid signature"], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=invalid_signature" in out
    assert "safe_provider_error=EAPI:Invalid signature" in out


def test_invalid_key_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": ["EAPI:Invalid key"], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=invalid_key" in out


def test_invalid_nonce_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": ["EAPI:Invalid nonce"], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=invalid_nonce" in out


def test_permission_denied_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": ["EGeneral:Permission denied"], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=permission_denied" in out


def test_http_failure_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 500

        def json(self):
            raise ValueError("not json")

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=http_error" in out
    assert "safe_provider_error=http_500" in out


def test_transport_timeout_classification(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            raise script.httpx.TimeoutException("timeout")

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 1
    assert "kraken_error_category=transport_timeout" in out


def test_no_secret_signature_nonce_or_body_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "public-key", "api_secret": "secret-value"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)
    out = capsys.readouterr().out

    assert result == 0
    assert "public-key" not in out
    assert "secret-value" not in out
    assert "signature-value" not in out
    assert "nonce=" not in out
    assert "request_body" not in out


def test_one_http_request_only_and_no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "_load_kraken_credentials", lambda: ({"api_key": "k", "api_secret": "s"}, True, True, True))
    monkeypatch.setattr(script, "_build_api_sign", lambda **_kwargs: "signature-value")

    calls = {"count": 0}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"error": [], "result": {}}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, content, headers):
            calls["count"] += 1
            return _FakeResponse()

    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(script.time, "perf_counter", lambda: 100.0)

    result = script._run(timeout_seconds=1.0)

    assert result == 0
    assert calls["count"] == 1


def test_main_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def _parse_args(_argv=None):
        return SimpleNamespace(timeout_seconds=1.0)

    def _run(*, timeout_seconds: float) -> int:
        captured["called"] = True
        captured["timeout_seconds"] = timeout_seconds
        return 0

    monkeypatch.setattr(script, "parse_args", _parse_args)
    monkeypatch.setattr(script, "_run", _run)

    result = script.main([])

    assert result == 0
    assert captured["called"] is True
    assert captured["timeout_seconds"] == 1.0
