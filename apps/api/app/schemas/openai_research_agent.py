from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.candidate_evaluation import CandidateEvaluationResponse
from app.schemas.research_agents import StrategyCandidateResponse


class OpenAIResearchGenerationResponse(BaseModel):
    status: str
    generated_candidates: list[StrategyCandidateResponse]
    evaluations: list[CandidateEvaluationResponse]
    generation_timestamp: datetime | None
    prompt_version: str | None
    response_duration_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
