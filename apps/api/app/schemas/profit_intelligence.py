from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer


ProfitMode = Literal["paper", "live", "combined"]
ProfitRange = Literal["24h", "72h", "7d", "30d", "90d", "all"]


class ProfitSeriesPointResponse(BaseModel):
    timestamp: datetime
    paper_equity: Decimal | None = None
    live_equity: Decimal | None = None
    combined_equity: Decimal | None = None
    cumulative_realized_pnl: Decimal | None = None
    cumulative_unrealized_pnl: Decimal | None = None
    cumulative_fees: Decimal | None = None
    cumulative_net_profit: Decimal | None = None
    drawdown: Decimal | None = None
    trade_count: int = 0
    source_event_ids: list[str] = Field(default_factory=list)

    @field_serializer(
        "paper_equity",
        "live_equity",
        "combined_equity",
        "cumulative_realized_pnl",
        "cumulative_unrealized_pnl",
        "cumulative_fees",
        "cumulative_net_profit",
        "drawdown",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class ProfitAnnotationResponse(BaseModel):
    timestamp: datetime
    event_type: str
    title: str
    description: str
    severity: str
    source_record_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfitMetricResponse(BaseModel):
    range: ProfitRange
    mode: ProfitMode
    start_at: datetime | None
    end_at: datetime
    starting_equity: Decimal | None
    ending_equity: Decimal | None
    gross_profit: Decimal | None
    gross_loss: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    fees: Decimal | None
    fees_available: bool
    net_profit: Decimal | None
    total_economic_pnl: Decimal | None
    return_percent: Decimal | None
    peak_equity: Decimal | None
    max_drawdown_amount: Decimal | None
    max_drawdown_percent: Decimal | None
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    trade_count: int
    open_position_count: int
    equity_series: list[ProfitSeriesPointResponse] = Field(default_factory=list)
    profit_series: list[ProfitSeriesPointResponse] = Field(default_factory=list)
    annotations: list[ProfitAnnotationResponse] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    data_completeness: int
    calculation_explanation: str
    generated_at: datetime

    @field_serializer(
        "starting_equity",
        "ending_equity",
        "gross_profit",
        "gross_loss",
        "realized_pnl",
        "unrealized_pnl",
        "fees",
        "net_profit",
        "total_economic_pnl",
        "return_percent",
        "peak_equity",
        "max_drawdown_amount",
        "max_drawdown_percent",
        "win_rate",
        "profit_factor",
        "average_win",
        "average_loss",
        "largest_win",
        "largest_loss",
        when_used="json",
    )
    def _serialize_decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")
