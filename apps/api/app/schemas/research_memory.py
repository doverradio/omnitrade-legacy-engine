from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel


class ResearchMemoryLaboratoryRunResponse(BaseModel):
    laboratory_run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    participating_agents: list[str]
    candidates_generated: int
    candidates_evaluated: int


class ResearchMemoryCandidateResponse(BaseModel):
    class ParameterDiffResponse(BaseModel):
        parameter_name: str
        previous_value: int
        new_value: int

    laboratory_run_id: uuid.UUID
    candidate_id: uuid.UUID
    originating_agent: str
    parameter_set: dict[str, Any]
    evaluation_summary: str | None
    quality_score: int | None
    tournament_rank: int | None
    status: str
    parent_candidate_id: uuid.UUID | None
    generation: int
    mutation_reason: str | None
    parameter_diff: list[ParameterDiffResponse]


class ResearchMemorySummaryResponse(BaseModel):
    total_laboratory_runs: int
    total_candidates: int
    highest_quality_candidate: ResearchMemoryCandidateResponse | None
    average_quality_score: float | None
    latest_laboratory_run: ResearchMemoryLaboratoryRunResponse | None
