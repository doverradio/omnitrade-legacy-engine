from __future__ import annotations

from app.services.research_agents.llm_adapter.registry import LLMAdapterRegistration
from app.services.research_agents.openai.agent import OpenAIResearchAgent


_OPENAI_RESEARCH_AGENT = OpenAIResearchAgent()


def get_openai_research_agent() -> OpenAIResearchAgent:
    return _OPENAI_RESEARCH_AGENT


def get_openai_research_agent_registration() -> LLMAdapterRegistration:
    agent = get_openai_research_agent()
    return LLMAdapterRegistration(
        adapter_id=agent.adapter_id,
        adapter_name=agent.adapter_name,
        provider="openai",
        capabilities=agent.capabilities,
        status=agent.adapter_status,
    )
