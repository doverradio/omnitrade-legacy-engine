from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel


class ResearchAgentResponse(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    capabilities: list[str]


class StrategyCandidateResponse(BaseModel):
    candidate_id: uuid.UUID
    generated_at: datetime
    originating_agent: str
    strategy_name: str
    description: str
    parameter_set: dict[str, Any]
    rationale: str
    status: str
