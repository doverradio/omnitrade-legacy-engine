from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer

from app.services.replay.interface import ReplayResult


class DecisionQualityEvaluationRequest(BaseModel):
    replay_id: uuid.UUID
    replay_agent_id: uuid.UUID
    decision_package_id: str
    replay_timestamp: datetime
    reconstructed_action: Literal["BUY", "SELL", "HOLD"]
    reconstructed_confidence: Decimal | None = None
    supporting_evidence: list[dict[str, object]]
    explanation: str | None = None
    metadata: dict[str, object]

    def to_replay_result(self) -> ReplayResult:
        return ReplayResult(
            replay_id=self.replay_id,
            replay_agent_id=self.replay_agent_id,
            decision_package_id=self.decision_package_id,
            replay_timestamp=self.replay_timestamp,
            reconstructed_action=self.reconstructed_action,
            confidence=self.reconstructed_confidence,
            supporting_evidence=tuple(self.supporting_evidence),
            explanation=self.explanation,
            metadata=self.metadata,
        )


class DecisionQualityResultResponse(BaseModel):
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

    @field_serializer(
        "calibration",
        "opportunity_cost",
        "drawdown",
        "risk_adjusted_return",
        "explanation_quality",
        when_used="json",
    )
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")
