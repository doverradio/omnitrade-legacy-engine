from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.schemas.decision_quality import DecisionQualityEvaluationRequest, DecisionQualityResultResponse


def test_decision_quality_result_response_serializes_fields() -> None:
    result = DecisionQualityResultResponse(
        quality_score=100,
        decision_reproduced=True,
        action_matches_original=True,
        confidence_matches_original=True,
        replay_duration_ms=12,
        evaluation_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        calibration=Decimal("0.100"),
        opportunity_cost=None,
        drawdown=Decimal("0.050"),
        risk_adjusted_return=None,
        explanation_quality=Decimal("0.800"),
    )

    payload = result.model_dump(mode="json")

    assert payload["quality_score"] == 100
    assert payload["decision_reproduced"] is True
    assert payload["replay_duration_ms"] == 12
    assert payload["calibration"] == "0.100"
    assert payload["drawdown"] == "0.050"
    assert payload["explanation_quality"] == "0.800"
    assert payload["opportunity_cost"] is None


def test_decision_quality_request_round_trips_to_replay_result() -> None:
    request = DecisionQualityEvaluationRequest(
        replay_id=uuid.uuid4(),
        replay_agent_id=uuid.uuid4(),
        decision_package_id="dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        replay_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        reconstructed_action="BUY",
        reconstructed_confidence=Decimal("0.875"),
        supporting_evidence=[{"type": "decision_record"}],
        explanation="deterministic replay",
        metadata={"original_action": "BUY", "original_confidence": "0.875", "replay_duration_ms": 12},
    )

    replay_result = request.to_replay_result()

    assert replay_result.decision_package_id == request.decision_package_id
    assert replay_result.reconstructed_action == "BUY"
    assert replay_result.confidence == Decimal("0.875")
