from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.operations import OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunEventCategory, ValidationRunResponse


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
    category: ValidationRunEventCategory
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
    total_managed_capital: str | None = None
    campaigns_near_profit_target: int = 0
    campaigns_at_target: int = 0
    profit_eligible_for_compounding: str | None = None
    profit_recommended_for_withdrawal: str | None = None
    profit_awaiting_review: str | None = None
    active_compounding_policies: int = 0
    validation_runs: list[ValidationRunResponse]
    selected_validation_run_id: str | None
    notes: str


class MissionControlSnapshotHistoryPointResponse(BaseModel):
    snapshot_id: str
    captured_at: datetime
    bucket_start: datetime
    bucket_end: datetime
    overall_score: int | None
    confidence: str | None
    data_completeness: int | None
    market_awareness_score: int | None
    decision_quality_score: int | None
    execution_reliability_score: int | None
    risk_discipline_score: int | None
    research_progress_score: int | None
    adaptation_rate_score: int | None
    operational_health_score: int | None
    capital_efficiency_score: int | None
    profit_performance_score: int | None
    paper_net_profit: str | None
    live_net_profit: str | None
    combined_net_profit: str | None
    paper_equity: str | None
    live_equity: str | None
    combined_equity: str | None
    realized_pnl: str | None
    unrealized_pnl: str | None
    fees: str | None
    drawdown_percent: str | None
    source_counts: dict[str, int]
    annotations: list[dict[str, object]]
    schema_version: str


class MissionControlSnapshotHistoryResponse(BaseModel):
    range: str
    dimension: str | None
    points: list[MissionControlSnapshotHistoryPointResponse]
    generated_at: datetime