from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.mission_control import (
    MissionControlIntelligenceHistoryPointResponse,
    MissionControlIntelligenceMetricResponse,
    MissionControlIntelligenceResponse,
    MissionControlIntelligenceTimelineEventResponse,
    MissionControlIntelligenceTrendResponse,
)
from app.schemas.operations import OperationalAlertResponse, OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunResponse
from app.services.dashboard_intelligence import build_dashboard_intelligence_score
from app.services.operations_status import build_operations_status
from app.services.validation_runs.service import list_validation_run_events, list_validation_runs

_RANGE_CONFIG: dict[str, tuple[int, int]] = {
    "24h": (8, 60),
    "72h": (12, 6 * 60),
    "7d": (12, 12 * 60),
    "30d": (16, 24 * 60),
    "90d": (20, 3 * 24 * 60),
    "all": (24, 5 * 24 * 60),
}


@dataclass(frozen=True, slots=True)
class _ComponentScores:
    prediction_quality: int
    risk_discipline: int
    research_activity: int
    execution_health: int
    infrastructure_health: int
    paper_trading_health: int
    worker_uptime: int


async def build_mission_control_intelligence(*, db: AsyncSession, range_value: str) -> MissionControlIntelligenceResponse:
    normalized_range = range_value.strip().lower()
    if normalized_range not in _RANGE_CONFIG:
        normalized_range = "24h"

    generated_at = datetime.now(timezone.utc)
    operations = await build_operations_status(db=db)
    dashboard_score = None
    if hasattr(db, "scalar") and hasattr(db, "execute"):
        dashboard_score = await build_dashboard_intelligence_score(db=db, range_value=normalized_range)
    validation_runs = await list_validation_runs(db=db)
    selected_validation_run = _pick_selected_run(validation_runs)
    run_events = await _load_run_events(db=db, validation_run=selected_validation_run)

    component_scores = _compute_component_scores(operations=operations, validation_runs=validation_runs)
    current_score = dashboard_score.score if dashboard_score is not None else _aggregate_score(component_scores)
    if dashboard_score is not None:
        baseline_equity = dashboard_score.timeline[0].equity if dashboard_score.timeline else Decimal("0")
        history = [
            MissionControlIntelligenceHistoryPointResponse(
                timestamp=item.timestamp,
                score=item.score,
                paper_equity=format(item.equity, "f"),
                paper_pnl=format(item.equity - baseline_equity, "f"),
                signals=operations.monitoring.signals_generated,
                trades=operations.monitoring.paper_trades_executed,
                decision_count=operations.monitoring.decision_records_created,
                health=item.operational_health,
            )
            for item in dashboard_score.timeline
        ]
    else:
        history = _build_history(
            generated_at=generated_at,
            range_value=normalized_range,
            current_score=current_score,
            operations=operations,
            validation_runs=validation_runs,
            selected_validation_run=selected_validation_run,
            run_events=run_events,
        )
    trend = _build_trend(history=history)
    timeline_events = _build_timeline_events(
        generated_at=generated_at,
        operations=operations,
        selected_validation_run=selected_validation_run,
        run_events=run_events,
        current_score=current_score,
    )
    metric_breakdown = _build_metric_breakdown(
        component_scores=component_scores,
        history=history,
    )

    confidence = _confidence_for_score(current_score=current_score, operations=operations, validation_runs=validation_runs)
    delta_label = trend.delta_label

    return MissionControlIntelligenceResponse(
        version="v1",
        range=normalized_range,
        generated_at=generated_at,
        current_score=current_score,
        delta_label=delta_label,
        confidence=confidence,
        trend=trend,
        history=history,
        timeline_events=timeline_events,
        metric_breakdown=metric_breakdown,
        operations=operations,
        validation_runs=validation_runs,
        selected_validation_run_id=None if selected_validation_run is None else str(selected_validation_run.validation_run_id),
        notes=(
            "Mission Control Intelligence Center V1 is a deterministic placeholder built from available operational metrics. "
            "It is informational only and does not change trading, research, or allocation behavior."
        ),
    )


async def _load_run_events(*, db: AsyncSession, validation_run: ValidationRunResponse | None):
    if validation_run is None:
        return []

    response = await list_validation_run_events(
        db=db,
        validation_run_id=validation_run.validation_run_id,
        page=1,
        page_size=50,
        order="oldest",
        window="entire_run",
        category="all",
        severity="all",
        search=None,
    )
    return list(response.items)


def _pick_selected_run(validation_runs: list[ValidationRunResponse]) -> ValidationRunResponse | None:
    running = next((item for item in validation_runs if item.status == "RUNNING"), None)
    return running or (validation_runs[0] if validation_runs else None)


def _compute_component_scores(*, operations: OperationalStatusResponse, validation_runs: list[ValidationRunResponse]) -> _ComponentScores:
    latest_run = validation_runs[0] if validation_runs else None
    validation_health = _score_validation_health(latest_run)
    worker_uptime = _score_worker_uptime(operations)
    infrastructure = _score_infrastructure_health(operations=operations, worker_uptime=worker_uptime)
    paper_trading = _score_paper_trading_health(operations=operations)
    prediction_quality = _score_prediction_quality(operations=operations, validation_health=validation_health)
    risk_discipline = _score_risk_discipline(operations=operations, validation_health=validation_health)
    research_activity = _score_research_activity(operations=operations)
    execution_health = _score_execution_health(operations=operations, paper_trading=paper_trading)

    return _ComponentScores(
        prediction_quality=prediction_quality,
        risk_discipline=risk_discipline,
        research_activity=research_activity,
        execution_health=execution_health,
        infrastructure_health=infrastructure,
        paper_trading_health=paper_trading,
        worker_uptime=worker_uptime,
    )


def _aggregate_score(component_scores: _ComponentScores) -> int:
    weighted_pairs = [
        (component_scores.prediction_quality, 20),
        (component_scores.risk_discipline, 15),
        (component_scores.research_activity, 15),
        (component_scores.execution_health, 15),
        (component_scores.infrastructure_health, 20),
        (component_scores.paper_trading_health, 10),
        (component_scores.worker_uptime, 5),
    ]
    score = sum(value * weight for value, weight in weighted_pairs) / sum(weight for _, weight in weighted_pairs)
    return int(round(max(0.0, min(100.0, score))))


def _score_validation_health(validation_run: ValidationRunResponse | None) -> int:
    if validation_run is None:
        return 0

    health_score = validation_run.health_score if validation_run.health_score is not None else 65
    if validation_run.status == "RUNNING":
        return _clamp_score(health_score)
    if validation_run.status == "COMPLETED" and validation_run.result_status in {"PASS", "CONDITIONAL_PASS"}:
        return _clamp_score(health_score + 8)
    if validation_run.status in {"FAILED", "CANCELLED"}:
        return _clamp_score(health_score - 12)
    return _clamp_score(health_score)


def _score_worker_uptime(operations: OperationalStatusResponse) -> int:
    orchestrator = operations.system_health.get("orchestrator")
    if orchestrator is None:
        return 50
    if orchestrator.state == "green":
        return 92
    if orchestrator.state == "yellow":
        return 68
    return 28


def _score_infrastructure_health(*, operations: OperationalStatusResponse, worker_uptime: int) -> int:
    api = _state_score(operations.system_health.get("api"))
    database = _state_score(operations.system_health.get("database"))
    research_agent = _state_score(operations.system_health.get("research_agent"))
    return _clamp_score(mean([api, database, research_agent, worker_uptime]))


def _score_paper_trading_health(*, operations: OperationalStatusResponse) -> int:
    monitoring = operations.monitoring
    equity_value = _decimal_from_string(monitoring.paper_equity)
    equity_score = 55 + min(35, int(equity_value / Decimal("10000"))) if equity_value > 0 else 0
    trade_activity = _activity_score(monitoring.paper_trades_executed + monitoring.trades_today, cap=40)
    return _clamp_score(mean([equity_score, trade_activity]))


def _score_prediction_quality(*, operations: OperationalStatusResponse, validation_health: int) -> int:
    monitoring = operations.monitoring
    signal_score = _activity_score(monitoring.signals_generated + monitoring.signals_today, cap=85)
    decision_score = _activity_score(monitoring.decision_records_created + monitoring.replay_count, cap=85)
    return _clamp_score(mean([validation_health, signal_score, decision_score]))


def _score_risk_discipline(*, operations: OperationalStatusResponse, validation_health: int) -> int:
    alerts_penalty = min(30, len(operations.alerts) * 6)
    health_bonus = validation_health // 4
    return _clamp_score(80 + health_bonus - alerts_penalty)


def _score_research_activity(*, operations: OperationalStatusResponse) -> int:
    monitoring = operations.monitoring
    base = (
        monitoring.candidate_count * 2
        + monitoring.campaign_count * 8
        + monitoring.laboratory_runs * 3
        + monitoring.evolution_count * 2
        + monitoring.research_memory_growth // 10
    )
    return _clamp_score(min(100, 35 + base))


def _score_execution_health(*, operations: OperationalStatusResponse, paper_trading: int) -> int:
    monitoring = operations.monitoring
    activity = _activity_score(monitoring.paper_trades_executed + monitoring.trades_today + monitoring.decision_records_created, cap=90)
    return _clamp_score(mean([paper_trading, activity]))


def _build_history(
    *,
    generated_at: datetime,
    range_value: str,
    current_score: int,
    operations: OperationalStatusResponse,
    validation_runs: list[ValidationRunResponse],
    selected_validation_run: ValidationRunResponse | None,
    run_events,
) -> list[MissionControlIntelligenceHistoryPointResponse]:
    point_count, interval_minutes = _RANGE_CONFIG[range_value]
    monitoring = operations.monitoring
    current_equity = _decimal_from_string(monitoring.paper_equity)
    start_equity = max(Decimal("0"), current_equity - Decimal(point_count * 250))
    current_pnl = current_equity - start_equity
    start_score = max(0, current_score - min(14, max(4, len(run_events) // 2 + 2)))
    history: list[MissionControlIntelligenceHistoryPointResponse] = []

    for index in range(point_count):
        if point_count == 1:
            progress = 1.0
        else:
            progress = index / (point_count - 1)
        timestamp = generated_at - timedelta(minutes=interval_minutes * (point_count - 1 - index))
        score = int(round(start_score + (current_score - start_score) * progress))
        equity = start_equity + (current_equity - start_equity) * Decimal(str(progress))
        pnl = current_pnl * Decimal(str(progress))
        signals = int(round(monitoring.signals_generated * progress))
        trades = int(round(monitoring.paper_trades_executed * progress))
        decisions = int(round(monitoring.decision_records_created * progress))
        health = _clamp_score(score + (2 if selected_validation_run and selected_validation_run.status == "RUNNING" else 0))

        history.append(
            MissionControlIntelligenceHistoryPointResponse(
                timestamp=timestamp,
                score=score,
                paper_equity=format(equity.quantize(Decimal("0.01")), "f"),
                paper_pnl=format(pnl.quantize(Decimal("0.01")), "f"),
                signals=signals,
                trades=trades,
                decision_count=decisions,
                health=health,
            )
        )

    return history


def _build_trend(*, history: list[MissionControlIntelligenceHistoryPointResponse]) -> MissionControlIntelligenceTrendResponse:
    if len(history) < 2:
        return MissionControlIntelligenceTrendResponse(
            direction="flat",
            label="Stable",
            delta_label="0 this period",
            confidence="Low",
        )

    delta = history[-1].score - history[0].score
    if delta > 1:
        direction = "up"
        label = "Improving"
        prefix = "+"
    elif delta < -1:
        direction = "down"
        label = "Softening"
        prefix = ""
    else:
        direction = "flat"
        label = "Stable"
        prefix = ""

    period = "this week" if len(history) >= 12 else "this period"
    confidence = "High" if len(history) >= 12 else "Medium"
    return MissionControlIntelligenceTrendResponse(
        direction=direction,
        label=label,
        delta_label=f"{prefix}{delta} {period}".replace("--", "-"),
        confidence=confidence,
    )


def _build_timeline_events(
    *,
    generated_at: datetime,
    operations: OperationalStatusResponse,
    selected_validation_run: ValidationRunResponse | None,
    run_events,
    current_score: int,
) -> list[MissionControlIntelligenceTimelineEventResponse]:
    timeline: list[MissionControlIntelligenceTimelineEventResponse] = []
    anchor_equity = operations.monitoring.paper_equity
    anchor_pnl = _derive_paper_pnl(anchor_equity)
    current_health = current_score

    for item in run_events:
        severity = str(getattr(item, "severity", "gray"))
        timeline.append(
            MissionControlIntelligenceTimelineEventResponse(
                event_id=f"validation-{item.id}",
                timestamp=item.timestamp,
                title=item.title,
                description=item.description,
                related_validation_run=str(item.validation_run_id),
                health_at_that_moment=_severity_to_health(severity, current_health),
                paper_equity=anchor_equity,
                paper_pnl=anchor_pnl,
                signals=operations.monitoring.signals_generated,
                trades=operations.monitoring.paper_trades_executed,
                decision_count=operations.monitoring.decision_records_created,
                severity=severity,
                category=item.category,
                event_type=item.event_type,
                metadata=item.metadata,
            )
        )

    for offset, alert in enumerate(operations.alerts):
        timeline.append(
            MissionControlIntelligenceTimelineEventResponse(
                event_id=f"alert-{alert.code}",
                timestamp=generated_at - timedelta(minutes=max(1, (len(operations.alerts) - offset) * 5)),
                title=alert.message,
                description=f"Operational alert: {alert.message}",
                related_validation_run=None if selected_validation_run is None else str(selected_validation_run.validation_run_id),
                health_at_that_moment=_severity_to_health(alert.severity, current_health),
                paper_equity=anchor_equity,
                paper_pnl=anchor_pnl,
                signals=operations.monitoring.signals_generated,
                trades=operations.monitoring.paper_trades_executed,
                decision_count=operations.monitoring.decision_records_created,
                severity=alert.severity,
                category="system",
                event_type=alert.code,
                metadata={"code": alert.code},
            )
        )

    return sorted(timeline, key=lambda item: item.timestamp)


def _build_metric_breakdown(
    *,
    component_scores: _ComponentScores,
    history: list[MissionControlIntelligenceHistoryPointResponse],
) -> list[MissionControlIntelligenceMetricResponse]:
    latest_score = history[-1].score if history else 0
    previous_score = history[0].score if history else 0
    trend = _trend_from_delta(latest_score - previous_score)
    return [
        MissionControlIntelligenceMetricResponse(
            name="Prediction Quality",
            score=component_scores.prediction_quality,
            trend=trend,
            sparkline=_sparkline(component_scores.prediction_quality, trend.direction, seed=3),
            details="Validation health, signal generation, and decision activity.",
        ),
        MissionControlIntelligenceMetricResponse(
            name="Risk Discipline",
            score=component_scores.risk_discipline,
            trend=trend,
            sparkline=_sparkline(component_scores.risk_discipline, trend.direction, seed=7),
            details="Alerts, operational risk, and validation stability.",
        ),
        MissionControlIntelligenceMetricResponse(
            name="Research Activity",
            score=component_scores.research_activity,
            trend=trend,
            sparkline=_sparkline(component_scores.research_activity, trend.direction, seed=11),
            details="Campaigns, laboratory runs, evolution, and memory growth.",
        ),
        MissionControlIntelligenceMetricResponse(
            name="Execution Health",
            score=component_scores.execution_health,
            trend=trend,
            sparkline=_sparkline(component_scores.execution_health, trend.direction, seed=13),
            details="Paper trade throughput and decision execution velocity.",
        ),
        MissionControlIntelligenceMetricResponse(
            name="Infrastructure Health",
            score=component_scores.infrastructure_health,
            trend=trend,
            sparkline=_sparkline(component_scores.infrastructure_health, trend.direction, seed=17),
            details="API, worker, database, and research adapter health.",
        ),
        MissionControlIntelligenceMetricResponse(
            name="Paper Trading Health",
            score=component_scores.paper_trading_health,
            trend=trend,
            sparkline=_sparkline(component_scores.paper_trading_health, trend.direction, seed=19),
            details="Paper equity, fills, and overall proving throughput.",
        ),
    ]


def _sparkline(score: int, direction: str, *, seed: int) -> list[int]:
    values: list[int] = []
    step = 3 if direction == "up" else -3 if direction == "down" else 0
    for index in range(6):
        offset = ((index + seed) % 3) - 1
        values.append(_clamp_score(score + step * index + offset * 2))
    return values


def _trend_from_delta(delta: int) -> MissionControlIntelligenceTrendResponse:
    if delta > 1:
        return MissionControlIntelligenceTrendResponse(direction="up", label="Improving", delta_label=f"+{delta} this week", confidence="High")
    if delta < -1:
        return MissionControlIntelligenceTrendResponse(direction="down", label="Softening", delta_label=f"{delta} this week", confidence="Medium")
    return MissionControlIntelligenceTrendResponse(direction="flat", label="Stable", delta_label="0 this week", confidence="Medium")


def _confidence_for_score(*, current_score: int, operations: OperationalStatusResponse, validation_runs: list[ValidationRunResponse]) -> str:
    available_signals = 4
    if validation_runs:
        available_signals += 1
    if operations.alerts:
        available_signals += 1
    if operations.monitoring.paper_trades_executed > 0:
        available_signals += 1

    if current_score >= 80 and available_signals >= 5:
        return "High"
    if current_score >= 60:
        return "Medium"
    return "Low"


def _state_score(indicator) -> int:
    if indicator is None:
        return 50
    if indicator.state == "green":
        return 100
    if indicator.state == "yellow":
        return 65
    return 30


def _activity_score(value: int, *, cap: int) -> int:
    return _clamp_score(min(100, 35 + min(cap, value) * 2))


def _severity_to_health(severity: str, current_health: int) -> int | None:
    if severity == "red":
        return _clamp_score(current_health - 18)
    if severity == "yellow":
        return _clamp_score(current_health - 8)
    if severity in {"green", "blue"}:
        return _clamp_score(current_health + 4)
    return current_health


def _decimal_from_string(value: str) -> Decimal:
    try:
        return Decimal(value)
    except Exception:
        return Decimal("0")


def _derive_paper_pnl(paper_equity: str) -> str:
    equity = _decimal_from_string(paper_equity)
    baseline = Decimal("100000")
    return format((equity - baseline).quantize(Decimal("0.01")), "f")


def _clamp_score(value: float | int) -> int:
    return int(round(max(0.0, min(100.0, float(value)))))