from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app


def test_evaluate_replay_endpoint_returns_decision_quality_result() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/arena/evaluate-replay",
            json={
                "replay_id": str(uuid.uuid4()),
                "replay_agent_id": "11111111-1111-1111-1111-111111111111",
                "decision_package_id": "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "replay_timestamp": datetime(2026, 7, 9, 12, tzinfo=timezone.utc).isoformat(),
                "reconstructed_action": "BUY",
                "reconstructed_confidence": str(Decimal("0.875")),
                "supporting_evidence": [{"type": "decision_record"}],
                "explanation": "deterministic replay",
                "metadata": {
                    "original_action": "BUY",
                    "original_confidence": "0.875",
                    "replay_duration_ms": 12,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["quality_score"] == 100
    assert payload["decision_reproduced"] is True
    assert payload["action_matches_original"] is True
    assert payload["confidence_matches_original"] is True
