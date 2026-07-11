from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.redaction import REDACTION_TOKEN, redact_message_for_diagnostics


def test_redacts_ot_coinbase_private_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_PRIVATE_KEY", "super-secret-private-key")
    message = "operation failed with super-secret-private-key"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "super-secret-private-key" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_canonical_coinbase_secret_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COINBASE_PRIVATE_KEY", "coinbase-canonical-private-secret")
    message = "provider error coinbase-canonical-private-secret"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "coinbase-canonical-private-secret" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_kraken_api_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAKEN_API_SECRET", "kraken-secret-value-123")
    message = "kraken failure kraken-secret-value-123"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "kraken-secret-value-123" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_legacy_kraken_api_secret_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_KRAKEN_API_SECRET", "legacy-kraken-secret-value-321")
    message = "kraken failure legacy-kraken-secret-value-321"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "legacy-kraken-secret-value-321" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_exchange_credentials_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_ENCRYPTION_KEY", "encryption-key-value-abcdef")
    message = "invalid exchange key encryption-key-value-abcdef"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "encryption-key-value-abcdef" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_database_url_password_and_embedded_url(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql+asyncpg://postgres:db-password-123@localhost:5432/omnitrade"
    monkeypatch.setenv("DATABASE_URL", database_url)
    message = f"connect failed using {database_url}"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=database_url))

    assert "db-password-123" not in redacted
    assert REDACTION_TOKEN in redacted


def test_redacts_multiple_secrets_in_single_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_PRIVATE_KEY", "coinbase-private-secret-aaa")
    monkeypatch.setenv("KRAKEN_API_SECRET", "kraken-secret-bbb")
    message = "both secrets coinbase-private-secret-aaa and kraken-secret-bbb are present"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert "coinbase-private-secret-aaa" not in redacted
    assert "kraken-secret-bbb" not in redacted
    assert redacted.count(REDACTION_TOKEN) >= 2


def test_ignores_blank_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_PRIVATE_KEY", "")
    message = "safe error message without secrets"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert redacted == message


def test_safe_non_secret_message_remains_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OT_COINBASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    message = "Active crypto paper account is required before initialization can continue"

    redacted = redact_message_for_diagnostics(message, settings=SimpleNamespace(database_url=""))

    assert redacted == message
