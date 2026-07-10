from __future__ import annotations

from decimal import Decimal

from app.config import Settings, get_settings


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


def test_get_settings_still_loads_phase_1_defaults_without_optional_credentials(
    monkeypatch, tmp_path
) -> None:
    for key in [
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.binance_us_api_base == "https://api.binance.us"
    assert settings.supabase_jwt_secret is None
    assert settings.alpaca_api_key_id is None

    get_settings.cache_clear()


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
