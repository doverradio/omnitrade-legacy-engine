from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.schemas.ai_coach import AICoachObservationResponse, AICoachReviewRequest
from app.schemas.capital_allocation import CapitalAllocationEntryResponse, CapitalAllocationRecommendationResponse
from app.schemas.decision_intelligence import DecisionIntelligenceRecommendationResponse
from app.schemas.decision_quality import DecisionQualityEvaluationRequest, DecisionQualityResultResponse
from app.schemas.arena import StrategyArenaScoreboardItem, StrategyArenaScoreboardResponse
from app.schemas.replay_agent import ReplayRequest, ReplayResultResponse, ReplayAgentCapabilityResponse, ReplayAgentRegistrationResponse
from app.schemas.strategy_health import StrategyHealthItemResponse, StrategyHealthResponse
from app.schemas.tournament import TournamentRankingEntryResponse, TournamentResponse
from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.capital_allocation.deterministic import build_capital_allocation_recommendation_v1
from app.services.capital_allocation.interface import CapitalAllocationInput
from app.services.decision_intelligence.deterministic import StrategyEvidence, build_decision_intelligence_recommendation_v1
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.decisions.package import DecisionPackageBuilder
from app.services.replay.default_agent import ReplayPackageNotFoundError, replay_decision_package_v0
from app.services.replay.identifiers import build_decision_package_id
from app.services.replay.registry import list_registered_replay_agents
from app.services.tournament.deterministic import build_tournament_snapshot_v1, replay_variance_from_confidence
from app.services.tournament.interface import TournamentStrategyEvidence

router = APIRouter(prefix="/arena", tags=["arena"])


@router.get("/replay-agents", response_model=list[ReplayAgentRegistrationResponse])
async def get_replay_agents() -> list[ReplayAgentRegistrationResponse]:
    return [
        ReplayAgentRegistrationResponse(
            replay_agent_id=item.replay_agent_id,
            name=item.name,
            status=item.status,
            capabilities=[
                ReplayAgentCapabilityResponse(name=capability.name, description=capability.description)
                for capability in item.capabilities
            ],
            decision_package_consumer=item.decision_package_consumer,
            execution_logic=item.execution_logic,
            processing_enabled=item.processing_enabled,
            scheduling_enabled=item.scheduling_enabled,
            writes_enabled=item.writes_enabled,
        )
        for item in list_registered_replay_agents()
    ]


@router.get("/strategy-scoreboard", response_model=StrategyArenaScoreboardResponse)
async def get_strategy_scoreboard(db: AsyncSession = Depends(get_db)) -> StrategyArenaScoreboardResponse:
    strategies = (
        await db.execute(select(Strategy).order_by(Strategy.is_active.desc(), Strategy.name.asc(), Strategy.created_at.asc()))
    ).scalars().all()

    if not strategies:
        return StrategyArenaScoreboardResponse(items=[])

    strategy_ids = [strategy.id for strategy in strategies]
    signals = (
        await db.execute(
            select(Signal)
            .where(Signal.strategy_id.in_(strategy_ids))
            .order_by(Signal.strategy_id.asc(), Signal.signal_time.asc(), Signal.id.asc())
        )
    ).scalars().all()
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    decision_records = (await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.asc()))).scalars().all()
    decision_package_builder = DecisionPackageBuilder()

    signals_by_strategy: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for signal in signals:
        signals_by_strategy[signal.strategy_id].append(signal)

    signal_ids_by_strategy: dict[uuid.UUID, set[uuid.UUID]] = {
        strategy_id: {signal.id for signal in strategy_signals}
        for strategy_id, strategy_signals in signals_by_strategy.items()
    }

    latest_prices_by_asset_id = await _load_latest_prices_by_asset_id(
        db=db,
        asset_ids=sorted({trade.asset_id for trade in trades}, key=str),
    )

    items: list[StrategyArenaScoreboardItem] = []
    for strategy in strategies:
        strategy_signals = signals_by_strategy.get(strategy.id, [])
        strategy_signal_ids = signal_ids_by_strategy.get(strategy.id, set())
        strategy_trades = [trade for trade in trades if trade.signal_id in strategy_signal_ids]
        strategy_decision_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, strategy_signal_ids)
        ]
        latest_decision_package_id = await _resolve_latest_decision_package_id(
            decision_package_builder=decision_package_builder,
            db=db,
            decision_records=strategy_decision_records,
        )

        trade_snapshot = _compute_strategy_trade_snapshot(
            strategy_trades=strategy_trades,
            latest_prices_by_asset_id=latest_prices_by_asset_id,
        )

        action_counts = Counter(signal.action for signal in strategy_signals)
        items.append(
            StrategyArenaScoreboardItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                enabled=strategy.is_active,
                status="active" if strategy.is_active else "disabled",
                signals_generated=len(strategy_signals),
                buy_signals=action_counts.get("buy", 0),
                sell_signals=action_counts.get("sell", 0),
                hold_signals=action_counts.get("hold", 0),
                paper_trades=len(strategy_trades),
                open_positions=trade_snapshot["open_positions"],
                realized_pnl=trade_snapshot["realized_pnl"],
                unrealized_pnl=trade_snapshot["unrealized_pnl"],
                total_return_pct=trade_snapshot["total_return_pct"],
                decision_records=len(strategy_decision_records),
                last_signal_timestamp=max((signal.signal_time for signal in strategy_signals), default=None),
                last_trade_timestamp=max((trade.executed_at for trade in strategy_trades), default=None),
                latest_decision_package_id=latest_decision_package_id,
            )
        )

    return StrategyArenaScoreboardResponse(items=items)


@router.post("/replay", response_model=ReplayResultResponse)
async def replay_decision_package(request: ReplayRequest, db: AsyncSession = Depends(get_db)) -> ReplayResultResponse:
    try:
        replay_result = await replay_decision_package_v0(db=db, decision_package_id=request.decision_package_id)
    except ReplayPackageNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Replay package not found") from error

    return ReplayResultResponse(
        replay_id=replay_result.replay_id,
        replay_agent_id=replay_result.replay_agent_id,
        decision_package_id=replay_result.decision_package_id,
        replay_timestamp=replay_result.replay_timestamp,
        reconstructed_action=replay_result.reconstructed_action,
        reconstructed_confidence=replay_result.confidence,
        supporting_evidence=[dict(item) for item in replay_result.supporting_evidence],
        explanation=replay_result.explanation,
        metadata=replay_result.metadata,
    )


@router.post("/evaluate-replay", response_model=DecisionQualityResultResponse)
async def evaluate_replay(request: DecisionQualityEvaluationRequest) -> DecisionQualityResultResponse:
    quality_result = evaluate_replay_result_v0(replay_result=request.to_replay_result())
    return DecisionQualityResultResponse(
        quality_score=quality_result.quality_score,
        decision_reproduced=quality_result.decision_reproduced,
        action_matches_original=quality_result.action_matches_original,
        confidence_matches_original=quality_result.confidence_matches_original,
        replay_duration_ms=quality_result.replay_duration_ms,
        evaluation_timestamp=quality_result.evaluation_timestamp,
        calibration=quality_result.calibration,
        opportunity_cost=quality_result.opportunity_cost,
        drawdown=quality_result.drawdown,
        risk_adjusted_return=quality_result.risk_adjusted_return,
        explanation_quality=quality_result.explanation_quality,
    )


@router.post("/coach-review", response_model=AICoachObservationResponse)
async def coach_review(request: AICoachReviewRequest) -> AICoachObservationResponse:
    observation = evaluate_decision_quality_v0(decision_quality_result=request.to_decision_quality_result())
    return AICoachObservationResponse(
        observation_id=observation.observation_id,
        evaluation_timestamp=observation.evaluation_timestamp,
        summary=observation.summary,
        strengths=list(observation.strengths),
        weaknesses=list(observation.weaknesses),
        confidence_note=observation.confidence_note,
        reproducibility_note=observation.reproducibility_note,
        suggested_follow_up=observation.suggested_follow_up,
    )


@router.get("/decision-intelligence", response_model=DecisionIntelligenceRecommendationResponse)
async def decision_intelligence(db: AsyncSession = Depends(get_db)) -> DecisionIntelligenceRecommendationResponse:
    active_strategies = (
        await db.execute(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .order_by(Strategy.name.asc(), Strategy.created_at.asc())
        )
    ).scalars().all()

    strategy_evidence: list[StrategyEvidence] = []
    decision_package_builder = DecisionPackageBuilder()

    for strategy in active_strategies:
        decision_records = (
            await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc()))
        ).scalars().all()
        strategy_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, set())
        ]

        latest_package_id = await _resolve_latest_decision_package_id(
            decision_package_builder=decision_package_builder,
            db=db,
            decision_records=strategy_records,
        )
        if latest_package_id is None:
            continue

        try:
            replay_result = await replay_decision_package_v0(db=db, decision_package_id=latest_package_id)
        except ReplayPackageNotFoundError:
            continue

        strategy_evidence.append(
            StrategyEvidence(
                strategy_name=strategy.name,
                replay_result=replay_result,
            )
        )

    recommendation = build_decision_intelligence_recommendation_v1(strategy_evidence=strategy_evidence)
    return DecisionIntelligenceRecommendationResponse(
        recommendation_id=recommendation.recommendation_id,
        generated_at=recommendation.generated_at,
        compared_strategies=list(recommendation.compared_strategies),
        highest_quality_strategy=recommendation.highest_quality_strategy,
        evidence_summary=recommendation.evidence_summary,
        confidence_summary=recommendation.confidence_summary,
        recommendation_summary=recommendation.recommendation_summary,
        human_review_required=recommendation.human_review_required,
        promotion_recommended=recommendation.promotion_recommended,
    )


@router.get("/tournament", response_model=TournamentResponse)
async def get_tournament(db: AsyncSession = Depends(get_db)) -> TournamentResponse:
    active_strategies = (
        await db.execute(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .order_by(Strategy.name.asc(), Strategy.created_at.asc())
        )
    ).scalars().all()

    if not active_strategies:
        empty_snapshot = build_tournament_snapshot_v1(strategies=[])
        return TournamentResponse(
            tournament_id=empty_snapshot.tournament_id,
            generated_at=empty_snapshot.generated_at,
            compared_strategies=list(empty_snapshot.compared_strategies),
            ranking=[],
        )

    strategy_ids = [strategy.id for strategy in active_strategies]
    signals = (
        await db.execute(
            select(Signal)
            .where(Signal.strategy_id.in_(strategy_ids))
            .order_by(Signal.strategy_id.asc(), Signal.signal_time.asc(), Signal.id.asc())
        )
    ).scalars().all()
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    decision_records = (await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.asc()))).scalars().all()

    signals_by_strategy: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for signal in signals:
        signals_by_strategy[signal.strategy_id].append(signal)

    signal_ids_by_strategy: dict[uuid.UUID, set[uuid.UUID]] = {
        strategy_id: {signal.id for signal in strategy_signals}
        for strategy_id, strategy_signals in signals_by_strategy.items()
    }

    latest_prices_by_asset_id = await _load_latest_prices_by_asset_id(
        db=db,
        asset_ids=sorted({trade.asset_id for trade in trades}, key=str),
    )

    decision_package_builder = DecisionPackageBuilder()
    tournament_strategies: list[TournamentStrategyEvidence] = []

    for strategy in active_strategies:
        strategy_signal_ids = signal_ids_by_strategy.get(strategy.id, set())
        strategy_trades = [trade for trade in trades if trade.signal_id in strategy_signal_ids]
        strategy_decision_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, strategy_signal_ids)
        ]

        latest_decision_package_id = await _resolve_latest_decision_package_id(
            decision_package_builder=decision_package_builder,
            db=db,
            decision_records=strategy_decision_records,
        )

        quality_score = 0
        replay_variance = Decimal("999")
        replay_count = 0
        if latest_decision_package_id is not None:
            try:
                replay_result = await replay_decision_package_v0(db=db, decision_package_id=latest_decision_package_id)
                replay_count = 1
                quality = evaluate_replay_result_v0(replay_result=replay_result)
                quality_score = quality.quality_score
                replay_variance = replay_variance_from_confidence(
                    original_confidence=replay_result.metadata.get("original_confidence"),
                    reconstructed_confidence=replay_result.confidence,
                )
            except ReplayPackageNotFoundError:
                pass

        trade_snapshot = _compute_strategy_trade_snapshot(
            strategy_trades=strategy_trades,
            latest_prices_by_asset_id=latest_prices_by_asset_id,
        )

        tournament_strategies.append(
            TournamentStrategyEvidence(
                strategy_name=strategy.name,
                quality_score=quality_score,
                replay_variance=replay_variance,
                replay_count=replay_count,
                paper_trades=len(strategy_trades),
                realized_pnl=trade_snapshot["realized_pnl"],
                unrealized_pnl=trade_snapshot["unrealized_pnl"],
                win_rate=_compute_strategy_win_rate(strategy_trades=strategy_trades),
            )
        )

    snapshot = build_tournament_snapshot_v1(strategies=tournament_strategies)
    return TournamentResponse(
        tournament_id=snapshot.tournament_id,
        generated_at=snapshot.generated_at,
        compared_strategies=list(snapshot.compared_strategies),
        ranking=[
            TournamentRankingEntryResponse(
                strategy_name=item.strategy_name,
                quality_score=item.quality_score,
                replay_variance=str(item.replay_variance),
                replay_count=item.replay_count,
                paper_trades=item.paper_trades,
                realized_pnl=str(item.realized_pnl),
                unrealized_pnl=str(item.unrealized_pnl),
                win_rate=None if item.win_rate is None else str(item.win_rate),
                overall_rank=item.overall_rank,
            )
            for item in snapshot.ranking
        ],
    )


@router.get("/capital-allocation", response_model=CapitalAllocationRecommendationResponse)
async def get_capital_allocation(db: AsyncSession = Depends(get_db)) -> CapitalAllocationRecommendationResponse:
    active_strategies = (
        await db.execute(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .order_by(Strategy.name.asc(), Strategy.created_at.asc())
        )
    ).scalars().all()

    strategy_ids = [strategy.id for strategy in active_strategies]
    signals = (
        await db.execute(
            select(Signal)
            .where(Signal.strategy_id.in_(strategy_ids))
            .order_by(Signal.strategy_id.asc(), Signal.signal_time.asc(), Signal.id.asc())
        )
    ).scalars().all()
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    decision_records = (await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.asc()))).scalars().all()

    signals_by_strategy: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for signal in signals:
        signals_by_strategy[signal.strategy_id].append(signal)

    signal_ids_by_strategy: dict[uuid.UUID, set[uuid.UUID]] = {
        strategy_id: {signal.id for signal in strategy_signals}
        for strategy_id, strategy_signals in signals_by_strategy.items()
    }

    latest_prices_by_asset_id = await _load_latest_prices_by_asset_id(
        db=db,
        asset_ids=sorted({trade.asset_id for trade in trades}, key=str),
    )

    decision_package_builder = DecisionPackageBuilder()
    tournament_strategies: list[TournamentStrategyEvidence] = []
    strategy_evidence: list[StrategyEvidence] = []
    quality_scores_by_strategy: dict[str, int] = {}

    for strategy in active_strategies:
        strategy_signal_ids = signal_ids_by_strategy.get(strategy.id, set())
        strategy_trades = [trade for trade in trades if trade.signal_id in strategy_signal_ids]
        strategy_decision_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, strategy_signal_ids)
        ]

        latest_decision_package_id = await _resolve_latest_decision_package_id(
            decision_package_builder=decision_package_builder,
            db=db,
            decision_records=strategy_decision_records,
        )

        quality_score = 0
        replay_variance = Decimal("999")
        replay_count = 0
        if latest_decision_package_id is not None:
            try:
                replay_result = await replay_decision_package_v0(db=db, decision_package_id=latest_decision_package_id)
                replay_count = 1
                quality = evaluate_replay_result_v0(replay_result=replay_result)
                quality_score = quality.quality_score
                replay_variance = replay_variance_from_confidence(
                    original_confidence=replay_result.metadata.get("original_confidence"),
                    reconstructed_confidence=replay_result.confidence,
                )
                strategy_evidence.append(StrategyEvidence(strategy_name=strategy.name, replay_result=replay_result))
            except ReplayPackageNotFoundError:
                pass

        quality_scores_by_strategy[strategy.name] = quality_score

        trade_snapshot = _compute_strategy_trade_snapshot(
            strategy_trades=strategy_trades,
            latest_prices_by_asset_id=latest_prices_by_asset_id,
        )

        tournament_strategies.append(
            TournamentStrategyEvidence(
                strategy_name=strategy.name,
                quality_score=quality_score,
                replay_variance=replay_variance,
                replay_count=replay_count,
                paper_trades=len(strategy_trades),
                realized_pnl=trade_snapshot["realized_pnl"],
                unrealized_pnl=trade_snapshot["unrealized_pnl"],
                win_rate=_compute_strategy_win_rate(strategy_trades=strategy_trades),
            )
        )

    tournament_snapshot = build_tournament_snapshot_v1(strategies=tournament_strategies)
    intelligence = build_decision_intelligence_recommendation_v1(strategy_evidence=strategy_evidence)
    total_paper_capital = Decimal("100000")

    allocation = build_capital_allocation_recommendation_v1(
        tournament_ranking=[
            CapitalAllocationInput(strategy_name=item.strategy_name, overall_rank=item.overall_rank)
            for item in tournament_snapshot.ranking
        ],
        highest_quality_strategy=intelligence.highest_quality_strategy,
        quality_scores_by_strategy=quality_scores_by_strategy,
        total_paper_capital=total_paper_capital,
    )

    return CapitalAllocationRecommendationResponse(
        recommendation_id=allocation.recommendation_id,
        generated_at=allocation.generated_at,
        total_paper_capital=str(allocation.total_paper_capital),
        allocations=[
            CapitalAllocationEntryResponse(
                strategy_name=item.strategy_name,
                allocation_percent=str(item.allocation_percent),
                allocation_amount=str(item.allocation_amount),
                rationale=item.rationale,
            )
            for item in allocation.allocations
        ],
    )


@router.get("/strategy-health", response_model=StrategyHealthResponse)
async def get_strategy_health(db: AsyncSession = Depends(get_db)) -> StrategyHealthResponse:
    strategies = (
        await db.execute(select(Strategy).order_by(Strategy.is_active.desc(), Strategy.name.asc(), Strategy.created_at.asc()))
    ).scalars().all()
    if not strategies:
        return StrategyHealthResponse(items=[])

    strategy_ids = [strategy.id for strategy in strategies]
    signals = (
        await db.execute(
            select(Signal)
            .where(Signal.strategy_id.in_(strategy_ids))
            .order_by(Signal.signal_time.asc(), Signal.id.asc())
        )
    ).scalars().all()
    signal_ids_by_strategy: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    signals_by_strategy: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for signal in signals:
        signal_ids_by_strategy[signal.strategy_id].add(signal.id)
        signals_by_strategy[signal.strategy_id].append(signal)

    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    decision_records = (await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.asc()))).scalars().all()

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    items: list[StrategyHealthItemResponse] = []
    for strategy in strategies:
        strategy_signal_ids = signal_ids_by_strategy.get(strategy.id, set())
        strategy_signals = signals_by_strategy.get(strategy.id, [])
        strategy_trades = [trade for trade in trades if trade.signal_id in strategy_signal_ids]
        strategy_decision_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, strategy_signal_ids)
        ]

        signals_today = sum(
            1
            for signal in strategy_signals
            if (signal.signal_time.tzinfo is not None and signal.signal_time.astimezone(timezone.utc) >= today_start)
            or (signal.signal_time.tzinfo is None and signal.signal_time >= today_start.replace(tzinfo=None))
        )
        decision_records_today = sum(
            1
            for record in strategy_decision_records
            if (record.timestamp.tzinfo is not None and record.timestamp.astimezone(timezone.utc) >= today_start)
            or (record.timestamp.tzinfo is None and record.timestamp >= today_start.replace(tzinfo=None))
        )

        if not strategy.is_active:
            status = "disabled"
        elif signals_today == 0 and decision_records_today == 0:
            status = "idle"
        else:
            status = "active"

        last_signal_time = max((signal.signal_time for signal in strategy_signals), default=None)
        last_trade_time = max((trade.executed_at for trade in strategy_trades), default=None)

        items.append(
            StrategyHealthItemResponse(
                strategy_name=strategy.name,
                enabled=strategy.is_active,
                last_signal_time=last_signal_time,
                last_trade_time=last_trade_time,
                signals_today=signals_today,
                decision_records_today=decision_records_today,
                status=status,
            )
        )

    return StrategyHealthResponse(items=items)


async def _load_latest_prices_by_asset_id(*, db: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal] = {}

    for asset_id in asset_ids:
        latest_close = await db.scalar(
            select(Candle.close)
            .where(Candle.asset_id == asset_id)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if isinstance(latest_close, Decimal):
            latest_prices_by_asset_id[asset_id] = latest_close

    return latest_prices_by_asset_id


def _compute_strategy_trade_snapshot(
    *,
    strategy_trades: list[Trade],
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal],
) -> dict[str, Decimal | int]:
    positions: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    realized_pnl = Decimal("0")
    deployed_capital = Decimal("0")

    for trade in sorted(strategy_trades, key=lambda item: (item.executed_at, item.id)):
        quantity = Decimal(str(trade.quantity))
        price = Decimal(str(trade.price))
        fee = Decimal(str(trade.fee))
        current_qty, current_avg = positions.get(trade.asset_id, (Decimal("0"), Decimal("0")))

        if trade.side == "buy":
            total_cost = (current_qty * current_avg) + (quantity * price) + fee
            next_qty = current_qty + quantity
            next_avg = total_cost / next_qty if next_qty > 0 else Decimal("0")
            positions[trade.asset_id] = (next_qty, next_avg)
            deployed_capital += (quantity * price) + fee
            continue

        if trade.side == "sell":
            sell_qty = min(current_qty, quantity)
            realized_pnl += (sell_qty * price) - (sell_qty * current_avg) - fee
            remaining_qty = current_qty - sell_qty
            if remaining_qty <= 0:
                positions[trade.asset_id] = (Decimal("0"), Decimal("0"))
            else:
                positions[trade.asset_id] = (remaining_qty, current_avg)

    unrealized_pnl = Decimal("0")
    open_positions = 0
    for asset_id, (quantity, avg_entry_price) in positions.items():
        if quantity <= 0:
            continue
        open_positions += 1
        mark_price = latest_prices_by_asset_id.get(asset_id, avg_entry_price)
        unrealized_pnl += (mark_price - avg_entry_price) * quantity

    equity = realized_pnl + unrealized_pnl
    total_return_pct = Decimal("0")
    if deployed_capital > 0:
        total_return_pct = equity / deployed_capital

    return {
        "open_positions": open_positions,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_return_pct": total_return_pct,
    }


def _compute_strategy_win_rate(*, strategy_trades: list[Trade]) -> Decimal | None:
    positions: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    closed_count = 0
    wins = 0

    for trade in sorted(strategy_trades, key=lambda item: (item.executed_at, item.id)):
        quantity = Decimal(str(trade.quantity))
        price = Decimal(str(trade.price))
        fee = Decimal(str(trade.fee))
        current_qty, current_avg = positions.get(trade.asset_id, (Decimal("0"), Decimal("0")))

        if trade.side == "buy":
            total_cost = (current_qty * current_avg) + (quantity * price) + fee
            next_qty = current_qty + quantity
            next_avg = total_cost / next_qty if next_qty > 0 else Decimal("0")
            positions[trade.asset_id] = (next_qty, next_avg)
            continue

        if trade.side == "sell" and current_qty > 0:
            sell_qty = min(current_qty, quantity)
            pnl = (sell_qty * price) - (sell_qty * current_avg) - fee
            closed_count += 1
            if pnl > 0:
                wins += 1

            remaining_qty = current_qty - sell_qty
            positions[trade.asset_id] = (remaining_qty if remaining_qty > 0 else Decimal("0"), current_avg)

    if closed_count == 0:
        return None

    return Decimal(wins) / Decimal(closed_count)


def _decision_record_matches_strategy(
    decision_record: DecisionRecord,
    strategy: Strategy,
    strategy_signal_ids: set[uuid.UUID],
) -> bool:
    strategy_identifiers = {
        str(strategy.id),
        strategy.name,
        strategy.slug,
    }
    strategy_signal_id_strings = {str(value) for value in strategy_signal_ids}

    for item in decision_record.generated_signals or []:
        signal_id = item.get("signal_id")
        if signal_id and signal_id in strategy_signal_id_strings:
            return True

    for item in decision_record.supporting_strategies or []:
        if _dict_matches_strategy_identifiers(item, strategy_identifiers):
            return True

    for item in decision_record.opposing_strategies or []:
        if _dict_matches_strategy_identifiers(item, strategy_identifiers):
            return True

    return False


def _dict_matches_strategy_identifiers(value: dict[str, Any], strategy_identifiers: set[str]) -> bool:
    candidate_values = [
        value.get("strategy_id"),
        value.get("strategyId"),
        value.get("id"),
        value.get("strategy_name"),
        value.get("name"),
        value.get("slug"),
    ]
    return any(str(candidate).strip() in strategy_identifiers for candidate in candidate_values if candidate is not None)


async def _resolve_latest_decision_package_id(
    *,
    decision_package_builder: DecisionPackageBuilder,
    db: AsyncSession,
    decision_records: list[DecisionRecord],
) -> str | None:
    for decision_record in sorted(decision_records, key=lambda item: (item.timestamp, item.decision_id), reverse=True):
        package = await decision_package_builder.build_decision_package(db=db, decision_id=decision_record.decision_id)
        if package is None:
            continue
        return build_decision_package_id(
            decision_id=package.decision_id,
            package_hash=package.content_hash,
            package_version=package.schema_version,
        )

    return None
