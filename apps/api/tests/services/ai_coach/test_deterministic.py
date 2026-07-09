from __future__ import annotations

from datetime import datetime, timezone

from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.decision_quality.interface import DecisionQualityResult


def _quality_result(*, decision_reproduced: bool, action_matches_original: bool, confidence_matches_original: bool) -> DecisionQualityResult:
    return DecisionQualityResult(
        quality_score=100 if decision_reproduced else 50,
        decision_reproduced=decision_reproduced,
        action_matches_original=action_matches_original,
        confidence_matches_original=confidence_matches_original,
        replay_duration_ms=12,
        evaluation_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
    )


def test_ai_coach_exact_replay_generates_positive_observation() -> None:
    observation = evaluate_decision_quality_v0(
        decision_quality_result=_quality_result(
            decision_reproduced=True,
            action_matches_original=True,
            confidence_matches_original=True,
        )
    )

    assert observation.summary == "Replay successfully reproduced the production decision."
    assert observation.strengths == ("Replay successfully reproduced the production decision.",)
    assert observation.weaknesses == ()
    assert observation.confidence_note == "Confidence aligned with the original decision."


def test_ai_coach_confidence_mismatch_generates_observation() -> None:
    observation = evaluate_decision_quality_v0(
        decision_quality_result=_quality_result(
            decision_reproduced=False,
            action_matches_original=True,
            confidence_matches_original=False,
        )
    )

    assert observation.summary == "Confidence mismatch detected."
    assert observation.strengths == ("Replay action matched production.",)
    assert observation.weaknesses == ("Confidence mismatch detected.",)
    assert observation.confidence_note == "Confidence mismatch detected."


def test_ai_coach_action_mismatch_generates_observation() -> None:
    observation = evaluate_decision_quality_v0(
        decision_quality_result=_quality_result(
            decision_reproduced=False,
            action_matches_original=False,
            confidence_matches_original=True,
        )
    )

    assert observation.summary == "Replay action differed from production."
    assert observation.strengths == ("Replay confidence matched production.",)
    assert observation.weaknesses == ("Replay action differed from production.",)
    assert observation.reproducibility_note == "Replay did not fully reproduce the production decision."
