from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.replay_agent import ReplayResultResponse
from app.services.replay.registry import DEFAULT_REPLAY_AGENT_ID, list_registered_replay_agents


def test_default_replay_agent_registration_exists() -> None:
    agents = list_registered_replay_agents()

    assert len(agents) == 1
    agent = agents[0]
    assert agent.replay_agent_id == DEFAULT_REPLAY_AGENT_ID
    assert agent.name == "Default Replay Agent"
    assert agent.status == "Registered"
    assert agent.decision_package_consumer is True
    assert agent.execution_logic is False
    assert agent.processing_enabled is False
    assert agent.scheduling_enabled is False
    assert agent.writes_enabled is False


def test_replay_agent_registration_endpoint_serializes_placeholder_agent() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/arena/replay-agents")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["replay_agent_id"] == str(DEFAULT_REPLAY_AGENT_ID)
    assert payload[0]["name"] == "Default Replay Agent"
    assert payload[0]["status"] == "Registered"
    assert payload[0]["capabilities"][0]["name"] == "Decision Package consumer"


def test_replay_result_response_serializes_interface_fields() -> None:
    replay_id = uuid.uuid4()
    decision_package_id = uuid.uuid4()
    response = ReplayResultResponse(
        replay_id=replay_id,
        replay_agent_id=DEFAULT_REPLAY_AGENT_ID,
        strategy_name="MA Crossover",
        decision_package_id=decision_package_id,
        replay_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        decision_outcome="BUY",
        confidence=Decimal("0.875"),
        supporting_evidence=[{"type": "decision_package"}],
        explanation="Read-only placeholder replay result.",
        simulated_execution_metrics={"slippage_bps": 5},
        risk_assessment={"risk_state": "unknown"},
        quality_metrics={"quality_state": "placeholder"},
        metadata={"mode": "read_only"},
    )

    payload = response.model_dump(mode="json")

    assert payload["replay_id"] == str(replay_id)
    assert payload["replay_agent_id"] == str(DEFAULT_REPLAY_AGENT_ID)
    assert payload["decision_package_id"] == str(decision_package_id)
    assert payload["decision_outcome"] == "BUY"
    assert payload["confidence"] == "0.875"
