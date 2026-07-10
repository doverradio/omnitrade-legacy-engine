from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.paper import _build_bucket_timestamps, _build_equity_curve_points, _ceil_timestamp, _floor_timestamp, _load_account, get_paper_performance_summary
from app.models.decision_quality_score import DecisionQualityScore
from app.models.paper_account import PaperAccount
from app.models.research_candidate_evaluation import ResearchCandidateEvaluation
from app.models.research_campaign import ResearchCampaign
from app.models.risk_event import RiskEvent
from app.models.trade import Trade
from app.schemas.dashboard import (
    DashboardIntelligenceComponentResponse,
    DashboardIntelligenceScoreResponse,
    DashboardIntelligenceTimelinePointResponse,
)
from app.services.operations_status import build_operations_status


@dataclass(frozen=True, slots=True)
class _RangeConfig:
    window: timedelta
    interval_minutes: int


_RANGE_CONFIG: dict[str, _RangeConfig] = {
    "24h": _RangeConfig(window=timedelta(hours=24), interval_minutes=60),
    "72h": _RangeConfig(window=timedelta(hours=72), interval_minutes=60),
    "7d": _RangeConfig(window=timedelta(days=7), interval_minutes=24 * 60),
    "30d": _RangeConfig(window=timedelta(days=30), interval_minutes=24 * 60),
    "90d": _RangeConfig(window=timedelta(days=90), interval_minutes=3 * 24 * 60),
    "all": _RangeConfig(window=timedelta(days=90), interval_minutes=3 * 24 * 60),
}

_COMPONENT_WEIGHTS = {
    "Decision Outcome Quality": 30,
    "Paper Performance": 20,
    "Risk Discipline": 15,
    "Replay / Decision Quality": 15,
    "Research Improvement": 10,
    "Operational Health": 10,
}


async def build_dashboard_intelligence_score(*, db: AsyncSession, range_value: str) -> DashboardIntelligenceScoreResponse:
    normalized_range = range_value.strip().lower()
    if normalized_range not in _RANGE_CONFIG:
        normalized_range = "24h"

    config = _RANGE_CONFIG[normalized_range]
    generated_at = datetime.now(timezone.utc)
    window_start = generated_at - config.window

    account = await _load_account(db=db, account_id=None)
    current_summary = await get_paper_performance_summary(account_id=account.id, db=db)

    trades = await _load_trades(db=db, account=account, window_start=window_start)
    decision_scores = await _load_decision_scores(db=db, window_start=window_start)
    research_evaluations = await _load_research_evaluations(db=db, window_start=window_start)
    campaigns = await _load_campaigns(db=db, window_start=window_start)
    risk_events = await _load_risk_events(db=db, window_start=window_start)

    try:
        operations = await build_operations_status(db=db)
        operational_health_score, operational_health_explanation = _score_operational_health(operations=operations)
        operational_available = True
    except Exception:
        operational_health_score = 0
        operational_health_explanation = "Operational status unavailable in this environment."
        operational_available = False

    equity_curve = _build_equity_points(
        account=account,
        trades=trades,
        window_start=window_start,
        generated_at=generated_at,
        interval_minutes=config.interval_minutes,
    )

    has_observable_data = bool(trades or decision_scores or research_evaluations or campaigns or risk_events)
    if not equity_curve:
        equity_curve = [
            DashboardIntelligenceTimelinePointResponse(
                timestamp=window_start,
                score=0,
                equity=account.starting_balance,
                decision_quality=0,
                research_quality=0,
                operational_health=operational_health_score,
            ),
            DashboardIntelligenceTimelinePointResponse(
                timestamp=generated_at,
                score=0,
                equity=account.starting_balance,
                decision_quality=0,
                research_quality=0,
                operational_health=operational_health_score,
            ),
        ]

    latest_point = equity_curve[-1]
    decision_score, decision_explanation, decision_available = _score_decision_outcome(
        current_summary=current_summary,
        decision_scores=decision_scores,
        trades=trades,
    )
    paper_score, paper_explanation, paper_available = _score_paper_performance(
        equity_point=latest_point,
        starting_balance=account.starting_balance,
    )
    risk_score, risk_explanation, risk_available = _score_risk_discipline(
        risk_events=risk_events,
        equity_point=latest_point,
        starting_balance=account.starting_balance,
    )
    replay_score, replay_explanation, replay_available = _score_replay_quality(decision_scores=decision_scores)
    research_score, research_explanation, research_available = _score_research_improvement(
        evaluations=research_evaluations,
        campaigns=campaigns,
    )

    components = [
        DashboardIntelligenceComponentResponse(
            name="Decision Outcome Quality",
            score=decision_score,
            weight=_COMPONENT_WEIGHTS["Decision Outcome Quality"],
            explanation=decision_explanation,
        ),
        DashboardIntelligenceComponentResponse(
            name="Paper Performance",
            score=paper_score,
            weight=_COMPONENT_WEIGHTS["Paper Performance"],
            explanation=paper_explanation,
        ),
        DashboardIntelligenceComponentResponse(
            name="Risk Discipline",
            score=risk_score,
            weight=_COMPONENT_WEIGHTS["Risk Discipline"],
            explanation=risk_explanation,
        ),
        DashboardIntelligenceComponentResponse(
            name="Replay / Decision Quality",
            score=replay_score,
            weight=_COMPONENT_WEIGHTS["Replay / Decision Quality"],
            explanation=replay_explanation,
        ),
        DashboardIntelligenceComponentResponse(
            name="Research Improvement",
            score=research_score,
            weight=_COMPONENT_WEIGHTS["Research Improvement"],
            explanation=research_explanation,
        ),
        DashboardIntelligenceComponentResponse(
            name="Operational Health",
            score=operational_health_score,
            weight=_COMPONENT_WEIGHTS["Operational Health"],
            explanation=operational_health_explanation,
        ),
    ]

    score, completeness = _aggregate_score(
        components=components,
        availability=[decision_available, paper_available, risk_available, replay_available, research_available, operational_available],
    )

    if not has_observable_data:
        score = 0
        completeness = 0
        timeline: list[DashboardIntelligenceTimelinePointResponse] = []
        components = [
            DashboardIntelligenceComponentResponse(
                name=item.name,
                score=0,
                weight=item.weight,
                explanation="No data available in the selected range.",
            )
            for item in components
        ]
    else:
        timeline = await _build_timeline_points(
            account=account,
            trades=trades,
            decision_scores=decision_scores,
            research_evaluations=research_evaluations,
            campaigns=campaigns,
            risk_events=risk_events,
            operational_health_score=operational_health_score,
            current_summary=current_summary,
            window_start=window_start,
            generated_at=generated_at,
            interval_minutes=config.interval_minutes,
        )

    return DashboardIntelligenceScoreResponse(
        score=score,
        data_completeness=completeness,
        range=normalized_range,
        generated_at=generated_at,
        components=components,
        timeline=timeline,
    )


async def _load_trades(*, db: AsyncSession, account: PaperAccount, window_start: datetime) -> list[Trade]:
    return (
        await db.execute(
            select(Trade)
            .where(Trade.paper_account_id == account.id)
            .where(Trade.is_paper.is_(True))
            .where(Trade.executed_at >= window_start)
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()


async def _load_decision_scores(*, db: AsyncSession, window_start: datetime) -> list[DecisionQualityScore]:
    return (
        await db.execute(
            select(DecisionQualityScore)
            .where(DecisionQualityScore.created_at >= window_start)
            .order_by(DecisionQualityScore.created_at.asc(), DecisionQualityScore.id.asc())
        )
    ).scalars().all()


async def _load_research_evaluations(*, db: AsyncSession, window_start: datetime) -> list[ResearchCandidateEvaluation]:
    return (
        await db.execute(
            select(ResearchCandidateEvaluation)
            .where(ResearchCandidateEvaluation.created_at >= window_start)
            .order_by(ResearchCandidateEvaluation.created_at.asc(), ResearchCandidateEvaluation.evaluation_id.asc())
        )
    ).scalars().all()


async def _load_campaigns(*, db: AsyncSession, window_start: datetime) -> list[ResearchCampaign]:
    return (
        await db.execute(
            select(ResearchCampaign)
            .where(ResearchCampaign.created_at >= window_start)
            .order_by(ResearchCampaign.created_at.asc(), ResearchCampaign.campaign_id.asc())
        )
    ).scalars().all()


async def _load_risk_events(*, db: AsyncSession, window_start: datetime) -> list[RiskEvent]:
    return (
        await db.execute(
            select(RiskEvent)
            .where(RiskEvent.created_at >= window_start)
            .order_by(RiskEvent.created_at.asc(), RiskEvent.id.asc())
        )
    ).scalars().all()


def _build_equity_points(
    *,
    account: PaperAccount,
    trades: list[Trade],
    window_start: datetime,
    generated_at: datetime,
    interval_minutes: int,
) -> list[DashboardIntelligenceTimelinePointResponse]:
    start = _floor_timestamp(window_start, interval_minutes=interval_minutes)
    end = _ceil_timestamp(generated_at, interval_minutes=interval_minutes)
    buckets = _build_bucket_timestamps(start=start, end=end, interval_minutes=interval_minutes)
    equity_points = _build_equity_curve_points(trades=trades, starting_balance=account.starting_balance, buckets=buckets)

    return [
        DashboardIntelligenceTimelinePointResponse(
            timestamp=item.timestamp,
            score=0,
            equity=item.equity,
            decision_quality=0,
            research_quality=0,
            operational_health=0,
        )
        for item in equity_points
    ]


async def _build_timeline_points(
    *,
    account: PaperAccount,
    trades: list[Trade],
    decision_scores: list[DecisionQualityScore],
    research_evaluations: list[ResearchCandidateEvaluation],
    campaigns: list[ResearchCampaign],
    risk_events: list[RiskEvent],
    operational_health_score: int,
    current_summary,
    window_start: datetime,
    generated_at: datetime,
    interval_minutes: int,
) -> list[DashboardIntelligenceTimelinePointResponse]:
    start = _floor_timestamp(window_start, interval_minutes=interval_minutes)
    end = _ceil_timestamp(generated_at, interval_minutes=interval_minutes)
    buckets = _build_bucket_timestamps(start=start, end=end, interval_minutes=interval_minutes)
    equity_points = _build_equity_curve_points(trades=trades, starting_balance=account.starting_balance, buckets=buckets)

    if not equity_points:
        return []

    points: list[DashboardIntelligenceTimelinePointResponse] = []
    decision_index = 0
    research_index = 0
    campaign_index = 0
    risk_index = 0
    peak_equity = float(account.starting_balance)

    for equity_point in equity_points:
        while decision_index < len(decision_scores) and decision_scores[decision_index].created_at <= equity_point.timestamp:
            decision_index += 1
        while research_index < len(research_evaluations) and research_evaluations[research_index].created_at <= equity_point.timestamp:
            research_index += 1
        while campaign_index < len(campaigns) and campaigns[campaign_index].created_at <= equity_point.timestamp:
            campaign_index += 1
        while risk_index < len(risk_events) and risk_events[risk_index].created_at <= equity_point.timestamp:
            risk_index += 1

        decision_subset = decision_scores[:decision_index]
        research_subset = research_evaluations[:research_index]
        campaign_subset = campaigns[:campaign_index]
        risk_subset = risk_events[:risk_index]

        decision_score, _, decision_available = _score_decision_outcome(
            current_summary=current_summary,
            decision_scores=decision_subset,
            trades=trades,
        )
        paper_score, _, paper_available = _score_paper_performance(
            equity_point=equity_point,
            starting_balance=account.starting_balance,
            peak_equity=peak_equity,
        )
        risk_score, _, risk_available = _score_risk_discipline(
            risk_events=risk_subset,
            equity_point=equity_point,
            starting_balance=account.starting_balance,
            peak_equity=peak_equity,
        )
        replay_score, _, replay_available = _score_replay_quality(decision_scores=decision_subset)
        research_score, _, research_available = _score_research_improvement(
            evaluations=research_subset,
            campaigns=campaign_subset,
        )

        component_scores: list[tuple[int, int]] = []
        for value, weight, available in [
            (decision_score, _COMPONENT_WEIGHTS["Decision Outcome Quality"], decision_available),
            (paper_score, _COMPONENT_WEIGHTS["Paper Performance"], paper_available),
            (risk_score, _COMPONENT_WEIGHTS["Risk Discipline"], risk_available),
            (replay_score, _COMPONENT_WEIGHTS["Replay / Decision Quality"], replay_available),
            (research_score, _COMPONENT_WEIGHTS["Research Improvement"], research_available),
            (operational_health_score, _COMPONENT_WEIGHTS["Operational Health"], True),
        ]:
            if available:
                component_scores.append((value, weight))

        if component_scores:
            score = int(round(sum(value * weight for value, weight in component_scores) / sum(weight for _, weight in component_scores)))
        else:
            score = 0

        points.append(
            DashboardIntelligenceTimelinePointResponse(
                timestamp=equity_point.timestamp,
                score=score,
                equity=equity_point.equity,
                decision_quality=decision_score,
                research_quality=research_score,
                operational_health=operational_health_score,
            )
        )

        peak_equity = max(peak_equity, float(equity_point.equity))

    return points


def _aggregate_score(
    *,
    components: list[DashboardIntelligenceComponentResponse],
    availability: list[bool],
) -> tuple[int, int]:
    total_weight = sum(_COMPONENT_WEIGHTS.values())
    available_weight = 0
    weighted_total = 0

    for component, available in zip(components, availability):
        if available:
            available_weight += component.weight
            weighted_total += component.score * component.weight

    if available_weight <= 0:
        return 0, 0

    score = int(round(weighted_total / available_weight))
    completeness = int(round((available_weight / total_weight) * 100))
    return score, completeness


def _score_decision_outcome(
    *,
    current_summary,
    decision_scores: list[DecisionQualityScore],
    trades: list[Trade],
) -> tuple[int, str, bool]:
    if not trades and not decision_scores:
        return 0, "No paper trade or replay data is available for this range.", False

    values: list[float] = []
    if trades:
        trade_count = getattr(current_summary, "trade_count", len(trades))
        win_rate_raw = getattr(current_summary, "win_rate", "0")
        win_rate = float(win_rate_raw) * 100.0 if trade_count > 0 else 0.0
        values.append(win_rate)
    if decision_scores:
        values.append(mean(float(item.composite_score) for item in decision_scores))

    score = _clamp_score(mean(values))
    explanation = f"Based on {len(trades)} paper trades and {len(decision_scores)} replay quality scores."
    return score, explanation, True


def _score_paper_performance(
    *,
    equity_point: DashboardIntelligenceTimelinePointResponse,
    starting_balance: Decimal,
    peak_equity: float | None = None,
) -> tuple[int, str, bool]:
    if starting_balance <= 0:
        return 0, "Starting balance is not available.", False

    current_equity = float(equity_point.equity)
    peak = current_equity if peak_equity is None else peak_equity
    total_return_pct = (current_equity - float(starting_balance)) / float(starting_balance)
    drawdown_pct = max(0.0, (peak - current_equity) / peak) if peak > 0 else 0.0
    stability_pct = _equity_volatility_pct(current_equity=current_equity, starting_balance=starting_balance)

    return_score = _clamp_score(50.0 + total_return_pct * 500.0)
    drawdown_score = _clamp_score(100.0 - drawdown_pct * 500.0)
    stability_score = _clamp_score(100.0 - stability_pct * 500.0)
    score = _clamp_score(mean([return_score, drawdown_score, stability_score]))
    explanation = "Equity return, drawdown, and stability are derived from the selected equity window."
    return score, explanation, True


def _score_risk_discipline(
    *,
    risk_events: list[RiskEvent],
    equity_point: DashboardIntelligenceTimelinePointResponse,
    starting_balance: Decimal,
    peak_equity: float | None = None,
) -> tuple[int, str, bool]:
    if not risk_events and equity_point is None:
        return 0, "No risk activity is available for this range.", False

    rejection_rate = 0.0
    if risk_events:
        rejected = sum(1 for item in risk_events if str(item.action_taken).lower() in {"blocked", "rejected"})
        rejection_rate = rejected / len(risk_events)

    current_equity = float(equity_point.equity)
    peak = current_equity if peak_equity is None else peak_equity
    drawdown_pct = max(0.0, (peak - current_equity) / peak) if peak > 0 else 0.0

    rejection_score = _clamp_score(100.0 - rejection_rate * 100.0)
    drawdown_score = _clamp_score(100.0 - drawdown_pct * 500.0)
    score = _clamp_score(mean([rejection_score, drawdown_score]))
    explanation = f"Risk rejects and drawdown are taken from {len(risk_events)} risk events and the current equity path."
    return score, explanation, True


def _score_replay_quality(*, decision_scores: list[DecisionQualityScore]) -> tuple[int, str, bool]:
    if not decision_scores:
        return 0, "No replay/decision quality scores are available for this range.", False

    average_quality = mean(float(item.composite_score) for item in decision_scores)
    score = _clamp_score(average_quality)
    explanation = f"Average decision quality from {len(decision_scores)} replay scores."
    return score, explanation, True


def _score_research_improvement(
    *,
    evaluations: list[ResearchCandidateEvaluation],
    campaigns: list[ResearchCampaign],
) -> tuple[int, str, bool]:
    if not evaluations and not campaigns:
        return 0, "No research evaluations or campaigns are available for this range.", False

    values: list[float] = []
    if evaluations:
        values.append(mean(float(item.decision_quality_score) for item in evaluations))
        midpoint = len(evaluations) // 2
        if midpoint > 0:
            early = evaluations[:midpoint]
            late = evaluations[midpoint:]
            early_avg = mean(float(item.decision_quality_score) for item in early)
            late_avg = mean(float(item.decision_quality_score) for item in late)
            values.append(_clamp_score(50.0 + (late_avg - early_avg)))

    if campaigns:
        completed = sum(1 for item in campaigns if item.completed_at is not None or item.status.upper() == "COMPLETED")
        values.append(100.0 * completed / len(campaigns))

    score = _clamp_score(mean(values))
    explanation = f"Research improvement is based on {len(evaluations)} candidate evaluations and {len(campaigns)} campaigns."
    return score, explanation, True


def _score_operational_health(*, operations) -> tuple[int, str]:
    overall_health = str(operations.overall_health).lower()
    alerts = len(getattr(operations, "alerts", []) or [])

    if overall_health == "green":
        base = 100
    elif overall_health == "yellow":
        base = 72
    elif overall_health == "red":
        base = 35
    else:
        base = 50

    score = max(0, base - (alerts * 6))
    return score, f"Operational health is {overall_health} with {alerts} active alerts."


def _equity_volatility_pct(*, current_equity: float, starting_balance: Decimal) -> float:
    if starting_balance <= 0:
        return 0.0
    return abs(current_equity - float(starting_balance)) / float(starting_balance)


def _clamp_score(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))