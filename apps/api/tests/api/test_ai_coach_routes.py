from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.schemas.ai_coach import AICoachObservationResponse


def test_ai_coach_observation_response_serializes_fields() -> None:
    response = AICoachObservationResponse(
        observation_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        evaluation_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        summary="Replay successfully reproduced the production decision.",
        strengths=["Replay successfully reproduced the production decision."],
        weaknesses=[],
        confidence_note="Confidence aligned with the original decision.",
        reproducibility_note="Replay reproduced the production decision exactly.",
        suggested_follow_up="Use this replay as a deterministic baseline for future comparisons.",
    )

    payload = response.model_dump(mode="json")

    assert payload["summary"] == "Replay successfully reproduced the production decision."
    assert payload["strengths"] == ["Replay successfully reproduced the production decision."]
    assert payload["weaknesses"] == []
