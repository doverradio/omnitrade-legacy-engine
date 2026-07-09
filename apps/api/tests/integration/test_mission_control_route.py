from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.routes.mission_control import router as mission_control_router
from app.main import create_app
from app.schemas.mission_control import (
    MissionControlIntelligenceHistoryPointResponse,
    MissionControlIntelligenceMetricResponse,
    MissionControlIntelligenceResponse,
    MissionControlIntelligenceTimelineEventResponse,
    MissionControlIntelligenceTrendResponse,
)
from app.schemas.operations import OperationalAlertResponse, OperationalHealthIndicatorResponse, OperationalMonitoringResponse, OperationalRunStatusResponse, OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunResponse


class _DummySession:
    pass


def _payload() -> MissionControlIntelligenceResponse:
    operations = OperationalStatusResponse(
        overall_health="green",
        run_status=OperationalRunStatusResponse(
            run_id="run-1",
            started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
            expected_end=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
            uptime="24:00:00",
            current_phase="researching",
            health_status="green",
        ),
        system_health={
            "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
            "orchestrator": OperationalHealthIndicatorResponse(state="green", detail="Heartbeat active"),
            "database": OperationalHealthIndicatorResponse(state="green", detail="Database connected"),
            "research_agent": OperationalHealthIndicatorResponse(state="green", detail="OpenAI research adapter available"),
        },
        research_status={"current_campaign": "Campaign Alpha", "current_champion": "RSI Mean Reversion", "campaign_status": "RUNNING"},
        monitoring=OperationalMonitoringResponse(
            candles_processed=120000,
            signals_generated=900,
            paper_trades_executed=120,
            decision_records_created=900,
            replay_count=140,
            candidate_count=80,
            campaign_count=3,
            laboratory_runs=25,
            evolution_count=44,
            current_champion="RSI Mean Reversion",
            paper_equity="104523.55",
            signals_today=42,
            trades_today=8,
            research_memory_growth=350,
        ),
        alerts=[OperationalAlertResponse(code="worker_stopped", severity="yellow", message="Worker stopped")],
    )

    return MissionControlIntelligenceResponse(
        version="v1",
        range="90d",
        generated_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        current_score=82,
        delta_label="+4 this week",
        confidence="High",
        trend=MissionControlIntelligenceTrendResponse(direction="up", label="Improving", delta_label="+4 this week", confidence="High"),
        history=[
            MissionControlIntelligenceHistoryPointResponse(
                timestamp=datetime(2026, 7, 9, 8, 0, tzinfo=timezone.utc),
                score=78,
                paper_equity="104000.00",
                paper_pnl="0.00",
                signals=20,
                trades=4,
                decision_count=40,
                health=80,
            ),
            MissionControlIntelligenceHistoryPointResponse(
                timestamp=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                score=82,
                paper_equity="104523.55",
                paper_pnl="523.55",
                signals=42,
                trades=8,
                decision_count=82,
                health=84,
            ),
        ],
        timeline_events=[
            MissionControlIntelligenceTimelineEventResponse(
                event_id="validation-1",
                timestamp=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
                title="Validation Run Started",
                description="Validation run is now active.",
                related_validation_run="11111111-1111-1111-1111-111111111111",
                health_at_that_moment=80,
                paper_equity="104523.55",
                paper_pnl="4523.55",
                signals=42,
                trades=8,
                decision_count=82,
                severity="green",
                category="system",
                event_type="VALIDATION_RUN_STARTED",
                metadata={},
            )
        ],
        metric_breakdown=[
            MissionControlIntelligenceMetricResponse(
                name="Prediction Quality",
                score=82,
                trend=MissionControlIntelligenceTrendResponse(direction="up", label="Improving", delta_label="+4 this week", confidence="High"),
                sparkline=[74, 76, 78, 79, 81, 82],
                details="Validation health, signal generation, and decision activity.",
            )
        ],
        operations=operations,
        validation_runs=[
            ValidationRunResponse(
                validation_run_id="11111111-1111-1111-1111-111111111111",
                name="72h Proving",
                objective="Validate stability",
                duration_hours=72,
                status="RUNNING",
                started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
                expected_end_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
                completed_at=None,
                paper_capital=Decimal("100000"),
                enabled_strategies=["MA Crossover"],
                enabled_research_agents=["Baseline"],
                enabled_research_features=["Laboratory"],
                health_score=88,
                result_status="INCOMPLETE",
            )
        ],
        selected_validation_run_id="11111111-1111-1111-1111-111111111111",
        notes="Mission Control Intelligence Center V1 is a deterministic placeholder built from available operational metrics. It is informational only and does not change trading, research, or allocation behavior.",
    )


def test_mission_control_intelligence_route_returns_shape(monkeypatch) -> None:
    app = create_app()

    async def _override_db():
        yield _DummySession()

    async def _service_stub(*_args, **_kwargs):
        return _payload()

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.mission_control.build_mission_control_intelligence", _service_stub)

    with TestClient(app) as client:
        response = client.get("/mission-control/intelligence?range=90d")

    assert response.status_code == 200
    payload = response.json()

    assert payload["version"] == "v1"
    assert payload["range"] == "90d"
    assert payload["current_score"] == 82
    assert payload["trend"]["direction"] == "up"
    assert payload["operations"]["overall_health"] == "green"
    assert payload["validation_runs"]
    assert payload["timeline_events"]