from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer

from app.services.decision_quality.interface import DecisionQualityResult


class AICoachReviewRequest(BaseModel):
    quality_score: int
    decision_reproduced: bool
    action_matches_original: bool
    confidence_matches_original: bool
    replay_duration_ms: int | None
    evaluation_timestamp: datetime
    calibration: Decimal | None = None
    opportunity_cost: Decimal | None = None
    drawdown: Decimal | None = None
    risk_adjusted_return: Decimal | None = None
    explanation_quality: Decimal | None = None

    def to_decision_quality_result(self) -> DecisionQualityResult:
        return DecisionQualityResult(
            quality_score=self.quality_score,
            decision_reproduced=self.decision_reproduced,
            action_matches_original=self.action_matches_original,
            confidence_matches_original=self.confidence_matches_original,
            replay_duration_ms=self.replay_duration_ms,
            evaluation_timestamp=self.evaluation_timestamp,
            calibration=self.calibration,
            opportunity_cost=self.opportunity_cost,
            drawdown=self.drawdown,
            risk_adjusted_return=self.risk_adjusted_return,
            explanation_quality=self.explanation_quality,
        )


class AICoachObservationResponse(BaseModel):
    observation_id: uuid.UUID
    evaluation_timestamp: datetime
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    confidence_note: str
    reproducibility_note: str
    suggested_follow_up: str

    @field_serializer("strengths", "weaknesses", when_used="json")
    def serialize_text_lists(self, value: list[str]) -> list[str]:
        return value
