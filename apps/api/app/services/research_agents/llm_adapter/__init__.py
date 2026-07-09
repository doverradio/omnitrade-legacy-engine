from app.services.research_agents.llm_adapter.contracts import (
    CandidateHistoryItem,
    CritiqueCandidateRequest,
    CritiqueCandidateResponse,
    ExplainCandidateRequest,
    ExplainCandidateResponse,
    HypothesisRequest,
    HypothesisResponse,
    SummarizeLaboratoryRequest,
    SummarizeLaboratoryResponse,
    TournamentHistoryItem,
)
from app.services.research_agents.llm_adapter.interface import LLMResearchAgentAdapter
from app.services.research_agents.llm_adapter.registry import (
    AdapterProvider,
    LLMAdapterRegistration,
    clear_registered_llm_research_adapters_for_testing,
    create_adapter,
    list_registered_llm_research_adapters,
    register_llm_research_adapter,
)
