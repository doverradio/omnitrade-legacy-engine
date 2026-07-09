from __future__ import annotations

from app.services.research_agents.llm_adapter.registry import (
    clear_registered_llm_research_adapters_for_testing,
    list_registered_llm_research_adapters,
    register_llm_research_adapter,
)


def test_llm_adapter_registration_and_listing() -> None:
    clear_registered_llm_research_adapters_for_testing()

    register_llm_research_adapter(
        adapter_name="OpenAI Agent",
        provider="openai",
        capabilities=("generate_hypotheses", "explain_candidate"),
        status="PLANNED",
    )
    register_llm_research_adapter(
        adapter_name="Anthropic Agent",
        provider="anthropic",
        capabilities=("generate_hypotheses", "critique_candidate"),
        status="PLANNED",
    )
    register_llm_research_adapter(
        adapter_name="Gemini Agent",
        provider="gemini",
        capabilities=("summarize_laboratory",),
        status="PLANNED",
    )
    register_llm_research_adapter(
        adapter_name="Local Model Agent",
        provider="local",
        capabilities=("generate_hypotheses", "summarize_laboratory"),
        status="PLANNED",
    )

    registrations = list_registered_llm_research_adapters()
    assert len(registrations) == 4
    assert {item.provider for item in registrations} == {"openai", "anthropic", "gemini", "local"}
    assert all(item.status == "PLANNED" for item in registrations)

    clear_registered_llm_research_adapters_for_testing()
