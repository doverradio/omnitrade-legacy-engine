from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class BacktestRunRequest(BaseModel):
    strategy_id: uuid.UUID
    parameter_set_id: uuid.UUID
    asset_id: uuid.UUID
    interval: str
    start_time: datetime
    end_time: datetime
    initial_capital: Decimal
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")


class BacktestRunAcceptedResponse(BaseModel):
    backtest_id: uuid.UUID
    status: str


class BacktestMetricsResponse(BaseModel):
    total_return_usd: Decimal
    total_return_pct: Decimal
    win_rate: Decimal
    max_drawdown: Decimal
    sharpe_like: Decimal
    trade_count: int
    average_trade_usd: Decimal
    fee_drag_pct: Decimal

    @field_serializer(
        "total_return_usd",
        "total_return_pct",
        "win_rate",
        "max_drawdown",
        "sharpe_like",
        "average_trade_usd",
        "fee_drag_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class SmallAccountWarningResponse(BaseModel):
    type: str
    detail: str


class BacktestTradeResponse(BaseModel):
    side: str
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    reason: str | None = None

    @field_serializer("quantity", "price", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class BacktestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    status: str
    strategy_id: uuid.UUID
    parameter_set_id: uuid.UUID
    asset_id: uuid.UUID
    initial_capital: Decimal
    metrics: BacktestMetricsResponse | None = None
    small_account_warning: SmallAccountWarningResponse | None = None
    trades: list[BacktestTradeResponse] = []
    error_detail: str | None = None

    @field_serializer("initial_capital", when_used="json")
    def serialize_initial_capital(self, value: Decimal) -> str:
        return format(value, "f")


class BacktestListItemResponse(BaseModel):
    id: uuid.UUID
    status: str
    strategy_id: uuid.UUID
    parameter_set_id: uuid.UUID
    asset_id: uuid.UUID
    interval: str
    start_time: datetime
    end_time: datetime
    initial_capital: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    metrics: BacktestMetricsResponse | None = None
    small_account_warning: SmallAccountWarningResponse | None = None

    @field_serializer("initial_capital", "fee_bps", "slippage_bps", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class BacktestListResponse(BaseModel):
    items: list[BacktestListItemResponse]
    next_cursor: str | None = None


class BacktestTradeListResponse(BaseModel):
    items: list[BacktestTradeResponse]
    next_cursor: str | None = None