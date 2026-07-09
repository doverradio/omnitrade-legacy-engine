from __future__ import annotations

from app.services.research_agents.llm_adapter.contracts import (
    CritiqueCandidateRequest,
    CritiqueCandidateResponse,
    ExplainCandidateRequest,
    ExplainCandidateResponse,
    HypothesisRequest,
    HypothesisResponse,
    SummarizeLaboratoryRequest,
    SummarizeLaboratoryResponse,
)


class LLMResearchAgentAdapter:
    adapter_name: str = "LLM Adapter"
    provider: str = "unknown"
    capabilities: tuple[str, ...] = (
        "generate_hypotheses",
        "explain_candidate",
        "critique_candidate",
        "summarize_laboratory",
    )
    status: str = "PLANNED"

    def generate_hypotheses(self, request: HypothesisRequest) -> HypothesisResponse:
        raise NotImplementedError()

    def explain_candidate(self, request: ExplainCandidateRequest) -> ExplainCandidateResponse:
        raise NotImplementedError()

    def critique_candidate(self, request: CritiqueCandidateRequest) -> CritiqueCandidateResponse:
        raise NotImplementedError()

    def summarize_laboratory(self, request: SummarizeLaboratoryRequest) -> SummarizeLaboratoryResponse:
        raise NotImplementedError()
