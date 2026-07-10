from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OperationalRunStatusResponse(BaseModel):
    run_id: str
    started_at: datetime
    expected_end: datetime
    uptime: str
    current_phase: str
    health_status: str


class OperationalHealthIndicatorResponse(BaseModel):
    state: str
    detail: str


class OperationalMonitoringResponse(BaseModel):
    candles_processed: int
    signals_generated: int
    paper_trades_executed: int
    decision_records_created: int
    replay_count: int
    candidate_count: int
    campaign_count: int
    laboratory_runs: int
    evolution_count: int
    current_champion: str | None
    paper_equity: str
    signals_today: int
    trades_today: int
    research_memory_growth: int


class OperationalAlertResponse(BaseModel):
    code: str
    severity: str
    message: str


class OperationalStatusResponse(BaseModel):
    overall_health: str
    run_status: OperationalRunStatusResponse
    system_health: dict[str, OperationalHealthIndicatorResponse]
    research_status: dict[str, str | int | None]
    monitoring: OperationalMonitoringResponse
    alerts: list[OperationalAlertResponse]


class OperationalFreshnessItemResponse(BaseModel):
    source: str
    latest_timestamp: datetime | None
    row_count: int


class OperationalFreshnessResponse(BaseModel):
    generated_at: datetime
    items: list[OperationalFreshnessItemResponse]
