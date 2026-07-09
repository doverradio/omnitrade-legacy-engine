from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid


@dataclass(frozen=True, slots=True)
class TournamentStrategyEvidence:
    strategy_name: str
    quality_score: int
    replay_variance: Decimal
    replay_count: int
    paper_trades: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    win_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class TournamentRankingEntry:
    strategy_name: str
    quality_score: int
    replay_variance: Decimal
    replay_count: int
    paper_trades: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    win_rate: Decimal | None
    overall_rank: int


@dataclass(frozen=True, slots=True)
class TournamentSnapshot:
    tournament_id: uuid.UUID
    generated_at: datetime
    compared_strategies: tuple[str, ...]
    ranking: tuple[TournamentRankingEntry, ...]
