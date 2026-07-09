from __future__ import annotations

from dataclasses import dataclass
import uuid

from app.services.research_agents.baseline_agent import BaselineResearchAgent
from app.services.research_agents.interface import StrategyCandidate


@dataclass(frozen=True, slots=True)
class ResearchAgentRegistration:
    agent_id: uuid.UUID
    agent_name: str
    capabilities: tuple[str, ...]


_BASELINE_AGENT = BaselineResearchAgent()
_REGISTERED_RESEARCH_AGENTS: tuple[BaselineResearchAgent, ...] = (_BASELINE_AGENT,)


def list_registered_research_agents() -> tuple[ResearchAgentRegistration, ...]:
    return tuple(
        ResearchAgentRegistration(
            agent_id=agent.agent_id,
            agent_name=agent.agent_name,
            capabilities=agent.capabilities,
        )
        for agent in _REGISTERED_RESEARCH_AGENTS
    )


def list_generated_strategy_candidates() -> tuple[StrategyCandidate, ...]:
    candidates: list[StrategyCandidate] = []
    for agent in _REGISTERED_RESEARCH_AGENTS:
        candidates.extend(agent.generate_candidates())
    return tuple(candidates)
