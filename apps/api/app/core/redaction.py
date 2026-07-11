from __future__ import annotations

import os
from typing import Any, get_args, get_origin
from urllib.parse import quote, unquote, urlsplit

from pydantic import SecretStr

from app.config import Settings, get_settings


REDACTION_TOKEN = "[REDACTED]"
_MIN_SECRET_LENGTH = 8

_EXPLICIT_SECRET_ENV_NAMES: tuple[str, ...] = (
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "COINBASE_PRIVATE_KEY",
    "COINBASE_PASSPHRASE",
    "OT_COINBASE_API_KEY_NAME",
    "OT_COINBASE_PRIVATE_KEY",
    "OT_COINBASE_PASSPHRASE",
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "KRAKEN_OTP",
    "OT_KRAKEN_API_KEY",
    "OT_KRAKEN_API_SECRET",
    "OT_KRAKEN_OTP",
    "EXCHANGE_CREDENTIALS_ENCRYPTION_KEY",
    "DATABASE_URL",
)


def _is_secretstr_annotation(annotation: Any) -> bool:
    if annotation is SecretStr:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(arg is SecretStr for arg in get_args(annotation))


def _field_secret_env_aliases() -> set[str]:
    names: set[str] = set()
    for field in Settings.model_fields.values():
        if not _is_secretstr_annotation(field.annotation):
            continue
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias)
            continue
        if alias is None:
            names.add(field.alias or field.serialization_alias or "")
            continue
        choices = getattr(alias, "choices", None)
        if choices is not None:
            for item in choices:
                if isinstance(item, str):
                    names.add(item)
        alias_name = getattr(alias, "alias", None)
        if isinstance(alias_name, str):
            names.add(alias_name)
    names.discard("")
    return names


def _value_variants(value: str) -> set[str]:
    variants = {value}
    try:
        encoded = quote(value, safe="")
        if encoded:
            variants.add(encoded)
        decoded = unquote(value)
        if decoded:
            variants.add(decoded)
    except Exception:
        pass
    return variants


def _database_password_candidates(database_url: str) -> set[str]:
    values: set[str] = set()
    if not database_url:
        return values
    try:
        parsed = urlsplit(database_url)
        password = parsed.password
        if password:
            values.update(_value_variants(password))
    except Exception:
        return values
    return values


def collect_sensitive_values_for_diagnostics(*, settings: Any | None = None) -> set[str]:
    if settings is None:
        settings = get_settings()

    env_names = set(_EXPLICIT_SECRET_ENV_NAMES)
    env_names.update(_field_secret_env_aliases())

    values: set[str] = set()

    for name in env_names:
        raw = os.getenv(name)
        if raw is None:
            continue
        text = raw.strip()
        if not text:
            continue
        values.update(_value_variants(text))

    database_url = os.getenv("DATABASE_URL")
    if (not database_url) and settings is not None:
        configured = getattr(settings, "database_url", None)
        if configured is not None:
            database_url = str(configured).strip() or None
    if database_url:
        values.update(_database_password_candidates(database_url))

    filtered = {
        value
        for value in values
        if len(value) >= _MIN_SECRET_LENGTH
    }
    return filtered


def redact_message_for_diagnostics(message: str, *, settings: Any | None = None) -> str:
    if not message:
        return ""

    redacted = message
    secret_values = sorted(collect_sensitive_values_for_diagnostics(settings=settings), key=len, reverse=True)
    for value in secret_values:
        redacted = redacted.replace(value, REDACTION_TOKEN)
    return redacted
