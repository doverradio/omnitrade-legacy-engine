from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel


class ResearchCampaignCreateRequest(BaseModel):
    name: str
    objective: str


class ResearchCampaignResponse(BaseModel):
    campaign_id: uuid.UUID
    name: str
    objective: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    participating_agents: list[str]
    laboratory_runs: int
    candidates_generated: int
    candidates_evaluated: int
    best_candidate: str | None
    best_quality_score: int | None
    current_champion: str | None
