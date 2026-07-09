from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.schemas.research_agents import ResearchAgentResponse, StrategyCandidateResponse


def test_research_agent_serialization() -> None:
    payload = ResearchAgentResponse(
        agent_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
        agent_name="Baseline Research Agent",
        capabilities=["Generate deterministic candidate strategies"],
    )

    serialized = payload.model_dump(mode="json")
    assert serialized["agent_name"] == "Baseline Research Agent"
    assert serialized["capabilities"] == ["Generate deterministic candidate strategies"]


def test_strategy_candidate_serialization() -> None:
    payload = StrategyCandidateResponse(
        candidate_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        generated_at=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        originating_agent="Baseline Research Agent",
        strategy_name="Volatility Filter MA-RSI Blend",
        description="Deterministic blend candidate",
        parameter_set={"fast_period": 12},
        rationale="Deterministic baseline",
        status="PROPOSED",
    )

    serialized = payload.model_dump(mode="json")
    assert serialized["originating_agent"] == "Baseline Research Agent"
    assert serialized["status"] == "PROPOSED"
