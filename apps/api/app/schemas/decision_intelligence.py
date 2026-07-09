from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DecisionIntelligenceRecommendationResponse(BaseModel):
    recommendation_id: uuid.UUID
    generated_at: datetime
    compared_strategies: list[str]
    highest_quality_strategy: str | None
    evidence_summary: str
    confidence_summary: str
    recommendation_summary: str
    human_review_required: bool
    promotion_recommended: bool
