from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel


class ResearchLaboratoryRunResponse(BaseModel):
    laboratory_run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    participating_agents: list[str]
    generated_candidates: int
    evaluated_candidates: int
    status: str


class ResearchLaboratoryStatusResponse(BaseModel):
    status: str
    registered_agents: list[str]
    last_run: ResearchLaboratoryRunResponse | None
    candidates_generated: int
    candidates_evaluated: int
    success_rate: str
