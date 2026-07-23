from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.config import DEFAULT_ENV_FILE, Settings, get_settings


def test_settings_allow_missing_future_phase_integration_credentials(monkeypatch) -> None:
    for key in [
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.supabase_service_role_key is None
    assert settings.supabase_jwt_secret is None
    assert settings.alpaca_api_key_id is None
    assert settings.alpaca_api_secret_key is None
    assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"


def test_get_settings_uses_cached_shared_bootstrap(monkeypatch) -> None:
    for key in [
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.database_url.startswith("postgresql+asyncpg://")

    get_settings.cache_clear()


def test_settings_env_file_is_backend_relative_and_not_cwd_relative(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert DEFAULT_ENV_FILE == Path(__file__).resolve().parents[2] / ".env"
    assert DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config.get("env_file") == DEFAULT_ENV_FILE


def test_live_crypto_settings_load_from_explicit_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CRYPTO_MAX_ORDER_USD", "7.50")
    monkeypatch.setenv("LIVE_CRYPTO_PREPARATION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CRYPTO_CONFIRMATION_CHALLENGE_MINUTES", "3")
    monkeypatch.setenv("LIVE_CRYPTO_PREVIEW_MAX_AGE_SECONDS", "45")
    monkeypatch.setenv("LIVE_CRYPTO_BALANCE_MAX_AGE_SECONDS", "50")
    monkeypatch.setenv("LIVE_CRYPTO_READINESS_MAX_AGE_SECONDS", "70")
    monkeypatch.setenv("LIVE_CRYPTO_PRICE_MAX_AGE_SECONDS", "80")

    settings = Settings(_env_file=None)

    assert settings.live_crypto_order_submission_enabled is True
    assert settings.live_crypto_max_order_usd == Decimal("7.50")
    assert settings.live_crypto_preparation_enabled is True
    assert settings.live_crypto_confirmation_challenge_minutes == 3
    assert settings.live_crypto_preview_max_age_seconds == 45
    assert settings.live_crypto_balance_max_age_seconds == 50
    assert settings.live_crypto_readiness_max_age_seconds == 70
    assert settings.live_crypto_price_max_age_seconds == 80


def test_automatic_mandate_package_activation_is_independent_and_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED", raising=False)
    monkeypatch.setenv("LIVE_CRYPTO_PREPARATION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.automatic_mandate_package_activation_enabled is False

    monkeypatch.setenv("AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED", "true")
    monkeypatch.setenv("LIVE_CRYPTO_PREPARATION_ENABLED", "false")
    monkeypatch.setenv("LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED", "false")
    settings = Settings(_env_file=None)
    assert settings.automatic_mandate_package_activation_enabled is True
    assert settings.live_crypto_preparation_enabled is False
    assert settings.live_crypto_order_submission_enabled is False
