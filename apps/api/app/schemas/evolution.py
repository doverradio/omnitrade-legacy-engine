from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel


class EvolutionMutationResponse(BaseModel):
    parameter_name: str
    previous_value: int
    new_value: int


class EvolvedCandidateResponse(BaseModel):
    candidate_id: uuid.UUID
    parent_candidate_id: uuid.UUID
    generation: int
    mutation_reason: str
    parameter_diff: list[EvolutionMutationResponse]
    parameter_set: dict[str, Any]
    generated_at: datetime
    quality_score: int | None
    tournament_rank: int | None
    status: str


class EvolutionRequest(BaseModel):
    parent_candidate_id: uuid.UUID | None = None
    generation_limit: int | None = None


class EvolutionResponse(BaseModel):
    generated_count: int
    descendants: list[EvolvedCandidateResponse]
