from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer


class ReplayAgentCapabilityResponse(BaseModel):
    name: str
    description: str


class ReplayAgentRegistrationResponse(BaseModel):
    replay_agent_id: uuid.UUID
    name: str
    status: str
    capabilities: list[ReplayAgentCapabilityResponse]
    decision_package_consumer: bool
    execution_logic: bool
    processing_enabled: bool
    scheduling_enabled: bool
    writes_enabled: bool


class ReplayResultResponse(BaseModel):
    replay_id: uuid.UUID
    replay_agent_id: uuid.UUID
    strategy_name: str
    decision_package_id: uuid.UUID
    replay_timestamp: datetime
    decision_outcome: Literal["BUY", "SELL", "HOLD"]
    confidence: Decimal | None = None
    supporting_evidence: list[dict[str, object]]
    explanation: str | None = None
    simulated_execution_metrics: dict[str, object]
    risk_assessment: dict[str, object]
    quality_metrics: dict[str, object]
    metadata: dict[str, object]

    @field_serializer("confidence", when_used="json")
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")
