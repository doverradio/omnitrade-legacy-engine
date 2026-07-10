from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.errors import InvalidRequestError


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.exchange_credentials_encryption_key
    if key is None or not key.get_secret_value().strip():
        raise InvalidRequestError(
            message="Exchange credentials encryption key is not configured",
            details={"setting": "EXCHANGE_CREDENTIALS_ENCRYPTION_KEY"},
        )

    try:
        return Fernet(key.get_secret_value().strip().encode("utf-8"))
    except Exception as exc:
        raise InvalidRequestError(
            message="Invalid exchange credentials encryption key",
            details={"setting": "EXCHANGE_CREDENTIALS_ENCRYPTION_KEY"},
        ) from exc


def encrypt_credential_payload(raw: str) -> str:
    fernet = _get_fernet()
    return fernet.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_credential_payload(token: str) -> str:
    fernet = _get_fernet()
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise InvalidRequestError(message="Stored exchange credentials could not be decrypted", details={}) from exc
