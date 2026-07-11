from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.exchange_connections.crypto import decrypt_credential_payload, encrypt_credential_payload
from scripts import rotate_exchange_connection_credentials as script


class _SessionCtx:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.committed = False
        self.rolled_back = False

    def add(self, item) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, _item) -> None:
        return None


def _connection(*, provider: str = "kraken_spot", environment: str = "production"):
    return SimpleNamespace(
        exchange_connection_id=uuid4(),
        provider=provider,
        environment=environment,
        connection_name="kraken-prod",
        created_at=datetime.now(timezone.utc),
        credentials_encrypted=encrypt_credential_payload('{"api_key_name":"old-key","private_key":"old-secret","passphrase":""}'),
        api_key_masked="***-old",
        api_secret_masked="********",
        passphrase_configured=False,
    )


@pytest.mark.asyncio
async def test_rotation_success_single_match_preserves_connection_id_encrypts_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = _FakeDb()
    conn = _connection()

    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("new-key", "new-secret", None))

    async def _matches(**_kwargs):
        return [conn]

    monkeypatch.setattr(script, "_find_matching_connections", _matches)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )

    out = capsys.readouterr().out
    decrypted = decrypt_credential_payload(conn.credentials_encrypted)

    assert result == 0
    assert db.committed is True
    assert conn.exchange_connection_id is not None
    assert "new-secret" not in out
    assert "new-key" not in out
    assert conn.credentials_encrypted != '{"api_key_name":"new-key","private_key":"new-secret","passphrase":""}'
    assert '"private_key": "new-secret"' in decrypted
    assert "credentials_rotated=true" in out
    assert "audit_recorded=true" in out


@pytest.mark.asyncio
async def test_rotation_zero_matches_fails_closed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = _FakeDb()
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("k", "s", None))

    async def _matches(**_kwargs):
        return []

    monkeypatch.setattr(script, "_find_matching_connections", _matches)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )
    out = capsys.readouterr().out

    assert result == 2
    assert "safe_failure_category=missing_exchange_connection" in out


@pytest.mark.asyncio
async def test_rotation_multiple_matches_fails_closed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = _FakeDb()
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("k", "s", None))

    async def _matches(**_kwargs):
        return [_connection(), _connection()]

    monkeypatch.setattr(script, "_find_matching_connections", _matches)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )
    out = capsys.readouterr().out

    assert result == 2
    assert "safe_failure_category=multiple_exchange_connections" in out


@pytest.mark.asyncio
async def test_rotation_requires_confirmation(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=False)
    )
    out = capsys.readouterr().out

    assert result == 2
    assert "safe_failure_category=confirmation_required" in out


@pytest.mark.asyncio
async def test_rotation_requires_actor(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="", confirm_replace=True)
    )
    out = capsys.readouterr().out

    assert result == 2
    assert "safe_failure_category=actor_required" in out


@pytest.mark.asyncio
async def test_rotation_is_idempotent_when_already_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = _FakeDb()
    conn = _connection()
    conn.credentials_encrypted = encrypt_credential_payload('{"api_key_name":"new-key","private_key":"new-secret","passphrase":""}')

    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("new-key", "new-secret", None))

    async def _matches(**_kwargs):
        return [conn]

    monkeypatch.setattr(script, "_find_matching_connections", _matches)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )
    out = capsys.readouterr().out

    assert result == 0
    assert "credentials_rotated=false" in out
    assert "credentials_already_current=true" in out
    assert db.committed is True


@pytest.mark.asyncio
async def test_rotation_rolls_back_on_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = _FakeDb()
    conn = _connection()

    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("new-key", "new-secret", None))

    async def _matches(**_kwargs):
        return [conn]

    async def _record_fail(**_kwargs):
        raise RuntimeError("audit failure")

    monkeypatch.setattr(script, "_find_matching_connections", _matches)
    monkeypatch.setattr(script, "_record_rotation_audit", _record_fail)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )
    out = capsys.readouterr().out

    assert result == 1
    assert db.rolled_back is True
    assert "safe_failure_category=rotation_failed" in out


@pytest.mark.asyncio
async def test_coinbase_connection_untouched(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = _FakeDb()
    kraken = _connection(provider="kraken_spot")
    coinbase = _connection(provider="coinbase_advanced")
    original_coinbase_encrypted = coinbase.credentials_encrypted

    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(script, "_load_credentials_from_settings", lambda _provider: ("new-key", "new-secret", None))

    async def _matches(**_kwargs):
        return [kraken]

    monkeypatch.setattr(script, "_find_matching_connections", _matches)

    result = await script._run(
        SimpleNamespace(provider="kraken_spot", environment="production", actor="operator:human", confirm_replace=True)
    )
    _ = capsys.readouterr()

    assert result == 0
    assert coinbase.credentials_encrypted == original_coinbase_encrypted


def test_parse_args_requires_confirmation_flag() -> None:
    args = script.parse_args([
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--actor",
        "operator:human",
        "--confirm-replace",
    ])
    assert args.confirm_replace is True
