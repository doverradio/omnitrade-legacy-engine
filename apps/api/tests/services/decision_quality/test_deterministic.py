from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.replay.interface import ReplayResult


def _replay_result(*, action: str, confidence: str, metadata: dict[str, object]) -> ReplayResult:
    return ReplayResult(
        replay_id=uuid.uuid4(),
        replay_agent_id=uuid.uuid4(),
        decision_package_id="dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        replay_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        reconstructed_action=action,
        confidence=Decimal(confidence),
        supporting_evidence=tuple(),
        explanation="deterministic replay",
        metadata=metadata,
    )


def test_evaluate_replay_result_marks_matching_replay() -> None:
    replay_result = _replay_result(
        action="BUY",
        confidence="0.875",
        metadata={"original_action": "BUY", "original_confidence": "0.875", "replay_duration_ms": 12},
    )

    result = evaluate_replay_result_v0(replay_result=replay_result)

    assert result.quality_score == 100
    assert result.decision_reproduced is True
    assert result.action_matches_original is True
    assert result.confidence_matches_original is True
    assert result.replay_duration_ms == 12


def test_evaluate_replay_result_marks_mismatching_replay() -> None:
    replay_result = _replay_result(
        action="SELL",
        confidence="0.400",
        metadata={"original_action": "BUY", "original_confidence": "0.875", "replay_duration_ms": 0},
    )

    result = evaluate_replay_result_v0(replay_result=replay_result)

    assert result.quality_score == 0
    assert result.decision_reproduced is False
    assert result.action_matches_original is False
    assert result.confidence_matches_original is False
    assert result.replay_duration_ms == 0
