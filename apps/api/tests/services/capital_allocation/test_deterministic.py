from __future__ import annotations

from decimal import Decimal

from app.services.capital_allocation.deterministic import build_capital_allocation_recommendation_v1
from app.services.capital_allocation.interface import CapitalAllocationInput


def test_capital_allocation_one_strategy() -> None:
    recommendation = build_capital_allocation_recommendation_v1(
        tournament_ranking=[CapitalAllocationInput(strategy_name="MA Crossover", overall_rank=1)],
        highest_quality_strategy="MA Crossover",
        quality_scores_by_strategy={"MA Crossover": 100},
        total_paper_capital=Decimal("100000"),
    )

    assert len(recommendation.allocations) == 1
    assert recommendation.allocations[0].strategy_name == "MA Crossover"
    assert recommendation.allocations[0].allocation_percent == Decimal("100")
    assert recommendation.allocations[0].allocation_amount == Decimal("100000")


def test_capital_allocation_two_strategies() -> None:
    recommendation = build_capital_allocation_recommendation_v1(
        tournament_ranking=[
            CapitalAllocationInput(strategy_name="MA Crossover", overall_rank=1),
            CapitalAllocationInput(strategy_name="RSI Mean Reversion", overall_rank=2),
        ],
        highest_quality_strategy="MA Crossover",
        quality_scores_by_strategy={
            "MA Crossover": 100,
            "RSI Mean Reversion": 50,
        },
        total_paper_capital=Decimal("100000"),
    )

    assert len(recommendation.allocations) == 2
    assert recommendation.allocations[0].allocation_percent == Decimal("70")
    assert recommendation.allocations[0].allocation_amount == Decimal("70000")
    assert recommendation.allocations[1].allocation_percent == Decimal("30")
    assert recommendation.allocations[1].allocation_amount == Decimal("30000")


def test_capital_allocation_totals_100_percent() -> None:
    recommendation = build_capital_allocation_recommendation_v1(
        tournament_ranking=[
            CapitalAllocationInput(strategy_name="MA Crossover", overall_rank=1),
            CapitalAllocationInput(strategy_name="RSI Mean Reversion", overall_rank=2),
            CapitalAllocationInput(strategy_name="Momentum", overall_rank=3),
        ],
        highest_quality_strategy="MA Crossover",
        quality_scores_by_strategy={
            "MA Crossover": 100,
            "RSI Mean Reversion": 50,
            "Momentum": 25,
        },
        total_paper_capital=Decimal("100000"),
    )

    total_percent = sum(item.allocation_percent for item in recommendation.allocations)
    total_amount = sum(item.allocation_amount for item in recommendation.allocations)

    assert total_percent == Decimal("100")
    assert total_amount == Decimal("100000")
