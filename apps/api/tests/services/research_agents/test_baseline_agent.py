from __future__ import annotations

from app.services.research_agents.registry import list_generated_strategy_candidates


def test_baseline_agent_generates_proposed_candidate() -> None:
    candidates = list_generated_strategy_candidates()

    assert len(candidates) == 5
    for candidate in candidates:
        assert candidate.originating_agent == "Baseline Research Agent"
        assert candidate.strategy_name
        assert isinstance(candidate.parameter_set, dict)
        assert candidate.status == "PROPOSED"


def test_baseline_agent_candidate_ids_are_deterministic() -> None:
    first = list_generated_strategy_candidates()
    second = list_generated_strategy_candidates()

    assert len(first) == 5
    assert len(second) == 5
    first_ids = [item.candidate_id for item in first]
    second_ids = [item.candidate_id for item in second]
    assert first_ids == second_ids
