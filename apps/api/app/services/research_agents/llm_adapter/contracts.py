from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel


class CandidateHistoryItem(BaseModel):
    candidate_id: uuid.UUID
    generation: int
    quality_score: int | None
    tournament_rank: int | None
    parameter_set: dict[str, Any]


class TournamentHistoryItem(BaseModel):
    tournament_id: uuid.UUID | None
    generated_at: datetime | None
    ranking: list[dict[str, Any]]


class HypothesisRequest(BaseModel):
    research_memory: dict[str, Any]
    evolution_analytics: dict[str, Any]
    candidate_history: list[CandidateHistoryItem]
    tournament_history: list[TournamentHistoryItem]


class HypothesisResponse(BaseModel):
    candidate_strategy: str
    rationale: str
    expected_behavior: str
    confidence: float


class ExplainCandidateRequest(BaseModel):
    candidate_id: uuid.UUID
    parameter_set: dict[str, Any]
    quality_score: int | None


class ExplainCandidateResponse(BaseModel):
    explanation: str


class CritiqueCandidateRequest(BaseModel):
    candidate_id: uuid.UUID
    parameter_set: dict[str, Any]
    quality_score: int | None
    tournament_rank: int | None


class CritiqueCandidateResponse(BaseModel):
    critique: str


class SummarizeLaboratoryRequest(BaseModel):
    laboratory_run_id: uuid.UUID | None
    run_summary: dict[str, Any]


class SummarizeLaboratoryResponse(BaseModel):
    summary: str
