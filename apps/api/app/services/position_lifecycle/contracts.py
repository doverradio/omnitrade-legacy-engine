from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class PositionLifecyclePolicy:
    policy_id: str
    policy_version: str
    asset_class: str
    venue_scope: str
    instrument_scope: str | None
    evaluation_cadence: str
    effective_at: datetime
    expires_at: datetime | None

    minimum_net_profit_to_exit: Decimal
    estimated_exit_fee_rate: Decimal
    estimated_slippage_rate: Decimal

    stale_price_threshold_minutes: int
    minimum_position_size: Decimal

    stop_loss_percent: Decimal | None
    stop_loss_price: Decimal | None
    max_hold_minutes: int | None

    dust_threshold: Decimal


@dataclass(frozen=True)
class PositionSnapshot:
    position_id: str
    live_trading_profile_id: UUID
    account_id: UUID
    capital_campaign_id: int | None
    symbol: str
    asset_class: str

    position_size: Decimal
    entry_price: Decimal
    accumulated_entry_and_carry_costs: Decimal

    opened_at: datetime | None
    last_fill_at: datetime | None

    provider_order_ids: tuple[str, ...]
    provider_fill_ids: tuple[str, ...]
    accounting_record_count: int
    fail_closed_reason: str | None

    current_price: Decimal | None
    market_data_timestamp: datetime | None
    market_data_age_minutes: int | None
    market_data_interval: str | None
    market_data_source: str | None
    market_data_candle_id: int | None


@dataclass(frozen=True)
class PositionLifecycleEvaluation:
    lifecycle_state: str
    recommendation: str
    reason: str

    current_market_value: Decimal | None
    expected_net_realized_pnl_if_sold_now: Decimal | None
    break_even_price: Decimal | None
    minimum_profitable_exit_price: Decimal | None

    market_data_stale: bool
    stale_indicator: bool
    dust_indicator: bool
    closed_indicator: bool
