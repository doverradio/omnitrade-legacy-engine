from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer


class PositionLifecycleItemResponse(BaseModel):
    position_id: str
    live_trading_profile_id: str
    account_id: str
    capital_campaign_id: int | None = None
    symbol: str
    asset_class: str
    policy_id: str
    policy_version: str

    lifecycle_state: str
    recommendation: str
    reason: str

    position_size: Decimal
    entry_price: Decimal
    current_price: Decimal | None = None
    current_market_value: Decimal | None = None
    expected_net_realized_pnl_if_sold_now: Decimal | None = None
    break_even_price: Decimal | None = None
    minimum_profitable_exit_price: Decimal | None = None

    opened_at: datetime | None = None
    last_fill_at: datetime | None = None
    provider_order_ids: list[str] = Field(default_factory=list)
    provider_fill_ids: list[str] = Field(default_factory=list)
    accounting_record_count: int = 0
    market_data_timestamp: datetime | None = None
    market_data_interval: str | None = None
    market_data_source: str | None = None
    market_data_candle_id: int | None = None
    market_data_age_minutes: int | None = None
    market_data_stale: bool = False
    stale_indicator: bool = False
    dust_indicator: bool = False
    closed_indicator: bool = False
    evaluated_at: datetime

    @field_serializer(
        "position_size",
        "entry_price",
        "current_price",
        "current_market_value",
        "expected_net_realized_pnl_if_sold_now",
        "break_even_price",
        "minimum_profitable_exit_price",
        when_used="json",
    )
    def _serialize_decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class PositionLifecycleResponse(BaseModel):
    generated_at: datetime
    count: int
    items: list[PositionLifecycleItemResponse] = Field(default_factory=list)
