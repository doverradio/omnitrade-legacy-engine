from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class DashboardIntelligenceComponentResponse(BaseModel):
    name: str
    score: int
    weight: int
    explanation: str


class DashboardIntelligenceTimelinePointResponse(BaseModel):
    timestamp: datetime
    score: int
    equity: Decimal
    decision_quality: int
    research_quality: int
    operational_health: int

    @field_serializer("equity", when_used="json")
    def serialize_decimal_field(self, value: Decimal) -> str:
        return format(value, "f")


class DashboardIntelligenceScoreResponse(BaseModel):
    score: int
    data_completeness: int
    range: str
    generated_at: datetime
    components: list[DashboardIntelligenceComponentResponse]
    timeline: list[DashboardIntelligenceTimelinePointResponse]