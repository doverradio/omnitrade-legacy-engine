from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StrategyHealthItemResponse(BaseModel):
    strategy_name: str
    enabled: bool
    last_signal_time: datetime | None
    last_trade_time: datetime | None
    signals_today: int
    decision_records_today: int
    status: str


class StrategyHealthResponse(BaseModel):
    items: list[StrategyHealthItemResponse]
