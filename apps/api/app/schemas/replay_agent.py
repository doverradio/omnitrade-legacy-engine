from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer


class ReplayRequest(BaseModel):
    decision_package_id: str


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
    decision_package_id: str
    replay_timestamp: datetime
    reconstructed_action: Literal["BUY", "SELL", "HOLD"]
    reconstructed_confidence: Decimal | None = None
    supporting_evidence: list[dict[str, object]]
    explanation: str | None = None
    metadata: dict[str, object]

    @field_serializer("reconstructed_confidence", when_used="json")
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")
