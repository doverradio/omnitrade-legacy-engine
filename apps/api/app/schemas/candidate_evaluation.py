from __future__ import annotations

import uuid

from pydantic import BaseModel


class CandidateEvaluationRequest(BaseModel):
    candidate_id: uuid.UUID


class CandidateEvaluationResponse(BaseModel):
    evaluation_id: uuid.UUID
    candidate_id: uuid.UUID
    replay_status: str
    decision_quality_score: int
    ai_coach_summary: str
    decision_intelligence_summary: str
    tournament_rank: int | None
    promotion_eligible: bool
