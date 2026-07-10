from functools import lru_cache
import json
from decimal import Decimal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"
    supabase_url: str = "http://localhost:54321"
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None

    binance_us_api_base: str = "https://api.binance.us"
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    exchange_credentials_encryption_key: SecretStr | None = None
    crypto_preview_max_quote_size_usd: Decimal = Decimal("25")
    crypto_preview_default_quote_size_usd: Decimal = Decimal("5")
    crypto_preview_allowed_products: str = "BTC-USD"
    crypto_preview_market_data_max_age_minutes: int = 15
    crypto_preview_expiration_minutes: int = 5
    crypto_preview_idempotency_window_minutes: int = 5
    live_crypto_order_submission_enabled: bool = False
    live_crypto_max_order_usd: Decimal = Decimal("5")
    live_crypto_preparation_enabled: bool = False
    live_crypto_confirmation_challenge_minutes: int = 1
    live_crypto_preview_max_age_seconds: int = 30
    live_crypto_balance_max_age_seconds: int = 30
    live_crypto_readiness_max_age_seconds: int = 60
    live_crypto_price_max_age_seconds: int = 30

    environment: str = "local"
    log_level: str = "INFO"
    global_kill_switch_default: bool = False
    cors_allowed_origins: str = "http://localhost:3000,https://app.bigdeal.sale"

    @property
    def parsed_cors_allowed_origins(self) -> list[str]:
        value = (self.cors_allowed_origins or "").strip()
        if not value:
            return []

        if value.startswith("["):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                loaded = []
            if isinstance(loaded, list):
                return [str(item).strip() for item in loaded if str(item).strip()]

        return [origin.strip() for origin in value.split(",") if origin.strip()]

    @property
    def parsed_crypto_preview_allowed_products(self) -> list[str]:
        return [item.strip().upper() for item in self.crypto_preview_allowed_products.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
