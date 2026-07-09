from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import uuid

from app.services.research_agents.llm_adapter.interface import LLMResearchAgentAdapter


AdapterProvider = Literal["openai", "anthropic", "gemini", "local"]


@dataclass(frozen=True, slots=True)
class LLMAdapterRegistration:
    adapter_id: uuid.UUID
    adapter_name: str
    provider: AdapterProvider
    capabilities: tuple[str, ...]
    status: str


_REGISTERED_LLM_ADAPTERS: tuple[LLMAdapterRegistration, ...] = tuple()


def register_llm_research_adapter(*, adapter_name: str, provider: AdapterProvider, capabilities: tuple[str, ...], status: str = "PLANNED") -> LLMAdapterRegistration:
    global _REGISTERED_LLM_ADAPTERS

    registration = LLMAdapterRegistration(
        adapter_id=uuid.uuid5(uuid.UUID("00000000-0000-0000-0000-000000000008"), f"{provider}:{adapter_name}"),
        adapter_name=adapter_name,
        provider=provider,
        capabilities=capabilities,
        status=status,
    )
    _REGISTERED_LLM_ADAPTERS = tuple(
        item
        for item in _REGISTERED_LLM_ADAPTERS
        if item.adapter_id != registration.adapter_id
    ) + (registration,)
    return registration


def list_registered_llm_research_adapters() -> tuple[LLMAdapterRegistration, ...]:
    return _REGISTERED_LLM_ADAPTERS


def clear_registered_llm_research_adapters_for_testing() -> None:
    global _REGISTERED_LLM_ADAPTERS
    _REGISTERED_LLM_ADAPTERS = tuple()


def create_adapter(adapter_class: type[LLMResearchAgentAdapter]) -> LLMResearchAgentAdapter:
    return adapter_class()
