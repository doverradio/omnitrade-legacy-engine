from __future__ import annotations

from decimal import Decimal

from app.services.tournament.deterministic import build_tournament_snapshot_v1
from app.services.tournament.interface import TournamentStrategyEvidence


def test_tournament_single_strategy() -> None:
    snapshot = build_tournament_snapshot_v1(
        strategies=[
            TournamentStrategyEvidence(
                strategy_name="MA Crossover",
                quality_score=100,
                replay_variance=Decimal("0.00"),
                replay_count=1,
                paper_trades=5,
                realized_pnl=Decimal("10.50"),
                unrealized_pnl=Decimal("1.10"),
                win_rate=Decimal("0.60"),
            )
        ]
    )

    assert snapshot.compared_strategies == ("MA Crossover",)
    assert len(snapshot.ranking) == 1
    assert snapshot.ranking[0].strategy_name == "MA Crossover"
    assert snapshot.ranking[0].overall_rank == 1


def test_tournament_two_strategies() -> None:
    snapshot = build_tournament_snapshot_v1(
        strategies=[
            TournamentStrategyEvidence(
                strategy_name="MA Crossover",
                quality_score=100,
                replay_variance=Decimal("0.00"),
                replay_count=1,
                paper_trades=5,
                realized_pnl=Decimal("10.50"),
                unrealized_pnl=Decimal("1.10"),
                win_rate=Decimal("0.60"),
            ),
            TournamentStrategyEvidence(
                strategy_name="RSI Mean Reversion",
                quality_score=50,
                replay_variance=Decimal("0.20"),
                replay_count=1,
                paper_trades=4,
                realized_pnl=Decimal("7.20"),
                unrealized_pnl=Decimal("0.40"),
                win_rate=Decimal("0.50"),
            ),
        ]
    )

    assert snapshot.ranking[0].strategy_name == "MA Crossover"
    assert snapshot.ranking[0].overall_rank == 1
    assert snapshot.ranking[1].strategy_name == "RSI Mean Reversion"
    assert snapshot.ranking[1].overall_rank == 2


def test_tournament_tie_break_alphabetical() -> None:
    snapshot = build_tournament_snapshot_v1(
        strategies=[
            TournamentStrategyEvidence(
                strategy_name="RSI Mean Reversion",
                quality_score=100,
                replay_variance=Decimal("0.00"),
                replay_count=1,
                paper_trades=4,
                realized_pnl=Decimal("10.00"),
                unrealized_pnl=Decimal("1.00"),
                win_rate=Decimal("0.50"),
            ),
            TournamentStrategyEvidence(
                strategy_name="MA Crossover",
                quality_score=100,
                replay_variance=Decimal("0.00"),
                replay_count=1,
                paper_trades=4,
                realized_pnl=Decimal("10.00"),
                unrealized_pnl=Decimal("1.00"),
                win_rate=Decimal("0.50"),
            ),
        ]
    )

    assert snapshot.ranking[0].strategy_name == "MA Crossover"
    assert snapshot.ranking[1].strategy_name == "RSI Mean Reversion"
