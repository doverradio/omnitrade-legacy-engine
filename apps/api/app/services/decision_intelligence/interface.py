from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass(frozen=True, slots=True)
class DecisionIntelligenceRecommendation:
    recommendation_id: uuid.UUID
    generated_at: datetime
    compared_strategies: tuple[str, ...]
    highest_quality_strategy: str | None
    evidence_summary: str
    confidence_summary: str
    recommendation_summary: str
    human_review_required: bool
    promotion_recommended: bool
