from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import json
import uuid

import pytest
from cryptography.fernet import Fernet

from app.models.exchange_connection import ExchangeConnection
from app.schemas.exchange_connections import SaveExchangeConnectionRequest, TestExchangeConnectionRequest as ExchangeTestConnectionRequest
from app.services.exchange_connections import service
from app.services.exchange_connections.crypto import decrypt_credential_payload, encrypt_credential_payload
from app.services.exchange_connections.providers.base import ExchangeAuthResult


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        if isinstance(item, ExchangeConnection) and getattr(item, "exchange_connection_id", None) is None:
            item.exchange_connection_id = uuid.uuid4()
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _item: object) -> None:
        return None


@pytest.mark.asyncio
async def test_credential_encryption_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setattr("app.services.exchange_connections.crypto._get_fernet", lambda: Fernet(key))

    raw = '{"api_key":"abc","api_secret":"xyz"}'
    encrypted = encrypt_credential_payload(raw)

    assert encrypted != raw
    assert decrypt_credential_payload(encrypted) == raw


@pytest.mark.asyncio
async def test_credential_retrieval_from_stored_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setattr("app.services.exchange_connections.crypto._get_fernet", lambda: Fernet(key))

    raw = json.dumps({"api_key": "key_12345", "api_secret": "secret_67890", "passphrase": "pass"})
    encrypted = encrypt_credential_payload(raw)

    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        connection_name="Coinbase",
        environment="sandbox",
        status="disconnected",
        credentials_encrypted=encrypted,
        api_key_masked="******2345",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=False,
        api_permissions=[],
        account_status=None,
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=None,
        last_heartbeat_at=None,
        last_api_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    decrypted = service.get_decrypted_credentials_for_connection(connection)

    assert decrypted["api_key"] == "key_12345"
    assert decrypted["api_secret"] == "secret_67890"
    assert decrypted["passphrase"] == "pass"


@pytest.mark.asyncio
async def test_authentication_and_save_masks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setattr("app.services.exchange_connections.crypto._get_fernet", lambda: Fernet(key))

    class _Provider:
        async def test_authentication(self, *, credentials, environment):
            _ = (credentials, environment)
            return ExchangeAuthResult(
                reachable=True,
                authenticated=True,
                account_status="active",
                permissions=["view", "trade"],
                heartbeat_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                error=None,
            )

    monkeypatch.setattr(service, "get_exchange_provider", lambda _provider: _Provider())

    db = _FakeDb()
    response = await service.create_exchange_connection(
        db=db,
        payload=SaveExchangeConnectionRequest(
            provider="coinbase_advanced",
            connection_name="Primary Coinbase",
            environment="sandbox",
            api_key_name="api-key-1234",
            private_key="api-secret-value",
            passphrase="secret-passphrase",
        ),
    )

    assert response.status == "connected"
    assert response.credentials_valid is True
    assert response.credential_mask.api_key_name.endswith("1234")
    assert response.credential_mask.private_key == "********"
    assert response.credential_mask.passphrase == "********"

    stored_connection = next(item for item in db.added if isinstance(item, ExchangeConnection))
    assert "api-secret-value" not in stored_connection.credentials_encrypted


@pytest.mark.asyncio
async def test_test_exchange_credentials_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _Provider:
        async def test_authentication(self, *, credentials, environment):
            captured["credentials"] = credentials
            captured["environment"] = environment
            return ExchangeAuthResult(
                reachable=True,
                authenticated=True,
                account_status="active",
                permissions=["view"],
                heartbeat_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                error=None,
            )

    monkeypatch.setattr(service, "get_exchange_provider", lambda _provider: _Provider())

    response = await service.test_exchange_credentials(
        payload=ExchangeTestConnectionRequest(
            provider="coinbase_advanced",
            environment="production",
            api_key_name="k",
            private_key="s",
            passphrase="p",
        )
    )

    assert response.authenticated is True
    assert response.permissions == ["view"]
    assert captured["environment"] == "production"
    assert captured["credentials"]["api_key"] == "k"
