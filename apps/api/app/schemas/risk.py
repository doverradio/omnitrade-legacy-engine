from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class KillSwitchStateResponse(BaseModel):
    engaged: bool
    engaged_at: datetime | None = None
    engaged_by: str | None = None
    reason: str | None = None


class RiskUsageResponse(BaseModel):
    used: Decimal
    limit: Decimal
    pct_used: Decimal

    @field_serializer("used", "limit", "pct_used", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CooldownStateResponse(BaseModel):
    strategy_id: uuid.UUID
    asset_id: uuid.UUID
    cooldown_until: datetime
    reason: str


class NoTradeZoneStateResponse(BaseModel):
    asset_id: uuid.UUID
    reason: str
    since: datetime


class AccountRiskStatusResponse(BaseModel):
    account_id: uuid.UUID
    trading_paused: bool
    paused_reason: str | None = None
    daily_loss: RiskUsageResponse
    drawdown: RiskUsageResponse
    active_cooldowns: list[CooldownStateResponse]
    active_no_trade_zones: list[NoTradeZoneStateResponse]
    active_cooldowns_state: str | None = None
    active_no_trade_zones_state: str | None = None
    policy_source: str | None = None
    daily_loss_input_source: str | None = None
    drawdown_input_source: str | None = None
    current_equity: Decimal | None = None
    current_cash_balance: Decimal | None = None
    current_position_value: Decimal | None = None
    start_of_day_equity: Decimal | None = None
    high_water_mark_equity: Decimal | None = None
    valuation_price_timestamp: datetime | None = None
    valuation_source: str | None = None
    valuation_state: str | None = None
    daily_loss_baseline_source: str | None = None
    drawdown_baseline_source: str | None = None
    baseline_state: str | None = None
    generated_at: datetime | None = None


class RiskStatusResponse(BaseModel):
    global_kill_switch: KillSwitchStateResponse
    account: AccountRiskStatusResponse


class KillSwitchRequest(BaseModel):
    scope: str
    account_id: uuid.UUID | None = None
    reason: str
    confirm: bool
    actor: str = "user:unknown"


class KillSwitchResponse(BaseModel):
    scope: str
    account_id: uuid.UUID | None = None
    engaged: bool
    engaged_at: datetime | None = None
    engaged_by: str | None = None
    disengaged_at: datetime | None = None
    disengaged_by: str | None = None


class RiskRulesValues(BaseModel):
    max_position_size_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    default_stop_loss_pct: Decimal
    cooldown_after_losses: int
    cooldown_duration_hours: int

    @field_serializer(
        "max_position_size_pct",
        "max_daily_loss_pct",
        "max_drawdown_pct",
        "default_stop_loss_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class RiskRulesResponse(BaseModel):
    account_id: uuid.UUID | None = None
    rules: RiskRulesValues
    is_override: bool
    system_defaults: RiskRulesValues


class RiskRulesPatchValues(BaseModel):
    max_position_size_pct: Decimal | None = None
    max_daily_loss_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    default_stop_loss_pct: Decimal | None = None
    cooldown_after_losses: int | None = None
    cooldown_duration_hours: int | None = None


class RiskRulesPatchRequest(BaseModel):
    account_id: uuid.UUID | None = None
    rules: RiskRulesPatchValues
    confirm_loosening: bool | None = None
    actor: str = "user:unknown"