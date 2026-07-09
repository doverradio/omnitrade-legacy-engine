from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class PositionResponse(BaseModel):
    asset_id: uuid.UUID
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    unrealized_pnl_usd: Decimal
    unrealized_pnl_pct: Decimal

    @field_serializer(
        "quantity",
        "avg_entry_price",
        "unrealized_pnl_usd",
        "unrealized_pnl_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperAccountResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    asset_class: str
    starting_balance: Decimal
    current_cash_balance: Decimal
    equity: Decimal
    equity_return_usd: Decimal
    equity_return_pct: Decimal
    positions: list[PositionResponse]

    @field_serializer(
        "starting_balance",
        "current_cash_balance",
        "equity",
        "equity_return_usd",
        "equity_return_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CreatePaperAccountRequest(BaseModel):
    name: str
    asset_class: str
    starting_balance: Decimal


class CreatePaperAccountResponse(BaseModel):
    id: uuid.UUID
    name: str
    asset_class: str
    starting_balance: Decimal
    current_cash_balance: Decimal
    is_active: bool

    @field_serializer("starting_balance", "current_cash_balance", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class ResetPaperAccountRequest(BaseModel):
    account_id: uuid.UUID
    confirm: bool


class ResetPaperAccountResponse(BaseModel):
    account_id: uuid.UUID
    current_cash_balance: Decimal
    positions: list[PositionResponse]

    @field_serializer("current_cash_balance", when_used="json")
    def serialize_numeric_field(self, value: Decimal) -> str:
        return format(value, "f")


class ExecuteSignalRequest(BaseModel):
    signal_id: uuid.UUID
    account_id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    actor: str = "system"
    client_order_id: str | None = None


class ExecuteSignalResponse(BaseModel):
    signal_id: uuid.UUID
    account_id: uuid.UUID
    asset_id: uuid.UUID
    execution_status: str
    execution_venue: str
    is_paper: bool
    trade_id: uuid.UUID | None = None
    broker_order_id: str | None = None
    venue_status: str | None = None
    message: str


class PaperTradeResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    executed_at: datetime
    signal_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    symbol: str | None = None

    @field_serializer("quantity", "price", "fee", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperTradeListResponse(BaseModel):
    items: list[PaperTradeResponse]
    next_cursor: str | None


class PaperTradeHistoryItem(BaseModel):
    trade_id: uuid.UUID
    executed_at: datetime
    asset: str | None = None
    side: str
    quantity: Decimal
    execution_price: Decimal
    notional: Decimal
    signal_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    decision_record_id: uuid.UUID | None = None
    realized_pnl: Decimal | None = None
    paper_account_id: uuid.UUID

    @field_serializer("quantity", "execution_price", "notional", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("realized_pnl", when_used="json")
    def serialize_optional_numeric_field(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class PaperTradeHistoryResponse(BaseModel):
    items: list[PaperTradeHistoryItem]
    limit: int
    offset: int
    total: int
    has_more: bool


class PaperEquityCurvePoint(BaseModel):
    timestamp: datetime
    equity: Decimal
    cash_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    trade_count_at_point: int

    @field_serializer("equity", "cash_balance", "realized_pnl", "unrealized_pnl", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperEquityCurveResponse(BaseModel):
    account_id: uuid.UUID
    window_minutes: int
    interval: int
    starting_balance: Decimal
    current_equity: Decimal
    total_return_usd: Decimal
    total_return_pct: Decimal
    latest_point_timestamp: datetime | None = None
    points: list[PaperEquityCurvePoint]

    @field_serializer(
        "starting_balance",
        "current_equity",
        "total_return_usd",
        "total_return_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PipelineActivityItem(BaseModel):
    signal_id: uuid.UUID
    action: str
    status: str
    reason: str | None = None
    created_at: datetime


class PaperPipelineHealthResponse(BaseModel):
    window_minutes: int
    candles: int
    signals_created: int
    hold_signals: int
    buy_sell_signals: int
    execution_candidates: int
    executions_attempted: int
    risk_events: int
    risk_rejected: int
    trades: int
    decision_records: int
    latest_rejection_reason: str | None = None
    latest_updated_at: datetime | None = None
    recent_activity: list[PipelineActivityItem]


class PaperLatestTradeSummary(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    symbol: str | None = None
    strategy_id: uuid.UUID | None = None
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    executed_at: datetime

    @field_serializer("quantity", "price", "fee", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperAssetPerformanceSummary(BaseModel):
    asset_id: uuid.UUID
    symbol: str | None = None
    trade_count: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal

    @field_serializer("realized_pnl", "unrealized_pnl", "total_pnl", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperStrategyPerformanceSummary(BaseModel):
    strategy_id: uuid.UUID
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    realized_pnl: Decimal

    @field_serializer("win_rate", "realized_pnl", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperPerformanceSummaryResponse(BaseModel):
    account_id: uuid.UUID
    starting_balance: Decimal
    current_cash_balance: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_usd: Decimal
    total_return_pct: Decimal
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    latest_trade: PaperLatestTradeSummary | None = None
    positions: list[PositionResponse]
    by_asset: list[PaperAssetPerformanceSummary]
    by_strategy: list[PaperStrategyPerformanceSummary]

    @field_serializer(
        "starting_balance",
        "current_cash_balance",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "total_return_usd",
        "total_return_pct",
        "win_rate",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")
