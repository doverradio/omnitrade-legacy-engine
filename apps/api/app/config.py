from functools import lru_cache
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=DEFAULT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"
    database_pool_size: int = Field(default=10, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=20, validation_alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: int = Field(default=30, validation_alias="DATABASE_POOL_TIMEOUT_SECONDS")
    database_pool_recycle_seconds: int = Field(default=1800, validation_alias="DATABASE_POOL_RECYCLE_SECONDS")
    database_connect_timeout_seconds: int = Field(default=5, validation_alias="DATABASE_CONNECT_TIMEOUT_SECONDS")
    database_command_timeout_seconds: int = Field(default=10, validation_alias="DATABASE_COMMAND_TIMEOUT_SECONDS")
    operator_db_timeout_seconds: int = Field(default=20, validation_alias="OPERATOR_DB_TIMEOUT_SECONDS")
    supabase_url: str = "http://localhost:54321"
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None

    binance_us_api_base: str = "https://api.binance.us"
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    coinbase_api_key_name: SecretStr | None = Field(default=None, validation_alias="OT_COINBASE_API_KEY_NAME")
    coinbase_private_key: SecretStr | None = Field(default=None, validation_alias="OT_COINBASE_PRIVATE_KEY")
    coinbase_passphrase: SecretStr | None = Field(default=None, validation_alias="OT_COINBASE_PASSPHRASE")
    kraken_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KRAKEN_API_KEY", "OT_KRAKEN_API_KEY"),
    )
    kraken_api_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KRAKEN_API_SECRET", "OT_KRAKEN_API_SECRET"),
    )
    kraken_otp: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KRAKEN_OTP", "OT_KRAKEN_OTP"),
    )
    exchange_credentials_encryption_key: SecretStr | None = None
    crypto_preview_max_quote_size_usd: Decimal = Decimal("25")
    crypto_preview_default_quote_size_usd: Decimal = Decimal("5")
    crypto_preview_allowed_products: str = "BTC-USD"
    crypto_preview_market_data_max_age_minutes: int = 15
    crypto_preview_expiration_minutes: int = 5
    crypto_preview_idempotency_window_minutes: int = 5
    live_crypto_order_submission_enabled: bool = Field(
        default=False,
        validation_alias="LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED",
    )
    automatic_mandate_package_activation_enabled: bool = Field(
        default=False,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED",
    )
    automatic_mandate_package_activation_package_id: UUID | None = Field(
        default=None,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID",
    )
    automatic_mandate_package_activation_campaign_id: UUID | None = Field(
        default=None,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID",
    )
    automatic_mandate_package_activation_campaign_version: int | None = Field(
        default=None,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_VERSION",
    )
    automatic_mandate_package_activation_mandate_id: UUID | None = Field(
        default=None,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID",
    )
    automatic_mandate_package_activation_mandate_version_id: UUID | None = Field(
        default=None,
        validation_alias="AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_VERSION_ID",
    )
    default_production_crypto_paper_account_id: UUID | None = Field(
        default=None,
        validation_alias="DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID",
    )
    live_crypto_dry_run_enabled: bool = Field(
        default=True,
        validation_alias="LIVE_CRYPTO_DRY_RUN_ENABLED",
    )
    live_crypto_max_order_usd: Decimal = Field(
        default=Decimal("5"),
        validation_alias="LIVE_CRYPTO_MAX_ORDER_USD",
    )
    live_crypto_preparation_enabled: bool = Field(
        default=False,
        validation_alias="LIVE_CRYPTO_PREPARATION_ENABLED",
    )
    venue_commissioning_enabled: bool = Field(
        default=False,
        validation_alias="VENUE_COMMISSIONING_ENABLED",
    )
    live_crypto_confirmation_challenge_minutes: int = Field(
        default=1,
        validation_alias="LIVE_CRYPTO_CONFIRMATION_CHALLENGE_MINUTES",
    )
    live_crypto_preview_max_age_seconds: int = Field(
        default=30,
        validation_alias="LIVE_CRYPTO_PREVIEW_MAX_AGE_SECONDS",
    )
    live_crypto_balance_max_age_seconds: int = Field(
        default=30,
        validation_alias="LIVE_CRYPTO_BALANCE_MAX_AGE_SECONDS",
    )
    live_crypto_readiness_max_age_seconds: int = Field(
        default=60,
        validation_alias="LIVE_CRYPTO_READINESS_MAX_AGE_SECONDS",
    )
    canonical_proving_provider_evidence_max_age_seconds: int = Field(
        default=120,
        validation_alias="CANONICAL_PROVING_PROVIDER_EVIDENCE_MAX_AGE_SECONDS",
    )
    live_crypto_price_max_age_seconds: int = Field(
        default=30,
        validation_alias="LIVE_CRYPTO_PRICE_MAX_AGE_SECONDS",
    )
    live_crypto_accounting_balance_tolerance_usd: Decimal = Field(
        default=Decimal("0.01"),
        validation_alias="LIVE_CRYPTO_ACCOUNTING_BALANCE_TOLERANCE_USD",
    )
    instant_trade_db_timeout_seconds: int = Field(
        default=4,
        validation_alias="INSTANT_TRADE_DB_TIMEOUT_SECONDS",
    )
    instant_trade_provider_timeout_seconds: int = Field(
        default=8,
        validation_alias="INSTANT_TRADE_PROVIDER_TIMEOUT_SECONDS",
    )
    instant_trade_reconciliation_poll_timeout_seconds: int = Field(
        default=6,
        validation_alias="INSTANT_TRADE_RECONCILIATION_POLL_TIMEOUT_SECONDS",
    )
    research_evolution_enabled: bool = Field(default=True, validation_alias="RESEARCH_EVOLUTION_ENABLED")
    research_cycle_interval_minutes: int = Field(default=30, validation_alias="RESEARCH_CYCLE_INTERVAL_MINUTES")
    research_max_candidates_per_cycle: int = Field(default=6, validation_alias="RESEARCH_MAX_CANDIDATES_PER_CYCLE")
    research_max_descendants_per_candidate: int = Field(default=3, validation_alias="RESEARCH_MAX_DESCENDANTS_PER_CANDIDATE")
    research_max_generation: int = Field(default=5, validation_alias="RESEARCH_MAX_GENERATION")
    research_min_decisions: int = Field(default=50, validation_alias="RESEARCH_MIN_DECISIONS")
    research_min_actionable_signals: int = Field(default=5, validation_alias="RESEARCH_MIN_ACTIONABLE_SIGNALS")
    research_min_trades: int = Field(default=3, validation_alias="RESEARCH_MIN_TRADES")
    outcome_scoring_fee_bps: Decimal = Field(default=Decimal("10"), validation_alias="OUTCOME_SCORING_FEE_BPS")
    outcome_scoring_hold_buy_threshold_pct: Decimal = Field(
        default=Decimal("0"),
        validation_alias="OUTCOME_SCORING_HOLD_BUY_THRESHOLD_PCT",
    )
    outcome_scoring_hold_sell_threshold_pct: Decimal = Field(
        default=Decimal("0"),
        validation_alias="OUTCOME_SCORING_HOLD_SELL_THRESHOLD_PCT",
    )
    outcome_scoring_sideways_threshold_pct: Decimal = Field(
        default=Decimal("0.10"),
        validation_alias="OUTCOME_SCORING_SIDEWAYS_THRESHOLD_PCT",
    )
    outcome_scorecards_regime_min_evaluations: int = Field(
        default=50,
        validation_alias="OUTCOME_SCORECARDS_REGIME_MIN_EVALUATIONS",
    )
    outcome_scorecards_max_samples_per_action_horizon: int = Field(
        default=100,
        ge=1,
        le=1000,
        validation_alias="OUTCOME_SCORECARDS_MAX_SAMPLES_PER_ACTION_HORIZON",
    )
    strategy_aggregator_config_version: str = Field(
        default="v1", validation_alias="STRATEGY_AGGREGATOR_CONFIG_VERSION"
    )
    strategy_aggregator_min_eligible_strategies: int = Field(
        default=2, validation_alias="STRATEGY_AGGREGATOR_MIN_ELIGIBLE_STRATEGIES"
    )
    strategy_aggregator_min_buy_agreement: Decimal = Field(
        default=Decimal("0.60"), validation_alias="STRATEGY_AGGREGATOR_MIN_BUY_AGREEMENT"
    )
    strategy_aggregator_min_sell_agreement: Decimal = Field(
        default=Decimal("0.60"), validation_alias="STRATEGY_AGGREGATOR_MIN_SELL_AGREEMENT"
    )
    strategy_aggregator_min_confidence: Decimal = Field(
        default=Decimal("0.40"), validation_alias="STRATEGY_AGGREGATOR_MIN_CONFIDENCE"
    )
    strategy_aggregator_max_evidence_age_minutes: int = Field(
        default=30, validation_alias="STRATEGY_AGGREGATOR_MAX_EVIDENCE_AGE_MINUTES"
    )
    strategy_aggregator_min_outcome_sample_size: int = Field(
        default=20, validation_alias="STRATEGY_AGGREGATOR_MIN_OUTCOME_SAMPLE_SIZE"
    )
    strategy_aggregator_veto_on_data_quality_failure: bool = Field(
        default=True, validation_alias="STRATEGY_AGGREGATOR_VETO_ON_DATA_QUALITY_FAILURE"
    )

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
