from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.research_agents.registry import list_generated_strategy_candidates


def test_evaluate_candidate_route_returns_evaluation() -> None:
    app = create_app()
    candidate = list_generated_strategy_candidates()[0]

    with TestClient(app) as client:
        response = client.post(
            "/research/evaluate-candidate",
            json={"candidate_id": str(candidate.candidate_id)},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_id"] == str(candidate.candidate_id)
    assert payload["replay_status"] == "COMPLETED"
    assert payload["promotion_eligible"] is False


def test_evaluate_candidate_route_returns_not_found_for_unknown_candidate() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/research/evaluate-candidate",
            json={"candidate_id": "00000000-0000-0000-0000-000000000099"},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["message"] == "Strategy candidate not found"
