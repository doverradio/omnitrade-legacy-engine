from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel


class TournamentRankingEntryResponse(BaseModel):
    strategy_name: str
    quality_score: int
    replay_variance: str
    replay_count: int
    paper_trades: int
    realized_pnl: str
    unrealized_pnl: str
    win_rate: str | None
    overall_rank: int


class TournamentResponse(BaseModel):
    tournament_id: uuid.UUID
    generated_at: datetime
    compared_strategies: list[str]
    ranking: list[TournamentRankingEntryResponse]
