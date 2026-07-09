from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class StrategyArenaScoreboardItem(BaseModel):
    strategy_id: uuid.UUID
    strategy_name: str
    enabled: bool
    status: str
    signals_generated: int
    buy_signals: int
    sell_signals: int
    hold_signals: int
    paper_trades: int
    open_positions: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_pct: Decimal
    decision_records: int
    last_signal_timestamp: datetime | None = None
    last_trade_timestamp: datetime | None = None

    @field_serializer("realized_pnl", "unrealized_pnl", "total_return_pct", when_used="json")
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return format(value, "f")


class StrategyArenaScoreboardResponse(BaseModel):
    items: list[StrategyArenaScoreboardItem]
