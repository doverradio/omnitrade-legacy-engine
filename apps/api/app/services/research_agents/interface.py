from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
import uuid


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    candidate_id: uuid.UUID
    generated_at: datetime
    originating_agent: str
    strategy_name: str
    description: str
    parameter_set: dict[str, Any]
    rationale: str
    status: str


class ResearchAgent(Protocol):
    agent_id: uuid.UUID
    agent_name: str
    capabilities: tuple[str, ...]

    def generate_candidates(self) -> tuple[StrategyCandidate, ...]: ...
