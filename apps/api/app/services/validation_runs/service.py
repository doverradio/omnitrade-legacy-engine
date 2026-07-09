from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

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
    ValidationRunMetricsResponse,
    ValidationRunResponse,
    ValidationRunScorecardResponse,
)
from app.services.operations_status import build_operations_status
from app.services.paper.accounting import build_account_snapshot

_ALLOWED_STATUSES = {"DRAFT", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
_ALLOWED_RESULT_STATUSES = {"PASS", "CONDITIONAL_PASS", "FAIL", "INCOMPLETE"}


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
            event_type="VALIDATION_RUN_CREATED",
            message="Validation run created",
            payload={
                "status": "DRAFT",
                "duration_hours": run.duration_hours,
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
            payload={"completed_at": now.isoformat()},
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


async def list_validation_run_events(*, db: AsyncSession, validation_run_id: uuid.UUID) -> list[ValidationRunEventResponse]:
    await _load_run(db=db, validation_run_id=validation_run_id)
    rows = (
        await db.execute(
            select(ValidationRunEvent)
            .where(ValidationRunEvent.validation_run_id == validation_run_id)
            .order_by(ValidationRunEvent.created_at.desc(), ValidationRunEvent.id.desc())
        )
    ).scalars().all()

    return [
        ValidationRunEventResponse(
            event_type=item.event_type,
            message=item.message,
            payload=dict(item.payload),
            created_at=item.created_at,
        )
        for item in rows
    ]


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
