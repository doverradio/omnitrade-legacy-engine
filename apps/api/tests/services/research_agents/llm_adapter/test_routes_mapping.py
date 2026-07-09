from __future__ import annotations

from app.schemas.llm_adapter import LLMAdapterResponse
from app.services.research_agents.llm_adapter.registry import (
    clear_registered_llm_research_adapters_for_testing,
    list_registered_llm_research_adapters,
    register_llm_research_adapter,
)


def test_llm_adapter_response_serialization() -> None:
    clear_registered_llm_research_adapters_for_testing()

    registration = register_llm_research_adapter(
        adapter_name="Local Model Agent",
        provider="local",
        capabilities=("generate_hypotheses", "summarize_laboratory"),
    )

    response = LLMAdapterResponse(
        adapter_id=registration.adapter_id,
        adapter_name=registration.adapter_name,
        provider=registration.provider,
        capabilities=list(registration.capabilities),
        status=registration.status,
    )

    payload = response.model_dump()
    assert payload["adapter_name"] == "Local Model Agent"
    assert payload["provider"] == "local"
    assert payload["status"] == "PLANNED"

    clear_registered_llm_research_adapters_for_testing()
    assert list_registered_llm_research_adapters() == tuple()
