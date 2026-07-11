from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.schemas.operations import (
    OperationalAlertResponse,
    OperationalHealthIndicatorResponse,
    OperationalMonitoringResponse,
    OperationalRunStatusResponse,
    OperationalStatusResponse,
)
from app.schemas.validation_runs import (
    ValidationRunEventListResponse,
    ValidationRunEventResponse,
    ValidationRunResponse,
)
from app.services import mission_control_intelligence as service


class _DummySession:
    pass


class _QueryableDummySession:
    async def execute(self, *_args, **_kwargs):
        return None


def _operations_status(*, alert_count: int = 0, orchestrator_state: str = "green") -> OperationalStatusResponse:
    alerts = [OperationalAlertResponse(code=f"alert-{index}", severity="yellow", message="Worker restart") for index in range(alert_count)]
    return OperationalStatusResponse(
        overall_health="green" if alert_count == 0 else "yellow",
        run_status=OperationalRunStatusResponse(
            run_id="run-1",
            started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
            expected_end=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
            uptime="24:00:00",
            current_phase="researching",
            health_status="green" if alert_count == 0 else "yellow",
        ),
        system_health={
            "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
            "orchestrator": OperationalHealthIndicatorResponse(state=orchestrator_state, detail="Heartbeat active"),
            "database": OperationalHealthIndicatorResponse(state="green", detail="Database connected"),
            "research_agent": OperationalHealthIndicatorResponse(state="green", detail="OpenAI research adapter available"),
        },
        research_status={
            "current_campaign": "Campaign Alpha",
            "current_champion": "RSI Mean Reversion",
            "campaign_status": "RUNNING",
        },
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
        alerts=alerts,
    )


def _validation_run() -> ValidationRunResponse:
    return ValidationRunResponse(
        validation_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="72h Proving",
        objective="Validate stability",
        duration_hours=72,
        status="RUNNING",
        started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
        expected_end_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=None,
        paper_capital=Decimal("100000"),
        enabled_strategies=["MA Crossover", "RSI"],
        enabled_research_agents=["Baseline", "OpenAI Sandbox"],
        enabled_research_features=["Laboratory", "Evolution"],
        health_score=88,
        result_status="INCOMPLETE",
    )


def _validation_run_with_id(run_id: str) -> ValidationRunResponse:
    run = _validation_run()
    payload = run.model_dump()
    payload["validation_run_id"] = uuid.UUID(run_id)
    return ValidationRunResponse(**payload)


def _events() -> ValidationRunEventListResponse:
    return ValidationRunEventListResponse(
        items=[
            ValidationRunEventResponse(
                id=1,
                validation_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                timestamp=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
                event_type="VALIDATION_RUN_STARTED",
                category="system",
                severity="green",
                title="Validation Run Started",
                description="Validation run is now active.",
                metadata={"status": "RUNNING"},
            ),
            ValidationRunEventResponse(
                id=2,
                validation_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                timestamp=datetime(2026, 7, 9, 4, 0, tzinfo=timezone.utc),
                event_type="CHAMPION_STRATEGY_CHANGED",
                category="research",
                severity="purple",
                title="Champion Changed",
                description="The lead strategy changed.",
                metadata={"current_champion": "RSI Mean Reversion"},
            ),
        ],
        page=1,
        page_size=50,
        total=2,
        has_more=False,
        order="oldest",
        window="entire_run",
        category="all",
        severity="all",
        search=None,
    )


def _campaign(*, campaign_uuid: str, status: str, starting_capital: str, paper_account_id: str | None = None):
    return SimpleNamespace(
        uuid=uuid.UUID(campaign_uuid),
        status=status,
        starting_capital=Decimal(starting_capital),
        paper_account_id=None if paper_account_id is None else uuid.UUID(paper_account_id),
    )


@pytest.mark.asyncio
async def test_build_mission_control_intelligence_calculates_score_and_orders_history(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _operations_stub(*_args, **_kwargs):
        return _operations_status()

    async def _runs_stub(*_args, **_kwargs):
        return [_validation_run()]

    monkeypatch.setattr(service, "build_operations_status", _operations_stub)
    monkeypatch.setattr(service, "list_validation_runs", _runs_stub)

    async def _events_stub(*_args, **_kwargs):
        return _events()

    monkeypatch.setattr(service, "list_validation_run_events", _events_stub)

    result = await service.build_mission_control_intelligence(db=_DummySession(), range_value="7d")

    assert result.version == "v1"
    assert result.range == "7d"
    assert result.current_score > 0
    assert result.confidence == "High"
    assert result.trend.direction == "up"
    assert result.history == sorted(result.history, key=lambda item: item.timestamp)
    assert result.timeline_events == sorted(result.timeline_events, key=lambda item: item.timestamp)
    assert result.metric_breakdown
    assert {item.name for item in result.metric_breakdown} == {
        "Prediction Quality",
        "Risk Discipline",
        "Research Activity",
        "Execution Health",
        "Infrastructure Health",
        "Paper Trading Health",
    }


@pytest.mark.asyncio
async def test_build_mission_control_intelligence_handles_empty_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _operations_stub(*_args, **_kwargs):
        return _operations_status(alert_count=1, orchestrator_state="yellow")

    async def _runs_stub(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service, "build_operations_status", _operations_stub)
    monkeypatch.setattr(service, "list_validation_runs", _runs_stub)

    async def _events_stub(*_args, **_kwargs):
        return ValidationRunEventListResponse(
            items=[],
            page=1,
            page_size=50,
            total=0,
            has_more=False,
            order="oldest",
            window="entire_run",
            category="all",
            severity="all",
            search=None,
        )

    monkeypatch.setattr(service, "list_validation_run_events", _events_stub)

    result = await service.build_mission_control_intelligence(db=_DummySession(), range_value="all")

    assert result.range == "all"
    assert result.validation_runs == []
    assert result.selected_validation_run_id is None
    assert result.timeline_events
    assert result.history == sorted(result.history, key=lambda item: item.timestamp)
    assert result.notes.startswith("Mission Control Intelligence Center V1")


@pytest.mark.asyncio
async def test_timeline_pnl_uses_bound_paper_account_baseline_for_25_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return Decimal("25"), Decimal("25"), 1

    async def _campaign_stub(**_kwargs):
        return None

    async def _active_stub(**_kwargs):
        return None

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        fallback_equity="25.00",
    )

    assert equity == "25.00"
    assert pnl == "0.00"
    assert metadata["paper_pnl_source"] == "bound_paper_account"


@pytest.mark.asyncio
async def test_timeline_pnl_supports_legacy_100k_run_when_bound_evidence_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return Decimal("100500"), Decimal("100000"), 1

    async def _campaign_stub(**_kwargs):
        return None

    async def _active_stub(**_kwargs):
        return None

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        fallback_equity="25.00",
    )

    assert equity == "100500.00"
    assert pnl == "500.00"
    assert metadata["paper_pnl_source"] == "bound_paper_account"


@pytest.mark.asyncio
async def test_timeline_pnl_supports_two_simultaneous_25_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return Decimal("50"), Decimal("50"), 2

    async def _campaign_stub(**_kwargs):
        return None

    async def _active_stub(**_kwargs):
        return None

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        fallback_equity="25.00",
    )

    assert equity == "50.00"
    assert pnl == "0.00"
    assert metadata["paper_pnl_bound_account_count"] == 2


@pytest.mark.asyncio
async def test_timeline_pnl_uses_campaign_linked_capital_when_account_binding_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return None

    async def _campaign_stub(**_kwargs):
        return Decimal("30"), Decimal("25"), 1

    async def _active_stub(**_kwargs):
        return None

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        fallback_equity="25.00",
    )

    assert equity == "30.00"
    assert pnl == "5.00"
    assert metadata["paper_pnl_source"] == "campaign_opening_capital"


@pytest.mark.asyncio
async def test_timeline_pnl_falls_back_to_active_account_when_binding_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return None

    async def _campaign_stub(**_kwargs):
        return None

    async def _active_stub(**_kwargs):
        return Decimal("25"), Decimal("25"), uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        fallback_equity="25.00",
    )

    assert equity == "25.00"
    assert pnl == "0.00"
    assert metadata["paper_pnl_status"] == "fallback_unbound"


@pytest.mark.asyncio
async def test_timeline_pnl_marks_unresolved_when_no_evidence_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bound_stub(**_kwargs):
        return None

    async def _campaign_stub(**_kwargs):
        return None

    async def _active_stub(**_kwargs):
        return None

    monkeypatch.setattr(service, "_bound_accounts_equity_and_baseline", _bound_stub)
    monkeypatch.setattr(service, "_campaign_equity_and_baseline", _campaign_stub)
    monkeypatch.setattr(service, "_latest_active_account_equity_and_baseline", _active_stub)

    equity, pnl, metadata = await service._resolve_timeline_equity_and_pnl(
        db=_QueryableDummySession(),
        selected_validation_run=_validation_run_with_id("11111111-2222-3333-4444-555555555555"),
        fallback_equity="25.00",
    )

    assert equity == "25.00"
    assert pnl is None
    assert metadata["paper_pnl_status"] == "baseline_unresolved"


def test_managed_capital_calculation_no_campaigns() -> None:
    assert service._calculate_total_managed_capital([]) == Decimal("0")


def test_managed_capital_calculation_single_active_campaign() -> None:
    campaigns = [
        _campaign(
            campaign_uuid="11111111-1111-1111-1111-111111111111",
            status="RUNNING",
            starting_capital="25",
            paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
    ]
    assert service._calculate_total_managed_capital(campaigns) == Decimal("25")


def test_managed_capital_calculation_two_active_campaigns() -> None:
    campaigns = [
        _campaign(
            campaign_uuid="11111111-1111-1111-1111-111111111111",
            status="READY",
            starting_capital="25",
            paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        ),
        _campaign(
            campaign_uuid="22222222-2222-2222-2222-222222222222",
            status="RUNNING",
            starting_capital="40",
            paper_account_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ),
    ]
    assert service._calculate_total_managed_capital(campaigns) == Decimal("65")


def test_managed_capital_calculation_excludes_archived_and_completed() -> None:
    campaigns = [
        _campaign(
            campaign_uuid="11111111-1111-1111-1111-111111111111",
            status="RUNNING",
            starting_capital="25",
            paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        ),
        _campaign(
            campaign_uuid="22222222-2222-2222-2222-222222222222",
            status="ARCHIVED",
            starting_capital="50",
            paper_account_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ),
        _campaign(
            campaign_uuid="33333333-3333-3333-3333-333333333333",
            status="COMPLETED",
            starting_capital="75",
            paper_account_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        ),
        _campaign(
            campaign_uuid="44444444-4444-4444-4444-444444444444",
            status="PAUSED",
            starting_capital="15",
            paper_account_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        ),
    ]
    assert service._calculate_total_managed_capital(campaigns) == Decimal("40")


def test_managed_capital_calculation_avoids_duplicate_paper_account_reference() -> None:
    campaigns = [
        _campaign(
            campaign_uuid="11111111-1111-1111-1111-111111111111",
            status="RUNNING",
            starting_capital="25",
            paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        ),
        _campaign(
            campaign_uuid="22222222-2222-2222-2222-222222222222",
            status="TARGET_REACHED",
            starting_capital="25",
            paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        ),
    ]
    assert service._calculate_total_managed_capital(campaigns) == Decimal("25")