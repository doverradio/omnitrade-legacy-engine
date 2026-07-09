from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.decision_quality.interface import DecisionQualityResult
from app.services.replay.interface import ReplayResult


def evaluate_replay_result_v0(*, replay_result: ReplayResult) -> DecisionQualityResult:
    original_action = _extract_original_action(replay_result.metadata)
    original_confidence = _extract_original_confidence(replay_result.metadata)
    action_matches_original = original_action is not None and replay_result.reconstructed_action == original_action
    confidence_matches_original = (
        original_confidence is not None
        and replay_result.confidence is not None
        and replay_result.confidence == original_confidence
    )
    decision_reproduced = action_matches_original and confidence_matches_original
    quality_score = _calculate_quality_score(
        action_matches_original=action_matches_original,
        confidence_matches_original=confidence_matches_original,
        decision_reproduced=decision_reproduced,
    )

    return DecisionQualityResult(
        quality_score=quality_score,
        decision_reproduced=decision_reproduced,
        action_matches_original=action_matches_original,
        confidence_matches_original=confidence_matches_original,
        replay_duration_ms=_extract_replay_duration_ms(replay_result.metadata),
        evaluation_timestamp=datetime.now(timezone.utc),
    )


def _calculate_quality_score(*, action_matches_original: bool, confidence_matches_original: bool, decision_reproduced: bool) -> int:
    if decision_reproduced:
        return 100
    if action_matches_original or confidence_matches_original:
        return 50
    return 0


def _extract_original_action(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("original_action")
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized not in {"BUY", "SELL", "HOLD"}:
        return None
    return normalized


def _extract_original_confidence(metadata: dict[str, Any]) -> Decimal | None:
    value = metadata.get("original_confidence")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _extract_replay_duration_ms(metadata: dict[str, Any]) -> int | None:
    value = metadata.get("replay_duration_ms")
    if value is None:
        return 0
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return 0
    return max(duration, 0)
