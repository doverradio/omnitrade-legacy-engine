from __future__ import annotations

import pytest

from app.services.research_agents.llm_adapter.contracts import (
    CritiqueCandidateRequest,
    ExplainCandidateRequest,
    HypothesisRequest,
    SummarizeLaboratoryRequest,
)
from app.services.research_agents.llm_adapter.interface import LLMResearchAgentAdapter


def test_llm_adapter_methods_raise_not_implemented() -> None:
    adapter = LLMResearchAgentAdapter()

    with pytest.raises(NotImplementedError):
        adapter.generate_hypotheses(
            HypothesisRequest(
                research_memory={},
                evolution_analytics={},
                candidate_history=[],
                tournament_history=[],
            )
        )

    with pytest.raises(NotImplementedError):
        adapter.explain_candidate(
            ExplainCandidateRequest(
                candidate_id="00000000-0000-0000-0000-000000000001",
                parameter_set={},
                quality_score=None,
            )
        )

    with pytest.raises(NotImplementedError):
        adapter.critique_candidate(
            CritiqueCandidateRequest(
                candidate_id="00000000-0000-0000-0000-000000000001",
                parameter_set={},
                quality_score=None,
                tournament_rank=None,
            )
        )

    with pytest.raises(NotImplementedError):
        adapter.summarize_laboratory(
            SummarizeLaboratoryRequest(
                laboratory_run_id=None,
                run_summary={},
            )
        )
