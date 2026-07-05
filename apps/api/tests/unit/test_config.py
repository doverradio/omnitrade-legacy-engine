from __future__ import annotations

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
