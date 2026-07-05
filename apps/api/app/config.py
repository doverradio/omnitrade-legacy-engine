from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"
    supabase_url: str = "http://localhost:54321"
    supabase_service_role_key: SecretStr
    supabase_jwt_secret: SecretStr

    binance_us_api_base: str = "https://api.binance.us"
    alpaca_api_key_id: SecretStr
    alpaca_api_secret_key: SecretStr
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    environment: str = "local"
    log_level: str = "INFO"
    global_kill_switch_default: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
