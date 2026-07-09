from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import uuid

from pydantic import BaseModel, field_serializer


class ValidationRunCreateRequest(BaseModel):
    name: str
    objective: str
    duration_hours: int
    paper_capital: Decimal
    enabled_strategies: list[str]
    enabled_research_agents: list[str]
    enabled_research_features: list[str]


class ValidationRunScorecardResponse(BaseModel):
    category: str
    status: str
    score: int
    notes: str


class ValidationRunResponse(BaseModel):
    validation_run_id: uuid.UUID
    name: str
    objective: str
    duration_hours: int
    status: str
    started_at: datetime | None
    expected_end_at: datetime | None
    completed_at: datetime | None
    paper_capital: Decimal
    enabled_strategies: list[str]
    enabled_research_agents: list[str]
    enabled_research_features: list[str]
    health_score: int | None
    result_status: str

    @field_serializer("paper_capital", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class ValidationRunDetailResponse(ValidationRunResponse):
    overall_score: int
    scorecards: list[ValidationRunScorecardResponse]


class ValidationRunEventResponse(BaseModel):
    event_type: str
    message: str
    payload: dict[str, object]
    created_at: datetime


class ValidationRunMetricsResponse(BaseModel):
    elapsed_percentage: float
    time_remaining: str
    candles_processed_during_run: int
    signals_generated_during_run: int
    trades_executed_during_run: int
    decision_records_created_during_run: int
    paper_pnl_during_run: str
    current_equity: str
    current_champion: str | None
    candidates_generated: int
    candidates_evaluated: int
    evolution_descendants: int
    research_memory_growth: int
    alerts_count: int


class ValidationRunStartResponse(BaseModel):
    run: ValidationRunResponse
    initial_metrics: ValidationRunMetricsResponse


class ValidationRunListResponse(BaseModel):
    items: list[ValidationRunResponse]
