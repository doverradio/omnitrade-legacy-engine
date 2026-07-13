from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid


@dataclass(frozen=True)
class StrategyRosterRequest:
    asset_id: uuid.UUID
    provider: str
    product_id: str
    interval: str
    candle_open_time: datetime
    candle_close_time: datetime
    trigger: str
    scheduled_cycle_id: uuid.UUID | None = None


@dataclass(frozen=True)
class StrategyRosterRunResult:
    roster_run_id: uuid.UUID
    replayed: bool
    strategies_requested_count: int
    strategies_completed_count: int
    strategies_failed_count: int
    buy_count: int
    sell_count: int
    hold_count: int


@dataclass(frozen=True)
class StrategyRosterProposalResult:
    strategy_slug: str
    strategy_version: str
    strategy_identity: str
    parameter_set_identity: str
    action: str
    evaluation_status: str
    strength: Decimal | None
    confidence: Decimal | None
    reason: str
    deterministic_explanation: tuple[str, ...]
