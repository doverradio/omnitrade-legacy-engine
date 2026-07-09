from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.paper_account import PaperAccount
from app.models.validation_run import ValidationRun
from app.models.validation_run_event import ValidationRunEvent
from app.models.validation_run_metric import ValidationRunMetric
from app.models.validation_run_scorecard import ValidationRunScorecard
from app.schemas.operations import OperationalAlertResponse, OperationalStatusResponse
from app.schemas.validation_runs import (
    ValidationRunCreateRequest,
    ValidationRunDetailResponse,
    ValidationRunEventResponse,
    ValidationRunEventListResponse,
    ValidationRunMetricsResponse,
    ValidationRunResponse,
    ValidationRunScorecardResponse,
)
from app.services.operations_status import build_operations_status
from app.services.paper.accounting import build_account_snapshot

_ALLOWED_STATUSES = {"DRAFT", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
_ALLOWED_RESULT_STATUSES = {"PASS", "CONDITIONAL_PASS", "FAIL", "INCOMPLETE"}
_EVENT_ORDER = {"newest", "oldest"}
_EVENT_WINDOWS = {"last_hour", "last_24_hours", "entire_run"}
_EVENT_CATEGORIES = {"all", "system", "market", "strategy", "risk", "execution", "research", "database", "warnings", "failures", "manual_notes"}
_EVENT_SEVERITIES = {"all", "green", "blue", "purple", "yellow", "red", "gray"}
_EVENT_TYPE_CATEGORY = {
    "VALIDATION_RUN_STARTED": "system",
    "VALIDATION_RUN_COMPLETED": "system",
    "VALIDATION_RUN_CANCELLED": "system",
    "VALIDATION_HEARTBEAT": "system",
    "CANDLE_BATCH_INGESTED": "market",
    "ASSET_SYNCHRONIZATION_COMPLETED": "market",
    "SIGNAL_GENERATED": "strategy",
    "BUY_CANDIDATE_CREATED": "strategy",
    "SELL_CANDIDATE_CREATED": "strategy",
    "HOLD_DECISION_RECORDED": "strategy",
    "RISK_EVALUATION_STARTED": "risk",
    "RISK_APPROVED": "risk",
    "RISK_REJECTED": "risk",
    "PAPER_TRADE_SUBMITTED": "execution",
    "PAPER_TRADE_FILLED": "execution",
    "PAPER_TRADE_REJECTED": "execution",
    "POSITION_OPENED": "execution",
    "POSITION_CLOSED": "execution",
    "RESEARCH_CAMPAIGN_STARTED": "research",
    "RESEARCH_CAMPAIGN_COMPLETED": "research",
    "EVOLUTION_CYCLE_STARTED": "research",
    "EVOLUTION_CYCLE_COMPLETED": "research",
    "CHAMPION_STRATEGY_CHANGED": "research",
    "MIGRATION_DETECTED": "database",
    "DATABASE_HEALTH_WARNING": "database",
    "DATABASE_RECOVERED": "database",
    "WARNING": "warnings",
    "FAILURE": "failures",
    "MANUAL_NOTE": "manual_notes",
}
_CATEGORY_EVENT_TYPES = {
    "market": {
        "CANDLE_BATCH_INGESTED",
        "ASSET_SYNCHRONIZATION_COMPLETED",
    },
    "strategy": {
        "SIGNAL_GENERATED",
        "BUY_CANDIDATE_CREATED",
        "SELL_CANDIDATE_CREATED",
        "HOLD_DECISION_RECORDED",
    },
    "risk": {
        "RISK_EVALUATION_STARTED",
        "RISK_APPROVED",
        "RISK_REJECTED",
    },
    "execution": {
        "PAPER_TRADE_SUBMITTED",
        "PAPER_TRADE_FILLED",
        "PAPER_TRADE_REJECTED",
        "POSITION_OPENED",
        "POSITION_CLOSED",
    },
    "research": {
        "RESEARCH_CAMPAIGN_STARTED",
        "RESEARCH_CAMPAIGN_COMPLETED",
        "EVOLUTION_CYCLE_STARTED",
        "EVOLUTION_CYCLE_COMPLETED",
        "CHAMPION_STRATEGY_CHANGED",
    },
    "database": {
        "MIGRATION_DETECTED",
        "DATABASE_HEALTH_WARNING",
        "DATABASE_RECOVERED",
    },
    "system": {
        "VALIDATION_RUN_STARTED",
        "VALIDATION_RUN_COMPLETED",
        "VALIDATION_RUN_CANCELLED",
        "VALIDATION_HEARTBEAT",
    },
    "warnings": {"WARNING", "RISK_EVENT"},
    "failures": {"FAILURE", "ALERT"},
    "manual_notes": {"MANUAL_NOTE"},
}


@dataclass(frozen=True, slots=True)
class _MetricSnapshot:
    candles: int
    signals: int
    trades: int
    decision_records: int
    paper_equity: Decimal
    campaign_count: int
    research_candidates: int
    candidates_evaluated: int
    evolution_count: int
    research_memory_growth: int
    alerts_count: int
    current_champion: str | None


@dataclass(frozen=True, slots=True)
class _EventStreamSnapshot:
    candles: int
    assets: int
    signals_total: int
    signals_buy: int
    signals_sell: int
    signals_hold: int
    risk_total: int
    risk_approved: int
    risk_rejected: int
    trades: int
    trade_buy: int
    trade_sell: int
    campaign_started: int
    campaign_completed: int
    evolution_cycles: int
    champion: str | None
    db_state: str
    db_version: str | None


async def list_validation_runs(*, db: AsyncSession) -> list[ValidationRunResponse]:
    rows = (
        await db.execute(
            select(ValidationRun)
            .order_by(ValidationRun.created_at.desc(), ValidationRun.validation_run_id.desc())
        )
    ).scalars().all()
    return [_to_run_response(item) for item in rows]


async def get_validation_run(*, db: AsyncSession, validation_run_id: uuid.UUID) -> ValidationRunDetailResponse:
    run = await _load_run(db=db, validation_run_id=validation_run_id)
    scorecards, overall_score = await _load_scorecards(db=db, validation_run_id=validation_run_id)
    return ValidationRunDetailResponse(
        **_to_run_response(run).model_dump(),
        overall_score=overall_score,
        scorecards=scorecards,
    )


async def create_validation_run(*, db: AsyncSession, request: ValidationRunCreateRequest) -> ValidationRunResponse:
    if request.duration_hours <= 0:
        raise InvalidRequestError(
            message="duration_hours must be > 0",
            details={"duration_hours": request.duration_hours},
        )

    run = ValidationRun(
        name=request.name.strip(),
        objective=request.objective.strip(),
        duration_hours=request.duration_hours,
        status="DRAFT",
        started_at=None,
        expected_end_at=None,
        completed_at=None,
        paper_capital=request.paper_capital,
        enabled_strategies=list(request.enabled_strategies),
        enabled_research_agents=list(request.enabled_research_agents),
        enabled_research_features=list(request.enabled_research_features),
        health_score=None,
        result_status="INCOMPLETE",
        updated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()

    db.add(
        ValidationRunEvent(
            validation_run_id=run.validation_run_id,
            event_type="MANUAL_NOTE",
            message="Validation run created",
            payload={
                "severity": "gray",
                "title": "Manual Note",
                "description": "Validation run created in draft mode.",
                "metadata": {
                    "status": "DRAFT",
                    "duration_hours": run.duration_hours,
                },
            },
        )
    )
    await db.commit()

    return _to_run_response(run)


async def start_validation_run(*, db: AsyncSession, validation_run_id: uuid.UUID) -> tuple[ValidationRunResponse, ValidationRunMetricsResponse]:
    run = await _load_run(db=db, validation_run_id=validation_run_id)

    if run.status != "DRAFT":
        raise InvalidRequestError(
            message="Validation run can only be started from DRAFT",
            details={"validation_run_id": str(validation_run_id), "status": run.status},
        )

    now = datetime.now(timezone.utc)
    run.status = "RUNNING"
    run.started_at = now
    run.expected_end_at = now + timedelta(hours=run.duration_hours)
    run.completed_at = None
    run.result_status = "INCOMPLETE"
    run.updated_at = now

    baseline = await _capture_snapshot(db=db)
    db.add(
        ValidationRunMetric(
            validation_run_id=run.validation_run_id,
            snapshot_type="BASELINE",
            candles=baseline.candles,
            signals=baseline.signals,
            trades=baseline.trades,
            decision_records=baseline.decision_records,
            paper_equity=baseline.paper_equity,
            campaign_count=baseline.campaign_count,
            research_candidates=baseline.research_candidates,
            candidates_evaluated=baseline.candidates_evaluated,
            evolution_count=baseline.evolution_count,
            research_memory_growth=baseline.research_memory_growth,
            alerts_count=baseline.alerts_count,
        )
    )

    db.add(
        ValidationRunEvent(
            validation_run_id=run.validation_run_id,
            event_type="VALIDATION_RUN_STARTED",
            message="Validation run started",
            payload={
                "severity": "green",
                "title": "Validation Run Started",
                "description": "Validation run is now active and collecting baseline metrics.",
                "metadata": {
                    "started_at": now.isoformat(),
                    "expected_end_at": run.expected_end_at.isoformat() if run.expected_end_at else None,
                    "baseline": {
                        "candles": baseline.candles,
                        "signals": baseline.signals,
                        "trades": baseline.trades,
                        "decision_records": baseline.decision_records,
                        "paper_equity": format(baseline.paper_equity, "f"),
                        "campaign_count": baseline.campaign_count,
                        "research_candidates": baseline.research_candidates,
                        "evolution_count": baseline.evolution_count,
                    },
                },
            },
        )
    )

    metrics = await get_validation_run_metrics(db=db, validation_run_id=run.validation_run_id)
    scorecards, overall_score = _build_scorecards(
        operations=await build_operations_status(db=db),
        metrics=metrics,
        run=run,
    )
    await _upsert_scorecards(db=db, validation_run_id=run.validation_run_id, scorecards=scorecards)
    run.health_score = overall_score

    await db.commit()
    return _to_run_response(run), metrics


async def cancel_validation_run(*, db: AsyncSession, validation_run_id: uuid.UUID) -> ValidationRunResponse:
    run = await _load_run(db=db, validation_run_id=validation_run_id)
    if run.status not in {"DRAFT", "RUNNING"}:
        raise InvalidRequestError(
            message="Only DRAFT or RUNNING validation runs can be cancelled",
            details={"validation_run_id": str(validation_run_id), "status": run.status},
        )

    now = datetime.now(timezone.utc)
    run.status = "CANCELLED"
    run.completed_at = now
    run.result_status = "INCOMPLETE"
    run.updated_at = now

    db.add(
        ValidationRunEvent(
            validation_run_id=run.validation_run_id,
            event_type="VALIDATION_RUN_CANCELLED",
            message="Validation run cancelled",
            payload={
                "severity": "yellow",
                "title": "Validation Run Cancelled",
                "description": "Validation run cancelled before completion.",
                "metadata": {"completed_at": now.isoformat()},
            },
        )
    )

    metrics = await get_validation_run_metrics(db=db, validation_run_id=run.validation_run_id)
    scorecards, overall_score = _build_scorecards(
        operations=await build_operations_status(db=db),
        metrics=metrics,
        run=run,
    )
    await _upsert_scorecards(db=db, validation_run_id=run.validation_run_id, scorecards=scorecards)
    run.health_score = overall_score

    await db.commit()
    return _to_run_response(run)


async def list_validation_run_events(
    *,
    db: AsyncSession,
    validation_run_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
    order: str = "newest",
    window: str = "entire_run",
    category: str = "all",
    severity: str = "all",
    search: str | None = None,
) -> ValidationRunEventListResponse:
    run = await _load_run(db=db, validation_run_id=validation_run_id)
    normalized_order = order.strip().lower()
    normalized_window = window.strip().lower()
    normalized_category = category.strip().lower()
    normalized_severity = severity.strip().lower()
    normalized_search = (search or "").strip().lower() or None

    if normalized_order not in _EVENT_ORDER:
        raise InvalidRequestError(message="Invalid event order", details={"order": order})
    if normalized_window not in _EVENT_WINDOWS:
        raise InvalidRequestError(message="Invalid event window", details={"window": window})
    if normalized_category not in _EVENT_CATEGORIES:
        raise InvalidRequestError(message="Invalid event category", details={"category": category})
    if normalized_severity not in _EVENT_SEVERITIES:
        raise InvalidRequestError(message="Invalid event severity", details={"severity": severity})
    if page <= 0 or page_size <= 0:
        raise InvalidRequestError(message="page and page_size must be > 0", details={"page": page, "page_size": page_size})

    await _stream_validation_events(db=db, run=run)

    statement = select(ValidationRunEvent).where(ValidationRunEvent.validation_run_id == validation_run_id)
    if normalized_order == "oldest":
        statement = statement.order_by(ValidationRunEvent.created_at.asc(), ValidationRunEvent.id.asc())
    else:
        statement = statement.order_by(ValidationRunEvent.created_at.desc(), ValidationRunEvent.id.desc())

    rows = (await db.execute(statement)).scalars().all()
    since = _window_start(normalized_window)
    filtered_rows = list(rows)
    if since is not None:
        filtered_rows = [item for item in filtered_rows if item.created_at >= since]

    category_event_types = _CATEGORY_EVENT_TYPES.get(normalized_category)
    if category_event_types:
        filtered_rows = [item for item in filtered_rows if item.event_type in category_event_types]

    if normalized_severity != "all":
        filtered_rows = [
            item
            for item in filtered_rows
            if _safe_text(dict(item.payload or {}).get("severity")) == normalized_severity
        ]

    if normalized_search is not None:
        filtered_rows = [
            item
            for item in filtered_rows
            if normalized_search in item.message.lower()
            or normalized_search in item.event_type.lower()
            or normalized_search in str(item.payload).lower()
        ]

    total = len(filtered_rows)
    offset = (page - 1) * page_size
    page_rows = filtered_rows[offset : offset + page_size]
    items = [_to_event_response(item) for item in page_rows]

    return ValidationRunEventListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=(offset + len(items)) < total,
        order=normalized_order,
        window=normalized_window,
        category=normalized_category,
        severity=normalized_severity,
        search=normalized_search,
    )


async def get_validation_run_metrics(*, db: AsyncSession, validation_run_id: uuid.UUID) -> ValidationRunMetricsResponse:
    run = await _load_run(db=db, validation_run_id=validation_run_id)

    baseline = await db.scalar(
        select(ValidationRunMetric)
        .where(ValidationRunMetric.validation_run_id == validation_run_id)
        .where(ValidationRunMetric.snapshot_type == "BASELINE")
        .order_by(ValidationRunMetric.captured_at.asc(), ValidationRunMetric.id.asc())
        .limit(1)
    )

    current = await _capture_snapshot(db=db)
    await _stream_validation_events(db=db, run=run)

    base_candles = 0 if baseline is None else baseline.candles
    base_signals = 0 if baseline is None else baseline.signals
    base_trades = 0 if baseline is None else baseline.trades
    base_decision_records = 0 if baseline is None else baseline.decision_records
    base_equity = Decimal("0") if baseline is None else baseline.paper_equity
    base_candidates = 0 if baseline is None else baseline.research_candidates
    base_candidates_evaluated = 0 if baseline is None else baseline.candidates_evaluated
    base_evolution = 0 if baseline is None else baseline.evolution_count
    base_memory = 0 if baseline is None else baseline.research_memory_growth

    elapsed_percentage = _compute_elapsed_percentage(run=run)
    time_remaining = _compute_time_remaining(run=run)

    return ValidationRunMetricsResponse(
        elapsed_percentage=elapsed_percentage,
        time_remaining=time_remaining,
        candles_processed_during_run=max(current.candles - base_candles, 0),
        signals_generated_during_run=max(current.signals - base_signals, 0),
        trades_executed_during_run=max(current.trades - base_trades, 0),
        decision_records_created_during_run=max(current.decision_records - base_decision_records, 0),
        paper_pnl_during_run=format(current.paper_equity - base_equity, "f"),
        current_equity=format(current.paper_equity, "f"),
        current_champion=current.current_champion,
        candidates_generated=max(current.research_candidates - base_candidates, 0),
        candidates_evaluated=max(current.candidates_evaluated - base_candidates_evaluated, 0),
        evolution_descendants=max(current.evolution_count - base_evolution, 0),
        research_memory_growth=max(current.research_memory_growth - base_memory, 0),
        alerts_count=current.alerts_count,
    )


def _to_run_response(run: ValidationRun) -> ValidationRunResponse:
    status = run.status
    result_status = run.result_status
    if status not in _ALLOWED_STATUSES:
        status = "FAILED"
    if result_status not in _ALLOWED_RESULT_STATUSES:
        result_status = "INCOMPLETE"

    return ValidationRunResponse(
        validation_run_id=run.validation_run_id,
        name=run.name,
        objective=run.objective,
        duration_hours=run.duration_hours,
        status=status,
        started_at=run.started_at,
        expected_end_at=run.expected_end_at,
        completed_at=run.completed_at,
        paper_capital=run.paper_capital,
        enabled_strategies=list(run.enabled_strategies),
        enabled_research_agents=list(run.enabled_research_agents),
        enabled_research_features=list(run.enabled_research_features),
        health_score=run.health_score,
        result_status=result_status,
    )


async def _load_run(*, db: AsyncSession, validation_run_id: uuid.UUID) -> ValidationRun:
    run = await db.scalar(
        select(ValidationRun).where(ValidationRun.validation_run_id == validation_run_id)
    )
    if run is None:
        raise NotFoundError(
            message="Validation run not found",
            details={"validation_run_id": str(validation_run_id)},
        )
    return run


async def _capture_snapshot(*, db: AsyncSession) -> _MetricSnapshot:
    operations = await build_operations_status(db=db)
    paper_equity = await _read_current_equity(db=db)
    current_champion = await _read_current_champion(db=db)

    return _MetricSnapshot(
        candles=await _count(db=db, sql="SELECT COUNT(*) FROM candles"),
        signals=await _count(db=db, sql="SELECT COUNT(*) FROM signals"),
        trades=await _count(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true"),
        decision_records=await _count(db=db, sql="SELECT COUNT(*) FROM decision_records"),
        paper_equity=paper_equity,
        campaign_count=await _count(db=db, sql="SELECT COUNT(*) FROM research_campaigns"),
        research_candidates=await _count(db=db, sql="SELECT COUNT(*) FROM research_candidates"),
        candidates_evaluated=await _count(db=db, sql="SELECT COUNT(*) FROM research_candidate_evaluations"),
        evolution_count=await _count(db=db, sql="SELECT COUNT(*) FROM research_candidate_lineage"),
        research_memory_growth=await _count(db=db, sql="SELECT COUNT(*) FROM research_memory_entries"),
        alerts_count=len(operations.alerts),
        current_champion=current_champion,
    )


async def _count(*, db: AsyncSession, sql: str) -> int:
    value = (await db.execute(text(sql))).scalar_one_or_none()
    return int(value or 0)


async def _read_current_equity(*, db: AsyncSession) -> Decimal:
    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.asc())
        .limit(1)
    )
    if account is None:
        return Decimal("0")

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )
    return snapshot.equity


async def _read_current_champion(*, db: AsyncSession) -> str | None:
    row = await db.execute(
        text(
            "SELECT current_champion "
            "FROM research_campaign_statistics "
            "WHERE current_champion IS NOT NULL "
            "ORDER BY updated_at DESC NULLS LAST LIMIT 1"
        )
    )
    item = row.mappings().first()
    return None if item is None else item.get("current_champion")


def _compute_elapsed_percentage(*, run: ValidationRun) -> float:
    if run.started_at is None or run.expected_end_at is None:
        return 0.0

    now = datetime.now(timezone.utc)
    start = run.started_at.astimezone(timezone.utc)
    end = run.expected_end_at.astimezone(timezone.utc)
    total = (end - start).total_seconds()
    if total <= 0:
        return 100.0

    elapsed = max((now - start).total_seconds(), 0.0)
    return round(min((elapsed / total) * 100.0, 100.0), 2)


def _compute_time_remaining(*, run: ValidationRun) -> str:
    if run.expected_end_at is None:
        return "Not started"

    now = datetime.now(timezone.utc)
    end = run.expected_end_at.astimezone(timezone.utc)
    remaining = end - now
    if remaining.total_seconds() <= 0:
        return "Completed"

    total_seconds = int(remaining.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{days}d {hours:02d}h {minutes:02d}m"


def _window_start(window: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if window == "last_hour":
        return now - timedelta(hours=1)
    if window == "last_24_hours":
        return now - timedelta(hours=24)
    return None


def _to_event_response(event: ValidationRunEvent) -> ValidationRunEventResponse:
    payload = dict(event.payload or {})
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    category = _event_category(event.event_type)
    title = _safe_text(payload.get("title")) or _title_from_event_type(event.event_type)
    description = _safe_text(payload.get("description")) or event.message
    severity = _safe_text(payload.get("severity")) or _severity_from_event_type(event.event_type)

    return ValidationRunEventResponse(
        id=int(event.id),
        validation_run_id=event.validation_run_id,
        timestamp=event.created_at,
        event_type=event.event_type,
        category=category,
        severity=severity,
        title=title,
        description=description,
        metadata=metadata,
    )


def _safe_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _event_category(event_type: str) -> str:
    category = _EVENT_TYPE_CATEGORY.get(event_type)
    if category:
        return category
    return "all"


def _severity_from_event_type(event_type: str) -> str:
    if event_type in {"FAILURE", "ALERT", "DATABASE_HEALTH_WARNING"}:
        return "red"
    if event_type in {"WARNING", "RISK_EVENT", "RISK_EVALUATION_STARTED", "RISK_REJECTED", "PAPER_TRADE_REJECTED", "VALIDATION_RUN_CANCELLED"}:
        return "yellow"
    if event_type in {"RESEARCH_CAMPAIGN_STARTED", "RESEARCH_CAMPAIGN_COMPLETED", "EVOLUTION_CYCLE_STARTED", "EVOLUTION_CYCLE_COMPLETED", "CHAMPION_STRATEGY_CHANGED"}:
        return "purple"
    if event_type in {"VALIDATION_RUN_STARTED", "VALIDATION_RUN_COMPLETED", "VALIDATION_HEARTBEAT", "DATABASE_RECOVERED", "RISK_APPROVED", "PAPER_TRADE_FILLED", "POSITION_OPENED"}:
        return "green"
    if event_type in {"SIGNAL_GENERATED", "BUY_CANDIDATE_CREATED", "SELL_CANDIDATE_CREATED", "HOLD_DECISION_RECORDED", "CANDLE_BATCH_INGESTED", "ASSET_SYNCHRONIZATION_COMPLETED", "PAPER_TRADE_SUBMITTED", "POSITION_CLOSED"}:
        return "blue"
    return "gray"


def _title_from_event_type(event_type: str) -> str:
    return event_type.replace("_", " ").title()


async def _stream_validation_events(*, db: AsyncSession, run: ValidationRun) -> None:
    try:
        current_events = await _events_for_run(db=db, validation_run_id=run.validation_run_id)
        latest_heartbeat = next((item for item in current_events if item.event_type == "VALIDATION_HEARTBEAT"), None)
        previous_snapshot = _snapshot_from_event(latest_heartbeat)

        operations = await build_operations_status(db=db)
        snapshot = await _capture_stream_snapshot(db=db, operations=operations)
        now = datetime.now(timezone.utc)

        if run.status == "CANCELLED" and not _has_event_type(current_events, "VALIDATION_RUN_CANCELLED"):
            _append_event(
                db=db,
                validation_run_id=run.validation_run_id,
                event_type="VALIDATION_RUN_CANCELLED",
                severity="yellow",
                title="Validation Run Cancelled",
                description="Validation run was cancelled.",
                metadata={"completed_at": run.completed_at.isoformat() if run.completed_at else None},
            )

        completed = run.status in {"COMPLETED", "FAILED"} or (
            run.status == "RUNNING" and run.expected_end_at is not None and now >= run.expected_end_at
        )
        if completed and not _has_event_type(current_events, "VALIDATION_RUN_COMPLETED"):
            _append_event(
                db=db,
                validation_run_id=run.validation_run_id,
                event_type="VALIDATION_RUN_COMPLETED",
                severity="green" if run.status != "FAILED" else "red",
                title="Validation Run Completed",
                description="Validation run reached completion window.",
                metadata={
                    "status": run.status,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else now.isoformat(),
                },
            )

        if previous_snapshot is None:
            _append_event(
                db=db,
                validation_run_id=run.validation_run_id,
                event_type="VALIDATION_HEARTBEAT",
                severity="green",
                title="Validation Heartbeat",
                description="Validation timeline heartbeat initialized.",
                metadata={"snapshot": _snapshot_to_metadata(snapshot)},
            )
            await db.flush()
            return

        _stream_delta_events(
            db=db,
            validation_run_id=run.validation_run_id,
            previous=previous_snapshot,
            current=snapshot,
        )

        heartbeat_due = latest_heartbeat is None or (now - latest_heartbeat.created_at) >= timedelta(seconds=30)
        if heartbeat_due:
            _append_event(
                db=db,
                validation_run_id=run.validation_run_id,
                event_type="VALIDATION_HEARTBEAT",
                severity="green",
                title="Validation Heartbeat",
                description="Validation heartbeat captured current paper/research state.",
                metadata={"snapshot": _snapshot_to_metadata(snapshot)},
            )
        await db.flush()
    except Exception:
        # Streaming is observational only; failures here must not impact core run behavior.
        return


def _stream_delta_events(
    *,
    db: AsyncSession,
    validation_run_id: uuid.UUID,
    previous: _EventStreamSnapshot,
    current: _EventStreamSnapshot,
) -> None:
    candle_delta = max(current.candles - previous.candles, 0)
    if candle_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="CANDLE_BATCH_INGESTED", severity="blue", title="Candle Batch Ingested", description=f"{candle_delta} new candles ingested.", metadata={"count": candle_delta})

    asset_delta = max(current.assets - previous.assets, 0)
    if asset_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="ASSET_SYNCHRONIZATION_COMPLETED", severity="blue", title="Asset Synchronization Completed", description=f"{asset_delta} new active assets synchronized.", metadata={"count": asset_delta})

    signal_delta = max(current.signals_total - previous.signals_total, 0)
    if signal_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="SIGNAL_GENERATED", severity="blue", title="Signal Generated", description=f"{signal_delta} new strategy signals generated.", metadata={"count": signal_delta})

    buy_delta = max(current.signals_buy - previous.signals_buy, 0)
    if buy_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="BUY_CANDIDATE_CREATED", severity="blue", title="BUY Candidate Created", description=f"{buy_delta} BUY candidates created.", metadata={"count": buy_delta})

    sell_delta = max(current.signals_sell - previous.signals_sell, 0)
    if sell_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="SELL_CANDIDATE_CREATED", severity="blue", title="SELL Candidate Created", description=f"{sell_delta} SELL candidates created.", metadata={"count": sell_delta})

    hold_delta = max(current.signals_hold - previous.signals_hold, 0)
    if hold_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="HOLD_DECISION_RECORDED", severity="blue", title="HOLD Decision Recorded", description=f"{hold_delta} HOLD decisions recorded.", metadata={"count": hold_delta})

    risk_delta = max(current.risk_total - previous.risk_total, 0)
    if risk_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="RISK_EVALUATION_STARTED", severity="yellow", title="Risk Evaluation Started", description=f"{risk_delta} risk evaluations observed.", metadata={"count": risk_delta})

    risk_approved_delta = max(current.risk_approved - previous.risk_approved, 0)
    if risk_approved_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="RISK_APPROVED", severity="green", title="Risk Approved", description=f"{risk_approved_delta} risk approvals observed.", metadata={"count": risk_approved_delta})
        _append_event(db=db, validation_run_id=validation_run_id, event_type="PAPER_TRADE_SUBMITTED", severity="blue", title="Paper Trade Submitted", description=f"{risk_approved_delta} paper trades submitted to execution.", metadata={"count": risk_approved_delta})

    risk_rejected_delta = max(current.risk_rejected - previous.risk_rejected, 0)
    if risk_rejected_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="RISK_REJECTED", severity="yellow", title="Risk Rejected", description=f"{risk_rejected_delta} risk rejections observed.", metadata={"count": risk_rejected_delta})
        _append_event(db=db, validation_run_id=validation_run_id, event_type="PAPER_TRADE_REJECTED", severity="yellow", title="Paper Trade Rejected", description=f"{risk_rejected_delta} paper trades rejected by risk.", metadata={"count": risk_rejected_delta})

    trade_delta = max(current.trades - previous.trades, 0)
    if trade_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="PAPER_TRADE_FILLED", severity="green", title="Paper Trade Filled", description=f"{trade_delta} paper trades filled.", metadata={"count": trade_delta})

    opened_delta = max(current.trade_buy - previous.trade_buy, 0)
    if opened_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="POSITION_OPENED", severity="green", title="Position Opened", description=f"{opened_delta} positions opened from BUY fills.", metadata={"count": opened_delta})

    closed_delta = max(current.trade_sell - previous.trade_sell, 0)
    if closed_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="POSITION_CLOSED", severity="blue", title="Position Closed", description=f"{closed_delta} positions closed from SELL fills.", metadata={"count": closed_delta})

    campaign_started_delta = max(current.campaign_started - previous.campaign_started, 0)
    if campaign_started_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="RESEARCH_CAMPAIGN_STARTED", severity="purple", title="Research Campaign Started", description=f"{campaign_started_delta} research campaigns entered started state.", metadata={"count": campaign_started_delta})

    campaign_completed_delta = max(current.campaign_completed - previous.campaign_completed, 0)
    if campaign_completed_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="RESEARCH_CAMPAIGN_COMPLETED", severity="purple", title="Research Campaign Completed", description=f"{campaign_completed_delta} research campaigns completed.", metadata={"count": campaign_completed_delta})

    evolution_delta = max(current.evolution_cycles - previous.evolution_cycles, 0)
    if evolution_delta > 0:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="EVOLUTION_CYCLE_STARTED", severity="purple", title="Evolution Cycle Started", description=f"{evolution_delta} evolution cycles started.", metadata={"count": evolution_delta})
        _append_event(db=db, validation_run_id=validation_run_id, event_type="EVOLUTION_CYCLE_COMPLETED", severity="purple", title="Evolution Cycle Completed", description=f"{evolution_delta} evolution cycles completed.", metadata={"count": evolution_delta})

    if current.champion and previous.champion != current.champion:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="CHAMPION_STRATEGY_CHANGED", severity="purple", title="Champion Strategy Changed", description=f"Champion strategy changed to {current.champion}.", metadata={"previous": previous.champion, "current": current.champion})

    if previous.db_version and current.db_version and previous.db_version != current.db_version:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="MIGRATION_DETECTED", severity="gray", title="Migration Detected", description="Database migration version changed during validation run.", metadata={"previous_version": previous.db_version, "current_version": current.db_version})

    if current.db_state in {"yellow", "red"} and previous.db_state != current.db_state:
        _append_event(db=db, validation_run_id=validation_run_id, event_type="DATABASE_HEALTH_WARNING", severity="yellow" if current.db_state == "yellow" else "red", title="Database Health Warning", description=f"Database health is {current.db_state}.", metadata={"previous_state": previous.db_state, "current_state": current.db_state})

    if previous.db_state in {"yellow", "red"} and current.db_state == "green":
        _append_event(db=db, validation_run_id=validation_run_id, event_type="DATABASE_RECOVERED", severity="green", title="Database Recovered", description="Database health recovered to green.", metadata={"previous_state": previous.db_state, "current_state": current.db_state})


async def _capture_stream_snapshot(*, db: AsyncSession, operations: OperationalStatusResponse) -> _EventStreamSnapshot:
    db_state = operations.system_health["database"].state
    return _EventStreamSnapshot(
        candles=await _count_safe(db=db, sql="SELECT COUNT(*) FROM candles"),
        assets=await _count_safe(db=db, sql="SELECT COUNT(*) FROM assets WHERE is_active = true"),
        signals_total=await _count_safe(db=db, sql="SELECT COUNT(*) FROM signals"),
        signals_buy=await _count_safe(db=db, sql="SELECT COUNT(*) FROM signals WHERE action = 'buy'"),
        signals_sell=await _count_safe(db=db, sql="SELECT COUNT(*) FROM signals WHERE action = 'sell'"),
        signals_hold=await _count_safe(db=db, sql="SELECT COUNT(*) FROM signals WHERE action = 'hold'"),
        risk_total=await _count_safe(db=db, sql="SELECT COUNT(*) FROM risk_events"),
        risk_approved=await _count_safe(db=db, sql="SELECT COUNT(*) FROM risk_events WHERE lower(action_taken) LIKE '%approved%'"),
        risk_rejected=await _count_safe(db=db, sql="SELECT COUNT(*) FROM risk_events WHERE lower(action_taken) LIKE '%rejected%'"),
        trades=await _count_safe(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true"),
        trade_buy=await _count_safe(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true AND side = 'buy'"),
        trade_sell=await _count_safe(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true AND side = 'sell'"),
        campaign_started=await _count_safe(db=db, sql="SELECT COUNT(*) FROM research_campaigns WHERE started_at IS NOT NULL"),
        campaign_completed=await _count_safe(db=db, sql="SELECT COUNT(*) FROM research_campaigns WHERE completed_at IS NOT NULL"),
        evolution_cycles=await _count_safe(db=db, sql="SELECT COUNT(*) FROM research_candidate_lineage"),
        champion=await _read_current_champion(db=db),
        db_state=db_state,
        db_version=await _db_version(db=db),
    )


def _append_event(
    *,
    db: AsyncSession,
    validation_run_id: uuid.UUID,
    event_type: str,
    severity: str,
    title: str,
    description: str,
    metadata: dict[str, Any],
) -> None:
    db.add(
        ValidationRunEvent(
            validation_run_id=validation_run_id,
            event_type=event_type,
            message=description,
            payload={
                "severity": severity,
                "title": title,
                "description": description,
                "metadata": metadata,
            },
        )
    )


async def _events_for_run(*, db: AsyncSession, validation_run_id: uuid.UUID) -> list[ValidationRunEvent]:
    return (
        await db.execute(
            select(ValidationRunEvent)
            .where(ValidationRunEvent.validation_run_id == validation_run_id)
            .order_by(ValidationRunEvent.created_at.desc(), ValidationRunEvent.id.desc())
        )
    ).scalars().all()


def _snapshot_from_event(event: ValidationRunEvent | None) -> _EventStreamSnapshot | None:
    if event is None:
        return None
    payload = dict(event.payload or {})
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    snapshot = metadata.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    return _EventStreamSnapshot(
        candles=int(snapshot.get("candles", 0)),
        assets=int(snapshot.get("assets", 0)),
        signals_total=int(snapshot.get("signals_total", 0)),
        signals_buy=int(snapshot.get("signals_buy", 0)),
        signals_sell=int(snapshot.get("signals_sell", 0)),
        signals_hold=int(snapshot.get("signals_hold", 0)),
        risk_total=int(snapshot.get("risk_total", 0)),
        risk_approved=int(snapshot.get("risk_approved", 0)),
        risk_rejected=int(snapshot.get("risk_rejected", 0)),
        trades=int(snapshot.get("trades", 0)),
        trade_buy=int(snapshot.get("trade_buy", 0)),
        trade_sell=int(snapshot.get("trade_sell", 0)),
        campaign_started=int(snapshot.get("campaign_started", 0)),
        campaign_completed=int(snapshot.get("campaign_completed", 0)),
        evolution_cycles=int(snapshot.get("evolution_cycles", 0)),
        champion=snapshot.get("champion") if isinstance(snapshot.get("champion"), str) else None,
        db_state=str(snapshot.get("db_state", "green")),
        db_version=snapshot.get("db_version") if isinstance(snapshot.get("db_version"), str) else None,
    )


def _snapshot_to_metadata(snapshot: _EventStreamSnapshot) -> dict[str, Any]:
    return {
        "candles": snapshot.candles,
        "assets": snapshot.assets,
        "signals_total": snapshot.signals_total,
        "signals_buy": snapshot.signals_buy,
        "signals_sell": snapshot.signals_sell,
        "signals_hold": snapshot.signals_hold,
        "risk_total": snapshot.risk_total,
        "risk_approved": snapshot.risk_approved,
        "risk_rejected": snapshot.risk_rejected,
        "trades": snapshot.trades,
        "trade_buy": snapshot.trade_buy,
        "trade_sell": snapshot.trade_sell,
        "campaign_started": snapshot.campaign_started,
        "campaign_completed": snapshot.campaign_completed,
        "evolution_cycles": snapshot.evolution_cycles,
        "champion": snapshot.champion,
        "db_state": snapshot.db_state,
        "db_version": snapshot.db_version,
    }


def _has_event_type(events: list[ValidationRunEvent], event_type: str) -> bool:
    return any(item.event_type == event_type for item in events)


async def _count_safe(*, db: AsyncSession, sql: str) -> int:
    try:
        result = await db.execute(text(sql))
        if hasattr(result, "scalar_one_or_none"):
            value = result.scalar_one_or_none()
            return int(value or 0)
    except Exception:
        return 0
    return 0


async def _db_version(*, db: AsyncSession) -> str | None:
    try:
        result = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        if hasattr(result, "scalar_one_or_none"):
            value = result.scalar_one_or_none()
            if isinstance(value, str):
                return value
    except Exception:
        return None
    return None


def _build_scorecards(
    *,
    operations: OperationalStatusResponse,
    metrics: ValidationRunMetricsResponse,
    run: ValidationRun,
) -> tuple[list[ValidationRunScorecardResponse], int]:
    alert_codes = {item.code for item in operations.alerts}

    category_rows: list[ValidationRunScorecardResponse] = [
        _score_from_indicator(
            category="API Health",
            indicator=operations.system_health["api"],
        ),
        _score_from_indicator(
            category="Worker Health",
            indicator=operations.system_health["orchestrator"],
        ),
        _score_from_indicator(
            category="Database Health",
            indicator=operations.system_health["database"],
        ),
        _score_data_ingestion(metrics=metrics, alert_codes=alert_codes),
        _score_strategy_execution(metrics=metrics),
        _score_paper_trading(metrics=metrics),
        _score_from_indicator(
            category="Research Agents",
            indicator=operations.system_health["research_agent"],
        ),
        _score_evolution_engine(metrics=metrics, run=run),
        _score_campaign_engine(metrics=metrics),
        _score_dashboard_data(metrics=metrics),
    ]

    overall = int(round(sum(item.score for item in category_rows) / len(category_rows), 0))
    return category_rows, overall


def _score_from_indicator(*, category: str, indicator) -> ValidationRunScorecardResponse:
    if indicator.state == "green":
        return ValidationRunScorecardResponse(category=category, status="GREEN", score=100, notes=indicator.detail)
    if indicator.state == "yellow":
        return ValidationRunScorecardResponse(category=category, status="YELLOW", score=70, notes=indicator.detail)
    return ValidationRunScorecardResponse(category=category, status="RED", score=20, notes=indicator.detail)


def _score_data_ingestion(*, metrics: ValidationRunMetricsResponse, alert_codes: set[str]) -> ValidationRunScorecardResponse:
    if "no_new_candles" in alert_codes:
        return ValidationRunScorecardResponse(
            category="Data Ingestion",
            status="RED",
            score=20,
            notes="No new candles observed",
        )
    if metrics.candles_processed_during_run <= 0:
        return ValidationRunScorecardResponse(
            category="Data Ingestion",
            status="YELLOW",
            score=65,
            notes="Candle growth not yet observed",
        )
    return ValidationRunScorecardResponse(
        category="Data Ingestion",
        status="GREEN",
        score=100,
        notes="Candles are being processed",
    )


def _score_strategy_execution(*, metrics: ValidationRunMetricsResponse) -> ValidationRunScorecardResponse:
    if metrics.signals_generated_during_run <= 0 and metrics.decision_records_created_during_run <= 0:
        return ValidationRunScorecardResponse(
            category="Strategy Execution",
            status="YELLOW",
            score=60,
            notes="No strategy execution output yet",
        )
    if metrics.signals_generated_during_run > 0 and metrics.decision_records_created_during_run > 0:
        return ValidationRunScorecardResponse(
            category="Strategy Execution",
            status="GREEN",
            score=100,
            notes="Signals and decision records are flowing",
        )
    return ValidationRunScorecardResponse(
        category="Strategy Execution",
        status="YELLOW",
        score=75,
        notes="Partial strategy execution output observed",
    )


def _score_paper_trading(*, metrics: ValidationRunMetricsResponse) -> ValidationRunScorecardResponse:
    if metrics.trades_executed_during_run > 0:
        return ValidationRunScorecardResponse(
            category="Paper Trading",
            status="GREEN",
            score=100,
            notes="Paper trades executed during run",
        )
    if metrics.signals_generated_during_run > 0:
        return ValidationRunScorecardResponse(
            category="Paper Trading",
            status="YELLOW",
            score=70,
            notes="Signals generated but no paper trades yet",
        )
    return ValidationRunScorecardResponse(
        category="Paper Trading",
        status="YELLOW",
        score=60,
        notes="No paper trading activity yet",
    )


def _score_evolution_engine(*, metrics: ValidationRunMetricsResponse, run: ValidationRun) -> ValidationRunScorecardResponse:
    enabled = any(feature.lower() == "evolution" for feature in run.enabled_research_features)
    if not enabled:
        return ValidationRunScorecardResponse(
            category="Evolution Engine",
            status="YELLOW",
            score=70,
            notes="Evolution feature not enabled for this run",
        )

    if metrics.evolution_descendants > 0:
        return ValidationRunScorecardResponse(
            category="Evolution Engine",
            status="GREEN",
            score=100,
            notes="Evolution descendants generated",
        )

    return ValidationRunScorecardResponse(
        category="Evolution Engine",
        status="YELLOW",
        score=65,
        notes="Evolution enabled but no descendants yet",
    )


def _score_campaign_engine(*, metrics: ValidationRunMetricsResponse) -> ValidationRunScorecardResponse:
    if metrics.candidates_generated > 0:
        return ValidationRunScorecardResponse(
            category="Campaign Engine",
            status="GREEN",
            score=100,
            notes="Campaign and candidate generation activity observed",
        )
    return ValidationRunScorecardResponse(
        category="Campaign Engine",
        status="YELLOW",
        score=65,
        notes="No campaign candidate growth observed yet",
    )


def _score_dashboard_data(*, metrics: ValidationRunMetricsResponse) -> ValidationRunScorecardResponse:
    if metrics.alerts_count > 0:
        return ValidationRunScorecardResponse(
            category="Dashboard Data",
            status="YELLOW",
            score=70,
            notes="Dashboard data available with active alerts",
        )

    return ValidationRunScorecardResponse(
        category="Dashboard Data",
        status="GREEN",
        score=100,
        notes="Dashboard data available with no alerts",
    )


async def _upsert_scorecards(
    *,
    db: AsyncSession,
    validation_run_id: uuid.UUID,
    scorecards: list[ValidationRunScorecardResponse],
) -> None:
    existing = (
        await db.execute(
            select(ValidationRunScorecard)
            .where(ValidationRunScorecard.validation_run_id == validation_run_id)
        )
    ).scalars().all()
    existing_by_category = {item.category: item for item in existing}

    now = datetime.now(timezone.utc)
    for item in scorecards:
        row = existing_by_category.get(item.category)
        if row is None:
            db.add(
                ValidationRunScorecard(
                    validation_run_id=validation_run_id,
                    category=item.category,
                    status=item.status,
                    score=item.score,
                    notes=item.notes,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.status = item.status
            row.score = item.score
            row.notes = item.notes
            row.updated_at = now

    await db.flush()


async def _load_scorecards(
    *,
    db: AsyncSession,
    validation_run_id: uuid.UUID,
) -> tuple[list[ValidationRunScorecardResponse], int]:
    rows = (
        await db.execute(
            select(ValidationRunScorecard)
            .where(ValidationRunScorecard.validation_run_id == validation_run_id)
            .order_by(ValidationRunScorecard.category.asc())
        )
    ).scalars().all()

    scorecards = [
        ValidationRunScorecardResponse(
            category=item.category,
            status=item.status,
            score=item.score,
            notes=item.notes,
        )
        for item in rows
    ]

    if not scorecards:
        return [], 0

    overall = int(round(sum(item.score for item in scorecards) / len(scorecards), 0))
    return scorecards, overall
