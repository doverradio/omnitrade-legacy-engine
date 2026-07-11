from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_profit_cycle import CapitalCampaignProfitCycle
from app.models.capital_campaign_profit_policy import CapitalCampaignProfitPolicy
from app.models.paper_account import PaperAccount
from app.models.validation_run_paper_account import ValidationRunPaperAccount
from app.schemas.mission_control import (
    MissionControlIntelligenceHistoryPointResponse,
    MissionControlIntelligenceMetricResponse,
    MissionControlIntelligenceResponse,
    MissionControlIntelligenceTimelineEventResponse,
    MissionControlIntelligenceTrendResponse,
)
from app.schemas.operations import OperationalAlertResponse, OperationalStatusResponse
from app.schemas.validation_runs import ValidationRunResponse
from app.services.paper.accounting import build_account_snapshot
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
    total_managed_capital = await _load_total_managed_capital(db=db)
    campaign_profit_metrics = await _load_campaign_profit_metrics(db=db)
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
    timeline_equity, timeline_pnl, timeline_pnl_metadata = await _resolve_timeline_equity_and_pnl(
        db=db,
        selected_validation_run=selected_validation_run,
        fallback_equity=operations.monitoring.paper_equity,
    )
    timeline_events = _build_timeline_events(
        generated_at=generated_at,
        operations=operations,
        selected_validation_run=selected_validation_run,
        run_events=run_events,
        current_score=current_score,
        anchor_equity=timeline_equity,
        anchor_pnl=timeline_pnl,
        paper_pnl_metadata=timeline_pnl_metadata,
    )
    timeline_events.extend(
        await _load_live_operation_annotations(
            db=db,
            generated_at=generated_at,
            current_score=current_score,
            operations=operations,
            anchor_equity=timeline_equity,
            anchor_pnl=timeline_pnl,
            paper_pnl_metadata=timeline_pnl_metadata,
        )
    )
    timeline_events = sorted(timeline_events, key=lambda item: item.timestamp)
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
        total_managed_capital=None if total_managed_capital is None else format(total_managed_capital, "f"),
        campaigns_near_profit_target=campaign_profit_metrics["campaigns_near_profit_target"],
        campaigns_at_target=campaign_profit_metrics["campaigns_at_target"],
        profit_eligible_for_compounding=format(campaign_profit_metrics["profit_eligible_for_compounding"], "f"),
        profit_recommended_for_withdrawal=format(campaign_profit_metrics["profit_recommended_for_withdrawal"], "f"),
        profit_awaiting_review=format(campaign_profit_metrics["profit_awaiting_review"], "f"),
        active_compounding_policies=campaign_profit_metrics["active_compounding_policies"],
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


async def _load_total_managed_capital(*, db: AsyncSession) -> Decimal | None:
    if not hasattr(db, "execute"):
        return Decimal("0")
    campaigns = (
        (
            await db.execute(
                select(CapitalCampaign).order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return _calculate_total_managed_capital(campaigns)


def _calculate_total_managed_capital(campaigns: list[CapitalCampaign]) -> Decimal:
    active_statuses = {"READY", "RUNNING", "PAUSED", "TARGET_REACHED"}
    total = Decimal("0")
    seen_campaign_ids: set[str] = set()
    seen_paper_account_ids: set[str] = set()

    for campaign in campaigns:
        if campaign.status not in active_statuses:
            continue

        campaign_id = str(campaign.uuid)
        if campaign_id in seen_campaign_ids:
            continue

        if campaign.paper_account_id is not None:
            account_id = str(campaign.paper_account_id)
            if account_id in seen_paper_account_ids:
                continue
            seen_paper_account_ids.add(account_id)

        total += Decimal(str(campaign.starting_capital))
        seen_campaign_ids.add(campaign_id)

    return total


async def _load_campaign_profit_metrics(*, db: AsyncSession) -> dict[str, int | Decimal]:
    if not hasattr(db, "execute"):
        return {
            "campaigns_near_profit_target": 0,
            "campaigns_at_target": 0,
            "profit_eligible_for_compounding": Decimal("0"),
            "profit_recommended_for_withdrawal": Decimal("0"),
            "profit_awaiting_review": Decimal("0"),
            "active_compounding_policies": 0,
        }

    campaigns = (
        (
            await db.execute(
                select(CapitalCampaign).order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
            )
        )
        .scalars()
        .all()
    )
    active_policies = (
        (
            await db.execute(
                select(CapitalCampaignProfitPolicy).where(CapitalCampaignProfitPolicy.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    latest_cycles = (
        (
            await db.execute(
                select(CapitalCampaignProfitCycle)
                .order_by(
                    CapitalCampaignProfitCycle.capital_campaign_id.asc(),
                    CapitalCampaignProfitCycle.cycle_number.desc(),
                    CapitalCampaignProfitCycle.cycle_id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )

    latest_cycle_by_campaign: dict[int, CapitalCampaignProfitCycle] = {}
    for cycle in latest_cycles:
        if cycle.capital_campaign_id not in latest_cycle_by_campaign:
            latest_cycle_by_campaign[cycle.capital_campaign_id] = cycle

    policy_by_campaign: dict[int, CapitalCampaignProfitPolicy] = {item.capital_campaign_id: item for item in active_policies}

    near_target = 0
    at_target = 0
    eligible_for_compounding = Decimal("0")
    recommended_for_withdrawal = Decimal("0")
    awaiting_review = Decimal("0")
    active_compounding_policies = 0

    for campaign in campaigns:
        policy = policy_by_campaign.get(campaign.id)
        if policy is None:
            continue

        if policy.policy_type in {"FULL_COMPOUND", "PARTIAL_COMPOUND", "WITHDRAW_AND_COMPOUND", "PROTECTED_PRINCIPAL"}:
            active_compounding_policies += 1

        realized_profit = Decimal(str(campaign.realized_profit))
        progress_candidates: list[Decimal] = []
        reached = False

        if policy.profit_target_amount is not None and policy.profit_target_amount > 0:
            progress = (realized_profit / Decimal(str(policy.profit_target_amount))) * Decimal("100")
            progress_candidates.append(progress)
            if progress >= Decimal("100"):
                reached = True

        if policy.profit_target_percent is not None and policy.profit_target_percent > 0 and campaign.starting_capital > 0:
            realized_percent = (realized_profit / Decimal(str(campaign.starting_capital))) * Decimal("100")
            progress = (realized_percent / Decimal(str(policy.profit_target_percent))) * Decimal("100")
            progress_candidates.append(progress)
            if progress >= Decimal("100"):
                reached = True

        if reached:
            at_target += 1
        elif progress_candidates and max(progress_candidates) >= Decimal("80"):
            near_target += 1

        cycle = latest_cycle_by_campaign.get(campaign.id)
        if cycle is None:
            continue

        eligible_for_compounding += Decimal(str(cycle.compound_amount))
        recommended_for_withdrawal += Decimal(str(cycle.withdrawal_amount))
        if cycle.status == "REVIEW_REQUIRED":
            awaiting_review += Decimal(str(cycle.compound_amount + cycle.withdrawal_amount))

    return {
        "campaigns_near_profit_target": near_target,
        "campaigns_at_target": at_target,
        "profit_eligible_for_compounding": eligible_for_compounding,
        "profit_recommended_for_withdrawal": recommended_for_withdrawal,
        "profit_awaiting_review": awaiting_review,
        "active_compounding_policies": active_compounding_policies,
    }


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
    anchor_equity: str,
    anchor_pnl: str | None,
    paper_pnl_metadata: dict[str, object],
) -> list[MissionControlIntelligenceTimelineEventResponse]:
    timeline: list[MissionControlIntelligenceTimelineEventResponse] = []
    current_health = current_score

    for item in run_events:
        severity = str(getattr(item, "severity", "gray"))
        event_metadata = dict(item.metadata or {})
        for key, value in paper_pnl_metadata.items():
            event_metadata.setdefault(key, value)
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
                metadata=event_metadata,
            )
        )

    for offset, alert in enumerate(operations.alerts):
        alert_metadata: dict[str, object] = {"code": alert.code}
        for key, value in paper_pnl_metadata.items():
            alert_metadata.setdefault(key, value)
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
                metadata=alert_metadata,
            )
        )

    return sorted(timeline, key=lambda item: item.timestamp)


async def _load_live_operation_annotations(
    *,
    db: AsyncSession,
    generated_at: datetime,
    current_score: int,
    operations: OperationalStatusResponse,
    anchor_equity: str,
    anchor_pnl: str | None,
    paper_pnl_metadata: dict[str, object],
) -> list[MissionControlIntelligenceTimelineEventResponse]:
    if not hasattr(db, "execute"):
        return []

    tracked_actions = {
        "CONNECTION_VERIFIED",
        "PREVIEW_GENERATED",
        "DRY_RUN_READY",
        "DRY_RUN_BLOCKED",
        "CREDENTIAL_ROTATED",
        "CONNECTION_DISCONNECTED",
    }
    rows = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.action.in_(tracked_actions))
            .order_by(AuditLog.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    events: list[MissionControlIntelligenceTimelineEventResponse] = []
    for index, row in enumerate(rows):
        action = str(row.action)
        severity = "green"
        if action in {"DRY_RUN_BLOCKED", "CONNECTION_DISCONNECTED"}:
            severity = "yellow"
        if action == "CONNECTION_DISCONNECTED":
            severity = "red"
        after_state = row.after_state if isinstance(row.after_state, dict) else {}
        dry_run_metadata: dict[str, object] = {}
        if action in {"DRY_RUN_READY", "DRY_RUN_BLOCKED"}:
            dry_run_metadata = {
                "submission_skipped": bool(after_state.get("submission_skipped", True)),
                "submission_skip_reason": str(after_state.get("submission_skip_reason", "dry_run_submission_skipped")),
                "approval_event_id": after_state.get("approval_event_id"),
                "risk_event_id": after_state.get("risk_event_id"),
                "approved_intent_fingerprint": after_state.get("approved_intent_fingerprint"),
                "evidence_fingerprint": after_state.get("evidence_fingerprint"),
                "readiness_age_seconds": after_state.get("readiness_age_seconds"),
                "balance_age_seconds": after_state.get("balance_age_seconds"),
                "price_age_seconds": after_state.get("price_age_seconds"),
                "requested_quote_size": after_state.get("requested_quote_size"),
                "approved_quote_size": after_state.get("approved_quote_size"),
                "max_order_usd": after_state.get("max_order_usd"),
            }
        events.append(
            MissionControlIntelligenceTimelineEventResponse(
                event_id=f"live-ops-{row.id}",
                timestamp=row.created_at,
                title=action,
                description=f"Live operations annotation recorded: {action}",
                related_validation_run=None,
                health_at_that_moment=_severity_to_health(severity, current_score),
                paper_equity=anchor_equity,
                paper_pnl=anchor_pnl,
                signals=operations.monitoring.signals_generated,
                trades=operations.monitoring.paper_trades_executed,
                decision_count=operations.monitoring.decision_records_created,
                severity=severity,
                category="system",
                event_type=action,
                metadata={
                    "entity_type": row.entity_type,
                    "entity_id": None if row.entity_id is None else str(row.entity_id),
                    "submission_implied": False,
                    "order_submitted": False,
                    "index": index,
                    **dry_run_metadata,
                    **paper_pnl_metadata,
                },
            )
        )
    return events


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


async def _resolve_timeline_equity_and_pnl(
    *,
    db: AsyncSession,
    selected_validation_run: ValidationRunResponse | None,
    fallback_equity: str,
) -> tuple[str, str | None, dict[str, object]]:
    fallback_equity_decimal = _decimal_from_string(fallback_equity)

    if not hasattr(db, "execute"):
        return (
            format(fallback_equity_decimal.quantize(Decimal("0.01")), "f"),
            None,
            {
                "paper_pnl_source": "unavailable",
                "paper_pnl_status": "baseline_unresolved",
            },
        )

    if selected_validation_run is not None:
        bound = await _bound_accounts_equity_and_baseline(
            db=db,
            validation_run_id=selected_validation_run.validation_run_id,
        )
        if bound is not None:
            equity, baseline, account_count = bound
            return (
                format(equity.quantize(Decimal("0.01")), "f"),
                _format_pnl(equity=equity, baseline=baseline),
                {
                    "paper_pnl_source": "bound_paper_account",
                    "paper_pnl_status": "evidence_backed",
                    "paper_pnl_baseline": format(baseline.quantize(Decimal("0.01")), "f"),
                    "paper_pnl_bound_account_count": account_count,
                },
            )

        campaign = await _campaign_equity_and_baseline(
            db=db,
            validation_run_id=selected_validation_run.validation_run_id,
        )
        if campaign is not None:
            equity, baseline, campaign_count = campaign
            return (
                format(equity.quantize(Decimal("0.01")), "f"),
                _format_pnl(equity=equity, baseline=baseline),
                {
                    "paper_pnl_source": "campaign_opening_capital",
                    "paper_pnl_status": "evidence_backed",
                    "paper_pnl_baseline": format(baseline.quantize(Decimal("0.01")), "f"),
                    "paper_pnl_campaign_count": campaign_count,
                },
            )

    active_account = await _latest_active_account_equity_and_baseline(db=db)
    if active_account is not None:
        equity, baseline, account_id = active_account
        return (
            format(equity.quantize(Decimal("0.01")), "f"),
            _format_pnl(equity=equity, baseline=baseline),
            {
                "paper_pnl_source": "fallback_active_paper_account",
                "paper_pnl_status": "fallback_unbound",
                "paper_pnl_baseline": format(baseline.quantize(Decimal("0.01")), "f"),
                "paper_account_id": str(account_id),
            },
        )

    return (
        format(fallback_equity_decimal.quantize(Decimal("0.01")), "f"),
        None,
        {
            "paper_pnl_source": "unavailable",
            "paper_pnl_status": "baseline_unresolved",
        },
    )


async def _bound_accounts_equity_and_baseline(
    *,
    db: AsyncSession,
    validation_run_id,
) -> tuple[Decimal, Decimal, int] | None:
    account_ids = (
        (
            await db.execute(
                select(ValidationRunPaperAccount.paper_account_id)
                .where(ValidationRunPaperAccount.validation_run_id == validation_run_id)
                .order_by(ValidationRunPaperAccount.bound_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not account_ids:
        return None

    accounts = (
        (
            await db.execute(
                select(PaperAccount)
                .where(PaperAccount.id.in_(account_ids))
                .order_by(PaperAccount.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not accounts:
        return None

    baseline = Decimal("0")
    equity = Decimal("0")
    for account in accounts:
        snapshot = await build_account_snapshot(
            db=db,
            paper_account_id=account.id,
            starting_balance=account.starting_balance,
        )
        baseline += Decimal(str(account.starting_balance))
        equity += Decimal(str(snapshot.equity))
    return equity, baseline, len(accounts)


async def _campaign_equity_and_baseline(
    *,
    db: AsyncSession,
    validation_run_id,
) -> tuple[Decimal, Decimal, int] | None:
    campaigns = (
        (
            await db.execute(
                select(CapitalCampaign)
                .where(CapitalCampaign.validation_run_id == validation_run_id)
                .order_by(CapitalCampaign.created_at.asc(), CapitalCampaign.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not campaigns:
        return None

    baseline = Decimal("0")
    equity = Decimal("0")
    for campaign in campaigns:
        baseline += Decimal(str(campaign.starting_capital))
        equity += Decimal(str(campaign.current_equity))
    return equity, baseline, len(campaigns)


async def _latest_active_account_equity_and_baseline(
    *,
    db: AsyncSession,
) -> tuple[Decimal, Decimal, object] | None:
    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.desc())
        .limit(1)
    )
    if account is None:
        return None

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )
    return Decimal(str(snapshot.equity)), Decimal(str(account.starting_balance)), account.id


def _format_pnl(*, equity: Decimal, baseline: Decimal) -> str | None:
    if baseline <= 0:
        return None
    return format((equity - baseline).quantize(Decimal("0.01")), "f")


def _clamp_score(value: float | int) -> int:
    return int(round(max(0.0, min(100.0, float(value)))))