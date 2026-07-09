from __future__ import annotations

from app.services.research_agents.openai.agent import OpenAIResearchAgent
from app.services.research_agents.openai.registry import get_openai_research_agent_registration
from app.services.research_agents.openai import registry as openai_registry


class StubOpenAIClient:
    def __init__(self, *, available: bool) -> None:
        self.is_available = available


def test_openai_registration_exposes_available_status() -> None:
    previous = openai_registry._OPENAI_RESEARCH_AGENT
    try:
        openai_registry._OPENAI_RESEARCH_AGENT = OpenAIResearchAgent(client=StubOpenAIClient(available=True))

        registration = get_openai_research_agent_registration()

        assert registration.adapter_name == "OpenAI Research Agent"
        assert registration.provider == "openai"
        assert registration.status == "AVAILABLE"
        assert "generate_hypotheses" in registration.capabilities
    finally:
        openai_registry._OPENAI_RESEARCH_AGENT = previous


def test_openai_registration_exposes_unavailable_status() -> None:
    previous = openai_registry._OPENAI_RESEARCH_AGENT
    try:
        openai_registry._OPENAI_RESEARCH_AGENT = OpenAIResearchAgent(client=StubOpenAIClient(available=False))

        registration = get_openai_research_agent_registration()

        assert registration.status == "UNAVAILABLE"
    finally:
        openai_registry._OPENAI_RESEARCH_AGENT = previous
