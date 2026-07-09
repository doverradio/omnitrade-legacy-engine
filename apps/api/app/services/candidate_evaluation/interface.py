from __future__ import annotations

from dataclasses import dataclass
import uuid


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    evaluation_id: uuid.UUID
    candidate_id: uuid.UUID
    replay_status: str
    decision_quality_score: int
    ai_coach_summary: str
    decision_intelligence_summary: str
    tournament_rank: int | None
    promotion_eligible: bool
