from __future__ import annotations

from app.services.research_agents.registry import list_registered_research_agents


def test_research_agent_registration_contains_baseline_agent() -> None:
    agents = list_registered_research_agents()

    assert len(agents) == 1
    assert agents[0].agent_name == "Baseline Research Agent"
    assert "Generate deterministic candidate strategies" in agents[0].capabilities
