from __future__ import annotations

from app.services.research_agents.registry import list_generated_strategy_candidates


def test_baseline_agent_generates_proposed_candidate() -> None:
    candidates = list_generated_strategy_candidates()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.originating_agent == "Baseline Research Agent"
    assert candidate.strategy_name
    assert isinstance(candidate.parameter_set, dict)
    assert candidate.status == "PROPOSED"
