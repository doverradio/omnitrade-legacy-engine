from __future__ import annotations

import uuid

from app.services.ai_coach.interface import AICoachObservation
from app.services.decision_quality.interface import DecisionQualityResult


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")


def evaluate_decision_quality_v0(*, decision_quality_result: DecisionQualityResult) -> AICoachObservation:
    summary = _build_summary(decision_quality_result)
    strengths = _build_strengths(decision_quality_result)
    weaknesses = _build_weaknesses(decision_quality_result)
    confidence_note = _build_confidence_note(decision_quality_result)
    reproducibility_note = _build_reproducibility_note(decision_quality_result)
    suggested_follow_up = _build_follow_up(decision_quality_result)

    observation_id = uuid.uuid5(
        _NAMESPACE,
        "|".join(
            [
                str(decision_quality_result.quality_score),
                str(decision_quality_result.decision_reproduced),
                str(decision_quality_result.action_matches_original),
                str(decision_quality_result.confidence_matches_original),
                str(decision_quality_result.replay_duration_ms),
                summary,
            ],
        ),
    )

    return AICoachObservation(
        observation_id=observation_id,
        evaluation_timestamp=decision_quality_result.evaluation_timestamp,
        summary=summary,
        strengths=tuple(strengths),
        weaknesses=tuple(weaknesses),
        confidence_note=confidence_note,
        reproducibility_note=reproducibility_note,
        suggested_follow_up=suggested_follow_up,
    )


def _build_summary(result: DecisionQualityResult) -> str:
    if result.decision_reproduced:
        return "Replay successfully reproduced the production decision."

    messages: list[str] = []
    if not result.action_matches_original:
        messages.append("Replay action differed from production.")
    if not result.confidence_matches_original:
        messages.append("Confidence mismatch detected.")

    return " ".join(messages) if messages else "Replay review requires follow-up."


def _build_strengths(result: DecisionQualityResult) -> list[str]:
    strengths: list[str] = []
    if result.decision_reproduced:
        strengths.append("Replay successfully reproduced the production decision.")
    elif result.action_matches_original:
        strengths.append("Replay action matched production.")
    elif result.confidence_matches_original:
        strengths.append("Replay confidence matched production.")

    return strengths


def _build_weaknesses(result: DecisionQualityResult) -> list[str]:
    weaknesses: list[str] = []
    if not result.action_matches_original:
        weaknesses.append("Replay action differed from production.")
    if not result.confidence_matches_original:
        weaknesses.append("Confidence mismatch detected.")
    return weaknesses


def _build_confidence_note(result: DecisionQualityResult) -> str:
    if result.confidence_matches_original:
        return "Confidence aligned with the original decision."
    return "Confidence mismatch detected."


def _build_reproducibility_note(result: DecisionQualityResult) -> str:
    if result.decision_reproduced:
        return "Replay reproduced the production decision exactly."
    return "Replay did not fully reproduce the production decision."


def _build_follow_up(result: DecisionQualityResult) -> str:
    if result.decision_reproduced:
        return "Use this replay as a deterministic baseline for future comparisons."
    return "Review the decision package evidence and investigate the mismatch before any further analysis."
