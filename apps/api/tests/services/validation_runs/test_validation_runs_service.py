from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from app.models.validation_run import ValidationRun
from app.models.validation_run_event import ValidationRunEvent
from app.models.validation_run_metric import ValidationRunMetric
from app.models.validation_run_scorecard import ValidationRunScorecard
from app.schemas.operations import (
    OperationalHealthIndicatorResponse,
    OperationalMonitoringResponse,
    OperationalRunStatusResponse,
    OperationalStatusResponse,
)
from app.schemas.validation_runs import ValidationRunCreateRequest
from app.services.validation_runs import service


@dataclass
class _ScalarRows:
    rows: list[object]

    def all(self) -> list[object]:
        return list(self.rows)


@dataclass
class _ExecuteRows:
    rows: list[object]

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self.rows)


class _FakeSession:
    def __init__(self) -> None:
        self.runs: list[ValidationRun] = []
        self.events: list[ValidationRunEvent] = []
        self.metrics: list[ValidationRunMetric] = []
        self.scorecards: list[ValidationRunScorecard] = []

    def add(self, obj: object) -> None:
        if isinstance(obj, ValidationRun):
            if getattr(obj, "validation_run_id", None) is None:
                obj.validation_run_id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            if getattr(obj, "updated_at", None) is None:
                obj.updated_at = datetime.now(timezone.utc)
            self.runs.append(obj)
            return

        if isinstance(obj, ValidationRunEvent):
            if getattr(obj, "id", None) is None:
                obj.id = len(self.events) + 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            self.events.append(obj)
            return

        if isinstance(obj, ValidationRunMetric):
            if getattr(obj, "id", None) is None:
                obj.id = len(self.metrics) + 1
            if getattr(obj, "captured_at", None) is None:
                obj.captured_at = datetime.now(timezone.utc)
            self.metrics.append(obj)
            return

        if isinstance(obj, ValidationRunScorecard):
            if getattr(obj, "id", None) is None:
                obj.id = len(self.scorecards) + 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            if getattr(obj, "updated_at", None) is None:
                obj.updated_at = datetime.now(timezone.utc)
            self.scorecards.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params

        if "FROM validation_runs" in sql:
            run_id = params.get("validation_run_id_1")
            if run_id is not None:
                for item in self.runs:
                    if item.validation_run_id == run_id:
                        return item
                return None
            return self.runs[0] if self.runs else None

        if "FROM validation_run_metrics" in sql and "snapshot_type" in sql:
            run_id = params.get("validation_run_id_1")
            snapshot_type = params.get("snapshot_type_1")
            filtered = [
                item
                for item in self.metrics
                if item.validation_run_id == run_id and item.snapshot_type == snapshot_type
            ]
            filtered.sort(key=lambda item: (item.captured_at, item.id))
            return filtered[0] if filtered else None

        return None

    async def execute(self, statement):
        sql = str(statement)
        params = statement.compile().params if hasattr(statement, "compile") else {}

        if "FROM validation_runs" in sql:
            rows = list(self.runs)
            rows.sort(key=lambda item: (item.created_at, item.validation_run_id), reverse=True)
            return _ExecuteRows(rows)

        if "FROM validation_run_events" in sql:
            run_id = params.get("validation_run_id_1")
            rows = [item for item in self.events if item.validation_run_id == run_id]
            descending = " DESC" in sql
            rows.sort(key=lambda item: (item.created_at, item.id), reverse=descending)
            return _ExecuteRows(rows)

        if "FROM validation_run_scorecards" in sql:
            run_id = params.get("validation_run_id_1")
            rows = [item for item in self.scorecards if item.validation_run_id == run_id]
            rows.sort(key=lambda item: item.category)
            return _ExecuteRows(rows)

        return _ExecuteRows([])


@pytest.fixture()
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def patch_runtime_dependencies(monkeypatch):
    async def fake_capture_snapshot(*, db):
        return service._MetricSnapshot(
            candles=200,
            signals=40,
            trades=5,
            decision_records=40,
            paper_equity=Decimal("100250.00"),
            campaign_count=2,
            research_candidates=20,
            candidates_evaluated=18,
            evolution_count=8,
            research_memory_growth=55,
            alerts_count=1,
            current_champion="MA Crossover",
        )

    async def fake_operations_status(*, db):
        return OperationalStatusResponse(
            overall_health="yellow",
            run_status=OperationalRunStatusResponse(
                run_id="run",
                started_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
                expected_end=datetime(2026, 7, 12, tzinfo=timezone.utc),
                uptime="01:00:00",
                current_phase="researching",
                health_status="yellow",
            ),
            system_health={
                "api": OperationalHealthIndicatorResponse(state="green", detail="ok"),
                "orchestrator": OperationalHealthIndicatorResponse(state="yellow", detail="lag"),
                "database": OperationalHealthIndicatorResponse(state="green", detail="ok"),
                "research_agent": OperationalHealthIndicatorResponse(state="green", detail="ok"),
            },
            research_status={
                "current_campaign": "Campaign 1",
                "current_champion": "MA Crossover",
                "campaign_status": "RUNNING",
            },
            monitoring=OperationalMonitoringResponse(
                candles_processed=200,
                signals_generated=40,
                paper_trades_executed=5,
                decision_records_created=40,
                replay_count=10,
                candidate_count=20,
                campaign_count=2,
                laboratory_runs=3,
                evolution_count=8,
                current_champion="MA Crossover",
                paper_equity="100250.00",
                signals_today=10,
                trades_today=2,
                research_memory_growth=55,
            ),
            alerts=[],
        )

    monkeypatch.setattr(service, "_capture_snapshot", fake_capture_snapshot)
    monkeypatch.setattr(service, "build_operations_status", fake_operations_status)


@pytest.mark.asyncio
async def test_create_start_cancel_list_and_history_survive_restart(fake_session: _FakeSession) -> None:
    created = await service.create_validation_run(
        db=fake_session,
        request=ValidationRunCreateRequest(
            name="Validation 72h",
            objective="Run proving cycle",
            duration_hours=72,
            paper_capital=Decimal("100000"),
            enabled_strategies=["MA Crossover", "RSI"],
            enabled_research_agents=["Baseline", "OpenAI Sandbox"],
            enabled_research_features=["Laboratory", "Evolution", "Tournament", "Capital Allocation"],
        ),
    )
    assert created.status == "DRAFT"

    started, initial_metrics = await service.start_validation_run(
        db=fake_session,
        validation_run_id=created.validation_run_id,
    )
    assert started.status == "RUNNING"
    assert started.started_at is not None
    assert started.expected_end_at is not None
    assert initial_metrics.current_champion == "MA Crossover"

    listed = await service.list_validation_runs(db=fake_session)
    assert len(listed) == 1
    assert listed[0].validation_run_id == created.validation_run_id

    metrics = await service.get_validation_run_metrics(db=fake_session, validation_run_id=created.validation_run_id)
    assert metrics.candles_processed_during_run >= 0
    assert metrics.signals_generated_during_run >= 0

    detail = await service.get_validation_run(db=fake_session, validation_run_id=created.validation_run_id)
    assert detail.overall_score >= 0
    assert len(detail.scorecards) == 10

    cancelled = await service.cancel_validation_run(db=fake_session, validation_run_id=created.validation_run_id)
    assert cancelled.status == "CANCELLED"

    events_response = await service.list_validation_run_events(db=fake_session, validation_run_id=created.validation_run_id)
    event_types = {item.event_type for item in events_response.items}
    assert "VALIDATION_RUN_STARTED" in event_types
    assert "VALIDATION_RUN_CANCELLED" in event_types

    # Simulate restart by calling list again from the same durable session-backed state.
    listed_after_restart = await service.list_validation_runs(db=fake_session)
    assert listed_after_restart[0].status == "CANCELLED"


@pytest.mark.asyncio
async def test_scorecard_and_metrics_calculation(fake_session: _FakeSession) -> None:
    created = await service.create_validation_run(
        db=fake_session,
        request=ValidationRunCreateRequest(
            name="Validation 24h",
            objective="Scorecard check",
            duration_hours=24,
            paper_capital=Decimal("50000"),
            enabled_strategies=["MA Crossover"],
            enabled_research_agents=["Baseline"],
            enabled_research_features=["Laboratory", "Evolution"],
        ),
    )
    _, _ = await service.start_validation_run(db=fake_session, validation_run_id=created.validation_run_id)

    detail = await service.get_validation_run(db=fake_session, validation_run_id=created.validation_run_id)
    assert detail.overall_score >= 0
    assert all(item.score >= 0 for item in detail.scorecards)

    metrics = await service.get_validation_run_metrics(db=fake_session, validation_run_id=created.validation_run_id)
    assert metrics.elapsed_percentage >= 0
    assert metrics.current_equity == "100250.00"


@pytest.mark.asyncio
async def test_event_ordering_filtering_and_pagination(fake_session: _FakeSession) -> None:
    created = await service.create_validation_run(
        db=fake_session,
        request=ValidationRunCreateRequest(
            name="Timeline Filtering",
            objective="Timeline endpoint behavior",
            duration_hours=24,
            paper_capital=Decimal("10000"),
            enabled_strategies=["MA Crossover"],
            enabled_research_agents=["Baseline"],
            enabled_research_features=["Laboratory"],
        ),
    )
    await service.start_validation_run(db=fake_session, validation_run_id=created.validation_run_id)

    now = datetime.now(timezone.utc)
    fake_session.add(
        ValidationRunEvent(
            validation_run_id=created.validation_run_id,
            event_type="PAPER_TRADE_EXECUTED",
            message="Paper trade executed",
            payload={"title": "Paper Trade Executed", "description": "BUY simulated order", "severity": "blue", "metadata": {"trade_id": "t-1"}},
            created_at=now,
        )
    )
    fake_session.add(
        ValidationRunEvent(
            validation_run_id=created.validation_run_id,
            event_type="WARNING",
            message="Latency warning",
            payload={"title": "Warning", "description": "Data ingest lagging", "severity": "yellow", "metadata": {}},
            created_at=now - timedelta(minutes=50),
        )
    )
    fake_session.add(
        ValidationRunEvent(
            validation_run_id=created.validation_run_id,
            event_type="FAILURE",
            message="Worker restart required",
            payload={"title": "Failure", "description": "Worker stalled", "severity": "red", "metadata": {}},
            created_at=now - timedelta(hours=3),
        )
    )

    newest_page_1 = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        order="newest",
        page=1,
        page_size=2,
    )
    newest_page_2 = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        order="newest",
        page=2,
        page_size=2,
    )
    assert len(newest_page_1.items) == 2
    assert newest_page_1.has_more is True
    assert newest_page_2.page == 2
    assert newest_page_2.items[0].id != newest_page_1.items[0].id

    oldest = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        order="oldest",
        page=1,
        page_size=50,
    )
    assert oldest.items[0].timestamp <= oldest.items[-1].timestamp

    warning_only = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        category="warnings",
        page=1,
        page_size=50,
    )
    assert all(item.event_type in {"WARNING", "RISK_EVENT"} for item in warning_only.items)

    failures_last_hour = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        category="failures",
        window="last_hour",
        page=1,
        page_size=50,
    )
    assert all(item.severity == "red" for item in failures_last_hour.items)

    yellow_only = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        severity="yellow",
        page=1,
        page_size=50,
    )
    assert all(item.severity == "yellow" for item in yellow_only.items)

    search_trade = await service.list_validation_run_events(
        db=fake_session,
        validation_run_id=created.validation_run_id,
        search="trade",
        page=1,
        page_size=50,
    )
    assert any("trade" in item.description.lower() for item in search_trade.items)


@pytest.mark.asyncio
async def test_event_streaming_creates_heartbeat_event(fake_session: _FakeSession) -> None:
    created = await service.create_validation_run(
        db=fake_session,
        request=ValidationRunCreateRequest(
            name="Streamed Validation",
            objective="Heartbeat stream",
            duration_hours=24,
            paper_capital=Decimal("25"),
            enabled_strategies=["MA Crossover"],
            enabled_research_agents=["Baseline"],
            enabled_research_features=["Laboratory"],
        ),
    )
    await service.start_validation_run(db=fake_session, validation_run_id=created.validation_run_id)

    await service.get_validation_run_metrics(db=fake_session, validation_run_id=created.validation_run_id)
    events = await service.list_validation_run_events(db=fake_session, validation_run_id=created.validation_run_id)
    event_types = {item.event_type for item in events.items}
    assert "VALIDATION_HEARTBEAT" in event_types
