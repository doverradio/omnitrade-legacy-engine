from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass(frozen=True, slots=True)
class ResearchLaboratoryRun:
    laboratory_run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    participating_agents: tuple[str, ...]
    generated_candidates: int
    evaluated_candidates: int
    status: str


@dataclass(frozen=True, slots=True)
class ResearchLaboratoryStatus:
    status: str
    registered_agents: tuple[str, ...]
    last_run: ResearchLaboratoryRun | None
    candidates_generated: int
    candidates_evaluated: int
    success_rate: str
