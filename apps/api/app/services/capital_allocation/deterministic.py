from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from app.services.capital_allocation.interface import (
    CapitalAllocationEntry,
    CapitalAllocationInput,
    CapitalAllocationRecommendation,
)


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000004")


def build_capital_allocation_recommendation_v1(
    *,
    tournament_ranking: list[CapitalAllocationInput],
    highest_quality_strategy: str | None,
    quality_scores_by_strategy: dict[str, int],
    total_paper_capital: Decimal,
) -> CapitalAllocationRecommendation:
    ordered = sorted(tournament_ranking, key=lambda item: item.overall_rank)

    if not ordered:
        return CapitalAllocationRecommendation(
            recommendation_id=uuid.uuid5(_NAMESPACE, "empty"),
            generated_at=datetime.now(timezone.utc),
            total_paper_capital=total_paper_capital,
            allocations=tuple(),
        )

    if len(ordered) == 1:
        only = ordered[0]
        return CapitalAllocationRecommendation(
            recommendation_id=uuid.uuid5(_NAMESPACE, f"single:{only.strategy_name}:{total_paper_capital}"),
            generated_at=datetime.now(timezone.utc),
            total_paper_capital=total_paper_capital,
            allocations=(
                CapitalAllocationEntry(
                    strategy_name=only.strategy_name,
                    allocation_percent=Decimal("100"),
                    allocation_amount=total_paper_capital,
                    rationale=_rationale(
                        strategy_name=only.strategy_name,
                        rank=1,
                        quality_scores_by_strategy=quality_scores_by_strategy,
                        highest_quality_strategy=highest_quality_strategy,
                    ),
                ),
            ),
        )

    allocations: list[CapitalAllocationEntry] = []
    for item in ordered:
        if item.overall_rank == 1:
            percent = Decimal("70")
        elif item.overall_rank == 2:
            percent = Decimal("30")
        else:
            percent = Decimal("0")

        amount = (total_paper_capital * percent) / Decimal("100")
        allocations.append(
            CapitalAllocationEntry(
                strategy_name=item.strategy_name,
                allocation_percent=percent,
                allocation_amount=amount,
                rationale=_rationale(
                    strategy_name=item.strategy_name,
                    rank=item.overall_rank,
                    quality_scores_by_strategy=quality_scores_by_strategy,
                    highest_quality_strategy=highest_quality_strategy,
                ),
            )
        )

    recommendation_id = uuid.uuid5(
        _NAMESPACE,
        "|".join(f"{item.strategy_name}:{item.allocation_percent}:{item.allocation_amount}" for item in allocations),
    )

    return CapitalAllocationRecommendation(
        recommendation_id=recommendation_id,
        generated_at=datetime.now(timezone.utc),
        total_paper_capital=total_paper_capital,
        allocations=tuple(allocations),
    )


def _rationale(
    *,
    strategy_name: str,
    rank: int,
    quality_scores_by_strategy: dict[str, int],
    highest_quality_strategy: str | None,
) -> str:
    quality_score = quality_scores_by_strategy.get(strategy_name, 0)
    if rank == 1:
        return (
            f"Ranked first in tournament with quality score {quality_score}. "
            "Receives primary deterministic allocation tier."
        )
    if rank == 2:
        return (
            f"Ranked second in tournament with quality score {quality_score}. "
            "Receives secondary deterministic allocation tier."
        )
    if highest_quality_strategy and strategy_name == highest_quality_strategy:
        return (
            f"Matches highest-quality strategy signal with quality score {quality_score}, "
            "but receives zero allocation in v1 because allocation is capped to top two ranks."
        )
    return (
        f"Ranked outside top two deterministic tiers with quality score {quality_score}. "
        "Receives zero allocation in v1 policy."
    )
