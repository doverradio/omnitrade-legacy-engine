from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.operations import OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunResponse


class MissionControlIntelligenceTrendResponse(BaseModel):
    direction: str
    label: str
    delta_label: str
    confidence: str


class MissionControlIntelligenceHistoryPointResponse(BaseModel):
    timestamp: datetime
    score: int
    paper_equity: str
    paper_pnl: str
    signals: int
    trades: int
    decision_count: int
    health: int


class MissionControlIntelligenceTimelineEventResponse(BaseModel):
    event_id: str
    timestamp: datetime
    title: str
    description: str
    related_validation_run: str | None
    health_at_that_moment: int | None
    paper_equity: str | None
    paper_pnl: str | None
    signals: int | None
    trades: int | None
    decision_count: int | None
    severity: str
    category: str
    event_type: str
    metadata: dict[str, object]


class MissionControlIntelligenceMetricResponse(BaseModel):
    name: str
    score: int
    trend: MissionControlIntelligenceTrendResponse
    sparkline: list[int]
    details: str


class MissionControlIntelligenceResponse(BaseModel):
    version: str
    range: str
    generated_at: datetime
    current_score: int
    delta_label: str
    confidence: str
    trend: MissionControlIntelligenceTrendResponse
    history: list[MissionControlIntelligenceHistoryPointResponse]
    timeline_events: list[MissionControlIntelligenceTimelineEventResponse]
    metric_breakdown: list[MissionControlIntelligenceMetricResponse]
    operations: OperationalStatusResponse
    validation_runs: list[ValidationRunResponse]
    selected_validation_run_id: str | None
    notes: str