from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.schemas.tournament import TournamentRankingEntryResponse, TournamentResponse


def test_tournament_response_schema() -> None:
    payload = TournamentResponse(
        tournament_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        generated_at=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        compared_strategies=["MA Crossover", "RSI Mean Reversion"],
        ranking=[
            TournamentRankingEntryResponse(
                strategy_name="MA Crossover",
                quality_score=100,
                replay_variance="0.00",
                replay_count=1,
                paper_trades=6,
                realized_pnl="18.5",
                unrealized_pnl="2.25",
                win_rate="0.50",
                overall_rank=1,
            )
        ],
    )

    serialized = payload.model_dump(mode="json")
    assert serialized["ranking"][0]["strategy_name"] == "MA Crossover"
    assert serialized["ranking"][0]["overall_rank"] == 1