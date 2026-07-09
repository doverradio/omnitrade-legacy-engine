from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from app.services.tournament.interface import TournamentRankingEntry, TournamentSnapshot, TournamentStrategyEvidence


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000003")


def build_tournament_snapshot_v1(
    *,
    strategies: list[TournamentStrategyEvidence],
) -> TournamentSnapshot:
    ordered = sorted(
        strategies,
        key=lambda item: (
            -item.quality_score,
            item.replay_variance,
            -item.realized_pnl,
            item.strategy_name,
        ),
    )

    ranking = tuple(
        TournamentRankingEntry(
            strategy_name=item.strategy_name,
            quality_score=item.quality_score,
            replay_variance=item.replay_variance,
            replay_count=item.replay_count,
            paper_trades=item.paper_trades,
            realized_pnl=item.realized_pnl,
            unrealized_pnl=item.unrealized_pnl,
            win_rate=item.win_rate,
            overall_rank=index,
        )
        for index, item in enumerate(ordered, start=1)
    )

    compared_strategies = tuple(item.strategy_name for item in ordered)
    tournament_id = uuid.uuid5(
        _NAMESPACE,
        "|".join(
            f"{item.strategy_name}:{item.quality_score}:{item.replay_variance}:{item.realized_pnl}:{item.overall_rank}"
            for item in ranking
        )
        or "empty",
    )

    return TournamentSnapshot(
        tournament_id=tournament_id,
        generated_at=datetime.now(timezone.utc),
        compared_strategies=compared_strategies,
        ranking=ranking,
    )


def replay_variance_from_confidence(*, original_confidence: object, reconstructed_confidence: Decimal | None) -> Decimal:
    if original_confidence is None or reconstructed_confidence is None:
        return Decimal("999")

    try:
        original = Decimal(str(original_confidence))
    except Exception:
        return Decimal("999")

    return abs(reconstructed_confidence - original)
