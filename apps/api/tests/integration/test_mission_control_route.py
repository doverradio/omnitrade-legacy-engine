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
    MissionControlSnapshotHistoryPointResponse,
    MissionControlSnapshotHistoryResponse,
    MissionControlIntelligenceTimelineEventResponse,
    MissionControlIntelligenceTrendResponse,
)
from app.schemas.operations import OperationalAlertResponse, OperationalHealthIndicatorResponse, OperationalMonitoringResponse, OperationalRunStatusResponse, OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunResponse


class _DummySession:
    async def execute(self, statement, params=None):
        _ = (statement, params)
        return _ResultWithScalar(0)


class _ValidationEventResponse:
    def __init__(self, items):
        self.items = items


def _run_event() -> object:
    return type(
        "RunEvent",
        (),
        {
            "id": 1,
            "timestamp": datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
            "title": "Validation Event",
            "description": "Validation event emitted",
            "validation_run_id": "11111111-1111-1111-1111-111111111111",
            "category": "system",
            "event_type": "VALIDATION_EVENT",
            "severity": "green",
            "metadata": {},
        },
    )()


class _ResultWithScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def mappings(self):
        return self

    def first(self):
        return self._value




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


def test_mission_control_intelligence_history_route_returns_annotations(monkeypatch) -> None:
    app = create_app()

    async def _override_db():
        yield _DummySession()

    async def _history_stub(*_args, **_kwargs):
        return MissionControlSnapshotHistoryResponse(
            range="24h",
            dimension=None,
            generated_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
            points=[
                MissionControlSnapshotHistoryPointResponse(
                    snapshot_id="snapshot-1",
                    captured_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                    bucket_start=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                    bucket_end=datetime(2026, 7, 9, 10, 15, tzinfo=timezone.utc),
                    overall_score=82,
                    confidence="High",
                    data_completeness=100,
                    market_awareness_score=80,
                    decision_quality_score=82,
                    execution_reliability_score=84,
                    risk_discipline_score=77,
                    research_progress_score=79,
                    adaptation_rate_score=75,
                    operational_health_score=93,
                    capital_efficiency_score=81,
                    profit_performance_score=83,
                    paper_net_profit="523.55",
                    live_net_profit="0.00",
                    combined_net_profit="523.55",
                    paper_equity="104523.55",
                    live_equity="0.00",
                    combined_equity="104523.55",
                    realized_pnl="523.55",
                    unrealized_pnl="120.00",
                    fees="12.50",
                    drawdown_percent="0.09",
                    source_counts={"paper_trades": 8},
                    annotations=[
                        {
                            "event_type": "risk_guardrail_triggered",
                            "title": "Guardrail Triggered",
                            "required_action": "operator_review",
                        }
                    ],
                    schema_version="v1",
                )
            ],
        )

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.mission_control.build_snapshot_history", _history_stub)

    with TestClient(app) as client:
        response = client.get("/mission-control/intelligence/history?range=24h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["points"][0]["annotations"][0]["title"] == "Guardrail Triggered"
    assert payload["points"][0]["annotations"][0]["required_action"] == "operator_review"


def _operations_payload(*, paper_equity: str) -> OperationalStatusResponse:
    return OperationalStatusResponse(
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
            paper_equity=paper_equity,
            signals_today=42,
            trades_today=8,
            research_memory_growth=350,
        ),
        alerts=[],
    )


def _validation_run() -> ValidationRunResponse:
    return ValidationRunResponse(
        validation_run_id="11111111-1111-1111-1111-111111111111",
        name="72h Proving",
        objective="Validate stability",
        duration_hours=72,
        status="RUNNING",
        started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
        expected_end_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=None,
        paper_capital=Decimal("25"),
        enabled_strategies=["MA Crossover"],
        enabled_research_agents=["Baseline"],
        enabled_research_features=["Laboratory"],
        health_score=88,
        result_status="INCOMPLETE",
    )


def test_mission_control_intelligence_route_uses_evidence_backed_bound_account_pnl(monkeypatch) -> None:
    app = create_app()

    async def _read(operation, *, operation_name):
        _ = operation_name
        return await operation(_DummySession())

    async def _operations_stub(*_args, **_kwargs):
        return _operations_payload(paper_equity="25")

    async def _runs_stub(*_args, **_kwargs):
        return [_validation_run()]

    async def _events_stub(*_args, **_kwargs):
        return _ValidationEventResponse([_run_event()])

    async def _campaign_metrics_stub(*_args, **_kwargs):
        return {
            "campaigns_near_profit_target": 0,
            "campaigns_at_target": 0,
            "profit_eligible_for_compounding": Decimal("0"),
            "profit_recommended_for_withdrawal": Decimal("0"),
            "profit_awaiting_review": Decimal("0"),
            "active_compounding_policies": 0,
        }

    async def _total_capital_stub(*_args, **_kwargs):
        return Decimal("25")

    async def _no_live_annotations(*_args, **_kwargs):
        return []

    async def _timeline_stub(*_args, **_kwargs):
        return (
            "25.00",
            "0.00",
            {
                "paper_pnl_source": "bound_paper_account",
                "paper_pnl_status": "evidence_backed",
                "paper_pnl_baseline": "25.00",
                "paper_pnl_bound_account_count": 1,
            },
        )

    monkeypatch.setattr("app.api.routes.mission_control.run_read_with_retry", _read)
    monkeypatch.setattr("app.services.mission_control_intelligence.build_operations_status", _operations_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence.list_validation_runs", _runs_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence.list_validation_run_events", _events_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_total_managed_capital", _total_capital_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_campaign_profit_metrics", _campaign_metrics_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._resolve_timeline_equity_and_pnl", _timeline_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_live_operation_annotations", _no_live_annotations)

    with TestClient(app) as client:
        response = client.get("/mission-control/intelligence?range=24h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timeline_events"]
    metadata = payload["timeline_events"][0]["metadata"]
    assert metadata["paper_pnl_source"] == "bound_paper_account"
    assert metadata["paper_pnl_status"] == "evidence_backed"
    assert payload["timeline_events"][0]["paper_pnl"] == "0.00"


def test_mission_control_intelligence_route_marks_unresolved_baseline_without_fabricated_pnl(monkeypatch) -> None:
    app = create_app()

    async def _read(operation, *, operation_name):
        _ = operation_name
        return await operation(_DummySession())

    async def _operations_stub(*_args, **_kwargs):
        return _operations_payload(paper_equity="25")

    async def _runs_stub(*_args, **_kwargs):
        return [_validation_run()]

    async def _events_stub(*_args, **_kwargs):
        return _ValidationEventResponse([_run_event()])

    async def _campaign_metrics_stub(*_args, **_kwargs):
        return {
            "campaigns_near_profit_target": 0,
            "campaigns_at_target": 0,
            "profit_eligible_for_compounding": Decimal("0"),
            "profit_recommended_for_withdrawal": Decimal("0"),
            "profit_awaiting_review": Decimal("0"),
            "active_compounding_policies": 0,
        }

    async def _total_capital_stub(*_args, **_kwargs):
        return Decimal("25")

    async def _no_live_annotations(*_args, **_kwargs):
        return []

    async def _timeline_stub(*_args, **_kwargs):
        return (
            "25.00",
            None,
            {
                "paper_pnl_source": "unavailable",
                "paper_pnl_status": "baseline_unresolved",
            },
        )

    async def _paper_equity_stub(*_args, **_kwargs):
        return Decimal("25")

    monkeypatch.setattr("app.api.routes.mission_control.run_read_with_retry", _read)
    monkeypatch.setattr("app.services.mission_control_intelligence.build_operations_status", _operations_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence.list_validation_runs", _runs_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence.list_validation_run_events", _events_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_total_managed_capital", _total_capital_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_campaign_profit_metrics", _campaign_metrics_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._resolve_timeline_equity_and_pnl", _timeline_stub)
    monkeypatch.setattr("app.services.mission_control_intelligence._load_live_operation_annotations", _no_live_annotations)

    with TestClient(app) as client:
        response = client.get("/mission-control/intelligence?range=24h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timeline_events"]
    metadata = payload["timeline_events"][0]["metadata"]
    assert metadata["paper_pnl_source"] == "unavailable"
    assert metadata["paper_pnl_status"] == "baseline_unresolved"
    assert payload["timeline_events"][0]["paper_pnl"] is None

