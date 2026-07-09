from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass(frozen=True, slots=True)
class ResearchCampaign:
    campaign_id: uuid.UUID
    name: str
    objective: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    participating_agents: tuple[str, ...]
    laboratory_runs: int
    candidates_generated: int
    candidates_evaluated: int
    best_candidate: str | None
    best_quality_score: int | None
    current_champion: str | None
