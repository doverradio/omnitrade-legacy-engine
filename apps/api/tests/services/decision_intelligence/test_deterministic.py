from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.services.decision_intelligence.deterministic import StrategyEvidence, build_decision_intelligence_recommendation_v1
from app.services.replay.interface import ReplayResult


def _replay_result(*, action: str, reconstructed_confidence: str, original_confidence: str) -> ReplayResult:
    return ReplayResult(
        replay_id=uuid.uuid4(),
        replay_agent_id=uuid.uuid4(),
        decision_package_id="dpkg:test",
        replay_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        reconstructed_action=action,
        confidence=Decimal(reconstructed_confidence),
        supporting_evidence=tuple(),
        explanation="test",
        metadata={
            "original_action": action,
            "original_confidence": original_confidence,
            "replay_duration_ms": 1,
        },
    )


def test_decision_intelligence_handles_one_strategy() -> None:
    recommendation = build_decision_intelligence_recommendation_v1(
        strategy_evidence=[
            StrategyEvidence(
                strategy_name="MA Crossover",
                replay_result=_replay_result(action="BUY", reconstructed_confidence="0.80", original_confidence="0.80"),
            )
        ]
    )

    assert recommendation.highest_quality_strategy == "MA Crossover"
    assert recommendation.compared_strategies == ("MA Crossover",)
    assert recommendation.human_review_required is True
    assert recommendation.promotion_recommended is False


def test_decision_intelligence_handles_two_strategies() -> None:
    recommendation = build_decision_intelligence_recommendation_v1(
        strategy_evidence=[
            StrategyEvidence(
                strategy_name="MA Crossover",
                replay_result=_replay_result(action="BUY", reconstructed_confidence="0.80", original_confidence="0.80"),
            ),
            StrategyEvidence(
                strategy_name="RSI Mean Reversion",
                replay_result=_replay_result(action="BUY", reconstructed_confidence="0.50", original_confidence="0.90"),
            ),
        ]
    )

    assert recommendation.highest_quality_strategy == "MA Crossover"
    assert recommendation.compared_strategies == ("MA Crossover", "RSI Mean Reversion")


def test_decision_intelligence_tie_breaks_alphabetically() -> None:
    recommendation = build_decision_intelligence_recommendation_v1(
        strategy_evidence=[
            StrategyEvidence(
                strategy_name="RSI Mean Reversion",
                replay_result=_replay_result(action="BUY", reconstructed_confidence="0.80", original_confidence="0.80"),
            ),
            StrategyEvidence(
                strategy_name="MA Crossover",
                replay_result=_replay_result(action="BUY", reconstructed_confidence="0.80", original_confidence="0.80"),
            ),
        ]
    )

    assert recommendation.highest_quality_strategy == "MA Crossover"
