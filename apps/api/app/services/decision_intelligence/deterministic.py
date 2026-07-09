from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.decision_intelligence.interface import DecisionIntelligenceRecommendation
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.replay.interface import ReplayResult


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000002")


@dataclass(frozen=True, slots=True)
class StrategyEvidence:
    strategy_name: str
    replay_result: ReplayResult


def build_decision_intelligence_recommendation_v1(
    *,
    strategy_evidence: list[StrategyEvidence],
) -> DecisionIntelligenceRecommendation:
    if not strategy_evidence:
        return DecisionIntelligenceRecommendation(
            recommendation_id=uuid.uuid5(_NAMESPACE, "no-strategies"),
            generated_at=datetime.now(timezone.utc),
            compared_strategies=tuple(),
            highest_quality_strategy=None,
            evidence_summary="No active strategies had replay-ready evidence.",
            confidence_summary="No confidence comparison available.",
            recommendation_summary="No deterministic recommendation can be generated yet.",
            human_review_required=True,
            promotion_recommended=False,
        )

    scored: list[tuple[str, int, Decimal, str]] = []
    for item in strategy_evidence:
        quality = evaluate_replay_result_v0(replay_result=item.replay_result)
        coach = evaluate_decision_quality_v0(decision_quality_result=quality)
        replay_variance = _replay_variance(item.replay_result)
        scored.append((item.strategy_name, quality.quality_score, replay_variance, coach.confidence_note))

    scored.sort(key=lambda entry: (-entry[1], entry[2], entry[0]))
    best_strategy, best_quality, _, best_confidence_note = scored[0]
    compared = tuple(entry[0] for entry in scored)

    evidence_summary = (
        f"Compared {len(scored)} active strategies using deterministic replay quality and variance tie-breaks."
    )
    confidence_summary = f"Best strategy confidence note: {best_confidence_note}"
    recommendation_summary = (
        f"{best_strategy} ranked highest by deterministic quality scoring with configured tie-break rules."
    )

    recommendation_id = uuid.uuid5(
        _NAMESPACE,
        "|".join(
            [
                *compared,
                best_strategy,
                str(best_quality),
            ],
        ),
    )

    return DecisionIntelligenceRecommendation(
        recommendation_id=recommendation_id,
        generated_at=datetime.now(timezone.utc),
        compared_strategies=compared,
        highest_quality_strategy=best_strategy,
        evidence_summary=evidence_summary,
        confidence_summary=confidence_summary,
        recommendation_summary=recommendation_summary,
        human_review_required=True,
        promotion_recommended=False,
    )


def _replay_variance(replay_result: ReplayResult) -> Decimal:
    original = replay_result.metadata.get("original_confidence")
    reconstructed = replay_result.confidence

    if original is None or reconstructed is None:
        return Decimal("999")

    try:
        original_decimal = Decimal(str(original))
    except Exception:
        return Decimal("999")

    return abs(reconstructed - original_decimal)
