from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.schemas.paper import (
    CreatePaperAccountRequest,
    CreatePaperAccountResponse,
    ExecuteSignalRequest,
    ExecuteSignalResponse,
    PaperAssetPerformanceSummary,
    PaperEquityCurvePoint,
    PaperEquityCurveResponse,
    PaperLatestTradeSummary,
    PaperPerformanceSummaryResponse,
    PaperPipelineHealthResponse,
    PaperTradeHistoryItem,
    PaperTradeHistoryResponse,
    PaperStrategyPerformanceSummary,
    StrategyPipelineMetricsItem,
    PipelineActivityItem,
    PaperTradeListResponse,
    PaperTradeResponse,
    PaperAccountResponse,
    PositionResponse,
    ResetPaperAccountRequest,
    ResetPaperAccountResponse,
)
from app.services.paper.accounting import build_account_snapshot
from app.services.signals.execution_orchestrator import (
    SignalExecutionRequest,
    orchestrate_paper_signal_execution,
)

router = APIRouter(prefix="/paper", tags=["paper"])
logger = logging.getLogger(__name__)

_DEFAULT_OWNER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200
_DEFAULT_PIPELINE_WINDOW_MINUTES = 120
_MAX_PIPELINE_WINDOW_MINUTES = 1440
_DEFAULT_EQUITY_WINDOW_MINUTES = 720
_DEFAULT_EQUITY_INTERVAL_MINUTES = 15
_MAX_EQUITY_INTERVAL_MINUTES = 240


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaperAccountResponse:
    account = await _load_account(db=db, account_id=account_id)

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )

    return PaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=snapshot.cash_balance,
        equity=snapshot.equity,
        equity_return_usd=snapshot.equity_return_usd,
        equity_return_pct=snapshot.equity_return_pct,
        positions=[
            PositionResponse(
                asset_id=position.asset_id,
                symbol=position.symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                unrealized_pnl_usd=position.unrealized_pnl_usd,
                unrealized_pnl_pct=position.unrealized_pnl_pct,
            )
            for position in snapshot.positions
        ],
    )


@router.post("/account", response_model=CreatePaperAccountResponse, status_code=201)
async def create_paper_account(
    payload: CreatePaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> CreatePaperAccountResponse:
    if payload.asset_class not in {"crypto", "stock"}:
        raise InvalidRequestError(message="Invalid asset_class", details={"asset_class": payload.asset_class})

    if payload.starting_balance < Decimal("25"):
        raise InvalidRequestError(
            message="starting_balance must be at least 25",
            details={"starting_balance": format(payload.starting_balance, "f")},
        )

    account = PaperAccount(
        owner_user_id=_DEFAULT_OWNER_USER_ID,
        name=payload.name.strip(),
        asset_class=payload.asset_class,
        starting_balance=payload.starting_balance,
        current_cash_balance=payload.starting_balance,
        is_active=True,
    )

    db.add(account)
    if hasattr(db, "flush"):
        await db.flush()

    # Bootstrap account-level kill switch state so execution-time risk checks never see unknown state.
    account_kill_switch = RiskKillSwitch(
        scope="account",
        paper_account_id=account.id,
        engaged=False,
        rearm_required=False,
        changed_by="system_bootstrap",
        reason="account_bootstrap_default",
    )
    db.add(account_kill_switch)

    audit = AuditLog(
        actor="system",
        action="paper_account_created",
        entity_type="paper_account",
        entity_id=account.id,
        before_state=None,
        after_state={
            "name": account.name,
            "asset_class": account.asset_class,
            "starting_balance": format(account.starting_balance, "f"),
            "current_cash_balance": format(account.current_cash_balance, "f"),
            "is_active": account.is_active,
        },
    )
    db.add(audit)
    await db.commit()
    if hasattr(db, "refresh"):
        await db.refresh(account)

    return CreatePaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=account.current_cash_balance,
        is_active=account.is_active,
    )


@router.post("/reset", response_model=ResetPaperAccountResponse)
async def reset_paper_account(
    payload: ResetPaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> ResetPaperAccountResponse:
    if payload.confirm is not True:
        raise InvalidRequestError(message="Reset requires confirm=true", details={"confirm": payload.confirm})

    account = await _load_account(db=db, account_id=payload.account_id)

    existing_trade_ids = (
        await db.execute(select(Trade.id).where(Trade.paper_account_id == account.id).where(Trade.is_paper.is_(True)))
    ).scalars().all()
    prior_cash_balance = account.current_cash_balance

    await db.execute(delete(Trade).where(Trade.paper_account_id == account.id))
    account.current_cash_balance = account.starting_balance

    audit = AuditLog(
        actor="system",
        action="paper_account_reset",
        entity_type="paper_account",
        entity_id=account.id,
        before_state={
            "current_cash_balance": format(prior_cash_balance, "f"),
            "trade_count": len(existing_trade_ids),
        },
        after_state={
            "current_cash_balance": format(account.current_cash_balance, "f"),
            "trade_count": 0,
        },
    )
    db.add(audit)
    await db.commit()

    return ResetPaperAccountResponse(
        account_id=account.id,
        current_cash_balance=account.current_cash_balance,
        positions=[],
    )


@router.post("/signals/execute", response_model=ExecuteSignalResponse)
async def execute_signal_paper_only(
    payload: ExecuteSignalRequest,
    db: AsyncSession = Depends(get_db),
) -> ExecuteSignalResponse:
    result = await orchestrate_paper_signal_execution(
        db=db,
        request=SignalExecutionRequest(
            signal_id=payload.signal_id,
            paper_account_id=payload.account_id,
            asset_id=payload.asset_id,
            side=payload.side,
            quantity=payload.quantity,
            actor=payload.actor,
            client_order_id=payload.client_order_id,
        ),
    )

    return ExecuteSignalResponse(
        signal_id=result.signal_id,
        account_id=result.paper_account_id,
        asset_id=result.asset_id,
        execution_status=result.execution_status,
        execution_venue=result.execution_venue,
        is_paper=result.is_paper,
        trade_id=result.trade_id,
        broker_order_id=result.broker_order_id,
        venue_status=result.venue_status,
        message=result.message,
    )


@router.get("/trades", response_model=PaperTradeListResponse)
async def get_paper_trades(
    account_id: uuid.UUID = Query(...),
    strategy_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaperTradeListResponse:
    if start_time and end_time and start_time >= end_time:
        raise InvalidRequestError(
            message="Invalid trade history time range",
            details={"start_time": start_time.isoformat(), "end_time": end_time.isoformat()},
        )

    await _load_account(db=db, account_id=account_id)

    query = (
        select(Trade, Asset.symbol)
        .outerjoin(Asset, Asset.id == Trade.asset_id)
        .where(Trade.paper_account_id == account_id)
        .where(Trade.is_paper.is_(True))
    )

    if asset_id is not None:
        query = query.where(Trade.asset_id == asset_id)

    if start_time is not None:
        query = query.where(Trade.executed_at >= start_time)

    if end_time is not None:
        query = query.where(Trade.executed_at <= end_time)

    if strategy_id is not None:
        logger.warning("strategy_id trade filter is currently ignored: no signal model is available")

    if cursor:
        cursor_time, cursor_trade_id = _parse_trade_cursor(cursor)
        query = query.where(
            or_(
                Trade.executed_at < cursor_time,
                and_(Trade.executed_at == cursor_time, Trade.id < cursor_trade_id),
            )
        )

    query = query.order_by(Trade.executed_at.desc(), Trade.id.desc()).limit(limit + 1)

    rows = (await db.execute(query)).all()
    has_next = len(rows) > limit
    visible_rows = rows[:limit]

    items: list[PaperTradeResponse] = []
    for trade, symbol in visible_rows:
        items.append(
            PaperTradeResponse(
                id=trade.id,
                asset_id=trade.asset_id,
                side=trade.side,
                quantity=trade.quantity,
                price=trade.price,
                fee=trade.fee,
                executed_at=trade.executed_at,
                signal_id=trade.signal_id,
                strategy_id=None,
                symbol=symbol,
            )
        )

    next_cursor = None
    if has_next and items:
        last = items[-1]
        next_cursor = f"{last.executed_at.isoformat()}|{last.id}"

    return PaperTradeListResponse(items=items, next_cursor=next_cursor)


@router.get("/pipeline-health", response_model=PaperPipelineHealthResponse)
async def get_paper_pipeline_health(
    window_minutes: int = Query(default=_DEFAULT_PIPELINE_WINDOW_MINUTES, ge=1, le=_MAX_PIPELINE_WINDOW_MINUTES),
    db: AsyncSession = Depends(get_db),
) -> PaperPipelineHealthResponse:
    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    candles = await _count_rows_since(db=db, statement=select(func.count()).select_from(Candle).where(Candle.created_at >= window_start))
    signals_created = await _count_rows_since(
        db=db,
        statement=select(func.count()).select_from(Signal).where(Signal.created_at >= window_start),
    )
    hold_signals = await _count_rows_since(
        db=db,
        statement=(
            select(func.count())
            .select_from(Signal)
            .where(Signal.created_at >= window_start)
            .where(Signal.action == "hold")
        ),
    )
    buy_sell_signals = await _count_rows_since(
        db=db,
        statement=(
            select(func.count())
            .select_from(Signal)
            .where(Signal.created_at >= window_start)
            .where(Signal.action.in_(["buy", "sell"]))
        ),
    )

    execution_candidates = buy_sell_signals

    executions_attempted = await _count_rows_since(
        db=db,
        statement=(
            select(func.count(func.distinct(AuditLog.entity_id)))
            .select_from(AuditLog)
            .where(AuditLog.entity_type == "signal")
            .where(
                AuditLog.action.in_(
                    [
                        "signal_execution_orchestrated",
                        "signal_execution_rejected_by_risk",
                        "signal_execution_duplicate_skipped",
                        "signal_execution_failed",
                    ]
                )
            )
            .where(AuditLog.created_at >= window_start)
        ),
    )

    risk_events = await _count_rows_since(
        db=db,
        statement=select(func.count()).select_from(RiskEvent).where(RiskEvent.created_at >= window_start),
    )
    risk_rejected = await _count_rows_since(
        db=db,
        statement=(
            select(func.count())
            .select_from(RiskEvent)
            .where(RiskEvent.created_at >= window_start)
            .where(RiskEvent.action_taken == "blocked")
        ),
    )
    trades = await _count_rows_since(
        db=db,
        statement=(
            select(func.count())
            .select_from(Trade)
            .where(Trade.created_at >= window_start)
            .where(Trade.is_paper.is_(True))
        ),
    )
    decision_records = await _count_rows_since(
        db=db,
        statement=(
            select(func.count())
            .select_from(DecisionRecord)
            .where(DecisionRecord.timestamp >= window_start)
        ),
    )

    latest_rejection_reason = await db.scalar(
        select(RiskEvent.detail["reason_code"].astext)
        .where(RiskEvent.created_at >= window_start)
        .where(RiskEvent.action_taken == "blocked")
        .order_by(RiskEvent.created_at.desc())
        .limit(1)
    )

    latest_signal_rows = (
        await db.execute(
            select(Signal.id, Signal.action, Signal.status, Signal.created_at)
            .where(Signal.created_at >= window_start)
            .order_by(Signal.created_at.desc())
            .limit(5)
        )
    ).all()

    recent_activity: list[PipelineActivityItem] = []
    for signal_id, action, status, created_at in latest_signal_rows:
        reason = await db.scalar(
            select(RiskEvent.detail["reason_code"].astext)
            .where(RiskEvent.related_signal_id == signal_id)
            .order_by(RiskEvent.created_at.desc())
            .limit(1)
        )
        recent_activity.append(
            PipelineActivityItem(
                signal_id=signal_id,
                action=action,
                status=status,
                reason=reason,
                created_at=created_at,
            )
        )

    latest_updated_at = await _resolve_latest_update_timestamp(db=db, window_start=window_start)

    active_strategies = (
        await db.execute(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .order_by(Strategy.name.asc(), Strategy.created_at.asc())
        )
    ).scalars().all()
    strategy_ids = [strategy.id for strategy in active_strategies]

    strategy_signals = (
        await db.execute(
            select(Signal.id, Signal.strategy_id)
            .where(Signal.strategy_id.in_(strategy_ids))
            .where(Signal.created_at >= window_start)
            .order_by(Signal.created_at.desc(), Signal.id.desc())
        )
    ).all() if strategy_ids else []
    signal_count_by_strategy: dict[uuid.UUID, int] = defaultdict(int)
    signal_to_strategy: dict[uuid.UUID, uuid.UUID] = {}
    for signal_id, strategy_id in strategy_signals:
        signal_count_by_strategy[strategy_id] += 1
        signal_to_strategy[signal_id] = strategy_id

    strategy_trades = (
        await db.execute(
            select(Trade.signal_id)
            .where(Trade.created_at >= window_start)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.created_at.desc(), Trade.id.desc())
        )
    ).all()
    missing_trade_signal_ids = sorted(
        {
            signal_id
            for (signal_id,) in strategy_trades
            if signal_id is not None and signal_id not in signal_to_strategy
        },
        key=str,
    )
    if missing_trade_signal_ids:
        lookup_rows = (
            await db.execute(
                select(Signal.id, Signal.strategy_id)
                .where(Signal.id.in_(missing_trade_signal_ids))
            )
        ).all()
        for signal_id, strategy_id in lookup_rows:
            signal_to_strategy[signal_id] = strategy_id

    trade_count_by_strategy: dict[uuid.UUID, int] = defaultdict(int)
    for (signal_id,) in strategy_trades:
        if signal_id is None:
            continue
        strategy_id = signal_to_strategy.get(signal_id)
        if strategy_id is None:
            continue
        trade_count_by_strategy[strategy_id] += 1

    decision_records_window = (
        await db.execute(
            select(DecisionRecord)
            .where(DecisionRecord.timestamp >= window_start)
            .order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
        )
    ).scalars().all()
    decision_count_by_strategy: dict[uuid.UUID, int] = defaultdict(int)
    for record in decision_records_window:
        signal_ids = [item.get("signal_id") for item in (record.generated_signals or []) if isinstance(item, dict)]
        matched_strategy_ids = {
            signal_to_strategy[uuid.UUID(signal_id)]
            for signal_id in signal_ids
            if isinstance(signal_id, str)
            and _is_uuid(signal_id)
            and uuid.UUID(signal_id) in signal_to_strategy
        }
        for strategy_id in matched_strategy_ids:
            decision_count_by_strategy[strategy_id] += 1

    strategy_metrics = [
        StrategyPipelineMetricsItem(
            strategy_name=strategy.name,
            signals=signal_count_by_strategy.get(strategy.id, 0),
            trades=trade_count_by_strategy.get(strategy.id, 0),
            decision_records=decision_count_by_strategy.get(strategy.id, 0),
        )
        for strategy in active_strategies
    ]

    return PaperPipelineHealthResponse(
        window_minutes=window_minutes,
        candles=candles,
        signals_created=signals_created,
        hold_signals=hold_signals,
        buy_sell_signals=buy_sell_signals,
        execution_candidates=execution_candidates,
        executions_attempted=executions_attempted,
        risk_events=risk_events,
        risk_rejected=risk_rejected,
        trades=trades,
        decision_records=decision_records,
        latest_rejection_reason=latest_rejection_reason,
        latest_updated_at=latest_updated_at,
        recent_activity=recent_activity,
        strategy_metrics=strategy_metrics,
    )


@router.get("/trade-history", response_model=PaperTradeHistoryResponse)
async def get_paper_trade_history(
    account_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaperTradeHistoryResponse:
    account_filter_id = account_id
    if account_filter_id is not None:
        account = await _load_account(db=db, account_id=account_filter_id)
        account_filter_id = account.id

    filters = [Trade.is_paper.is_(True)]
    if account_filter_id is not None:
        filters.append(Trade.paper_account_id == account_filter_id)

    total = await _count_rows_since(
        db=db,
        statement=select(func.count()).select_from(Trade).where(*filters),
    )

    page_rows = (
        await db.execute(
            select(Trade, Asset.symbol)
            .outerjoin(Asset, Asset.id == Trade.asset_id)
            .where(*filters)
            .order_by(Trade.executed_at.desc(), Trade.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    all_trades = (
        await db.execute(
            select(Trade)
            .where(*filters)
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    realized_pnl_by_trade_id = _compute_realized_pnl_by_trade(trades=all_trades)

    page_signal_ids = sorted(
        {trade.signal_id for trade, _ in page_rows if trade.signal_id is not None},
        key=str,
    )
    strategy_by_signal_id: dict[uuid.UUID, uuid.UUID] = {}
    if page_signal_ids:
        strategy_rows = (
            await db.execute(select(Signal.id, Signal.strategy_id).where(Signal.id.in_(page_signal_ids)))
        ).all()
        strategy_by_signal_id = {signal_id: strategy_id for signal_id, strategy_id in strategy_rows}

    decision_record_by_signal_id = await _resolve_decision_records_for_signals(
        db=db,
        signal_ids=page_signal_ids,
    )

    items = [
        PaperTradeHistoryItem(
            trade_id=trade.id,
            executed_at=trade.executed_at,
            asset=symbol,
            side=trade.side,
            quantity=trade.quantity,
            execution_price=trade.price,
            notional=Decimal(str(trade.quantity)) * Decimal(str(trade.price)),
            signal_id=trade.signal_id,
            strategy_id=(strategy_by_signal_id.get(trade.signal_id) if trade.signal_id is not None else None),
            decision_record_id=(
                decision_record_by_signal_id.get(trade.signal_id)
                if trade.signal_id is not None
                else None
            ),
            realized_pnl=realized_pnl_by_trade_id.get(trade.id),
            paper_account_id=trade.paper_account_id,
        )
        for trade, symbol in page_rows
    ]

    return PaperTradeHistoryResponse(
        items=items,
        limit=limit,
        offset=offset,
        total=total,
        has_more=offset + len(items) < total,
    )


@router.get("/performance-summary", response_model=PaperPerformanceSummaryResponse)
async def get_paper_performance_summary(
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaperPerformanceSummaryResponse:
    account = await _load_account(db=db, account_id=account_id)

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )

    trade_rows = (
        await db.execute(
            select(Trade, Asset.symbol)
            .outerjoin(Asset, Asset.id == Trade.asset_id)
            .where(Trade.paper_account_id == account.id)
            .where(Trade.is_paper.is_(True))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).all()

    trades = [row[0] for row in trade_rows]
    symbol_by_trade_id = {row[0].id: row[1] for row in trade_rows}

    strategy_by_signal_id: dict[uuid.UUID, uuid.UUID] = {}
    signal_ids = sorted({trade.signal_id for trade in trades if trade.signal_id is not None}, key=str)
    if signal_ids:
        strategy_rows = (
            await db.execute(select(Signal.id, Signal.strategy_id).where(Signal.id.in_(signal_ids)))
        ).all()
        strategy_by_signal_id = {signal_id: strategy_id for signal_id, strategy_id in strategy_rows}

    realized_pnl, win_count, loss_count, realized_by_asset, realized_by_strategy, wins_by_strategy, losses_by_strategy = (
        _compute_realized_performance(trades=trades, strategy_by_signal_id=strategy_by_signal_id)
    )
    unrealized_pnl = sum((position.unrealized_pnl_usd for position in snapshot.positions), Decimal("0"))
    total_return_usd = snapshot.equity_return_usd
    total_return_pct = snapshot.equity_return_pct
    trade_count = len(trades)
    win_rate = Decimal("0")
    if trade_count > 0:
        win_rate = Decimal(win_count) / Decimal(trade_count)

    latest_trade = None
    if trades:
        latest = max(trades, key=lambda trade: (trade.executed_at, trade.id))
        latest_trade = PaperLatestTradeSummary(
            id=latest.id,
            asset_id=latest.asset_id,
            symbol=symbol_by_trade_id.get(latest.id),
            strategy_id=strategy_by_signal_id.get(latest.signal_id) if latest.signal_id is not None else None,
            side=latest.side,
            quantity=latest.quantity,
            price=latest.price,
            fee=latest.fee,
            executed_at=latest.executed_at,
        )

    unrealized_by_asset = {position.asset_id: position.unrealized_pnl_usd for position in snapshot.positions}
    symbols_by_asset = {position.asset_id: position.symbol for position in snapshot.positions}
    for trade in trades:
        if trade.asset_id not in symbols_by_asset:
            symbol = symbol_by_trade_id.get(trade.id)
            if isinstance(symbol, str):
                symbols_by_asset[trade.asset_id] = symbol

    by_asset_ids = sorted(set(realized_by_asset.keys()) | set(unrealized_by_asset.keys()), key=str)
    by_asset = [
        PaperAssetPerformanceSummary(
            asset_id=asset_id,
            symbol=symbols_by_asset.get(asset_id),
            trade_count=sum(1 for trade in trades if trade.asset_id == asset_id),
            realized_pnl=realized_by_asset.get(asset_id, Decimal("0")),
            unrealized_pnl=unrealized_by_asset.get(asset_id, Decimal("0")),
            total_pnl=realized_by_asset.get(asset_id, Decimal("0")) + unrealized_by_asset.get(asset_id, Decimal("0")),
        )
        for asset_id in by_asset_ids
    ]

    by_strategy_ids = sorted(realized_by_strategy.keys(), key=str)
    by_strategy = [
        PaperStrategyPerformanceSummary(
            strategy_id=strategy_id,
            trade_count=wins_by_strategy.get(strategy_id, 0) + losses_by_strategy.get(strategy_id, 0),
            win_count=wins_by_strategy.get(strategy_id, 0),
            loss_count=losses_by_strategy.get(strategy_id, 0),
            win_rate=(
                Decimal(wins_by_strategy.get(strategy_id, 0))
                / Decimal(wins_by_strategy.get(strategy_id, 0) + losses_by_strategy.get(strategy_id, 0))
                if (wins_by_strategy.get(strategy_id, 0) + losses_by_strategy.get(strategy_id, 0)) > 0
                else Decimal("0")
            ),
            realized_pnl=realized_by_strategy.get(strategy_id, Decimal("0")),
        )
        for strategy_id in by_strategy_ids
    ]

    return PaperPerformanceSummaryResponse(
        account_id=account.id,
        starting_balance=account.starting_balance,
        current_cash_balance=snapshot.cash_balance,
        equity=snapshot.equity,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_return_usd=total_return_usd,
        total_return_pct=total_return_pct,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        latest_trade=latest_trade,
        positions=[
            PositionResponse(
                asset_id=position.asset_id,
                symbol=position.symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                unrealized_pnl_usd=position.unrealized_pnl_usd,
                unrealized_pnl_pct=position.unrealized_pnl_pct,
            )
            for position in snapshot.positions
        ],
        by_asset=by_asset,
        by_strategy=by_strategy,
    )


@router.get("/equity-curve", response_model=PaperEquityCurveResponse)
async def get_paper_equity_curve(
    account_id: uuid.UUID | None = Query(default=None),
    window_minutes: int = Query(default=_DEFAULT_EQUITY_WINDOW_MINUTES, ge=1, le=_MAX_PIPELINE_WINDOW_MINUTES),
    interval: int = Query(default=_DEFAULT_EQUITY_INTERVAL_MINUTES, ge=1, le=_MAX_EQUITY_INTERVAL_MINUTES),
    db: AsyncSession = Depends(get_db),
) -> PaperEquityCurveResponse:
    account = await _load_account(db=db, account_id=account_id)

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.paper_account_id == account.id)
            .where(Trade.is_paper.is_(True))
            .where(Trade.executed_at >= window_start)
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()

    if not trades:
        points = [
            PaperEquityCurvePoint(
                timestamp=window_start,
                equity=account.starting_balance,
                cash_balance=account.starting_balance,
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                trade_count_at_point=0,
            ),
            PaperEquityCurvePoint(
                timestamp=now,
                equity=account.starting_balance,
                cash_balance=account.starting_balance,
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                trade_count_at_point=0,
            ),
        ]
        return PaperEquityCurveResponse(
            account_id=account.id,
            window_minutes=window_minutes,
            interval=interval,
            starting_balance=account.starting_balance,
            current_equity=account.starting_balance,
            total_return_usd=Decimal("0"),
            total_return_pct=Decimal("0"),
            latest_point_timestamp=now,
            points=points,
        )

    bucket_start = _floor_timestamp(window_start, interval_minutes=interval)
    bucket_end = _ceil_timestamp(now, interval_minutes=interval)
    buckets = _build_bucket_timestamps(start=bucket_start, end=bucket_end, interval_minutes=interval)

    points = _build_equity_curve_points(
        trades=trades,
        starting_balance=account.starting_balance,
        buckets=buckets,
    )

    latest_equity = points[-1].equity if points else account.starting_balance
    total_return_usd = latest_equity - account.starting_balance
    total_return_pct = Decimal("0")
    if account.starting_balance > 0:
        total_return_pct = total_return_usd / account.starting_balance

    return PaperEquityCurveResponse(
        account_id=account.id,
        window_minutes=window_minutes,
        interval=interval,
        starting_balance=account.starting_balance,
        current_equity=latest_equity,
        total_return_usd=total_return_usd,
        total_return_pct=total_return_pct,
        latest_point_timestamp=points[-1].timestamp if points else None,
        points=points,
    )


async def _load_account(*, db: AsyncSession, account_id: uuid.UUID | None) -> PaperAccount:
    if account_id is not None:
        account = await db.scalar(select(PaperAccount).where(PaperAccount.id == account_id))
        if account is None:
            raise NotFoundError(message="Paper account not found", details={"account_id": str(account_id)})
        return account

    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.desc())
        .limit(1)
    )
    if account is None:
        raise NotFoundError(message="Paper account not found", details={})

    return account


def _parse_trade_cursor(raw_cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        timestamp_raw, trade_id_raw = raw_cursor.rsplit("|", 1)
        return datetime.fromisoformat(timestamp_raw), uuid.UUID(trade_id_raw)
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError(message="Invalid trade cursor", details={"cursor": raw_cursor}) from exc


async def _count_rows_since(*, db: AsyncSession, statement) -> int:
    value = await db.scalar(statement)
    return int(value or 0)


async def _resolve_latest_update_timestamp(*, db: AsyncSession, window_start: datetime) -> datetime | None:
    latest_values = [
        await db.scalar(select(func.max(Candle.created_at)).where(Candle.created_at >= window_start)),
        await db.scalar(select(func.max(Signal.created_at)).where(Signal.created_at >= window_start)),
        await db.scalar(select(func.max(RiskEvent.created_at)).where(RiskEvent.created_at >= window_start)),
        await db.scalar(select(func.max(Trade.created_at)).where(Trade.created_at >= window_start)),
        await db.scalar(select(func.max(DecisionRecord.timestamp)).where(DecisionRecord.timestamp >= window_start)),
    ]
    non_null = [value for value in latest_values if value is not None]
    if not non_null:
        return None
    return max(non_null)


def _compute_realized_performance(*, trades: list[Trade], strategy_by_signal_id: dict[uuid.UUID, uuid.UUID]):
    positions: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    realized_pnl = Decimal("0")
    win_count = 0
    loss_count = 0
    realized_by_asset: dict[uuid.UUID, Decimal] = {}
    realized_by_strategy: dict[uuid.UUID, Decimal] = {}
    wins_by_strategy: dict[uuid.UUID, int] = {}
    losses_by_strategy: dict[uuid.UUID, int] = {}

    for trade in sorted(trades, key=lambda item: (item.executed_at, item.id)):
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

        if trade.side != "sell":
            continue

        sell_qty = min(current_qty, quantity)
        if sell_qty <= 0:
            continue

        pnl = (sell_qty * price) - (sell_qty * current_avg) - fee
        realized_pnl += pnl
        realized_by_asset[trade.asset_id] = realized_by_asset.get(trade.asset_id, Decimal("0")) + pnl

        strategy_id = strategy_by_signal_id.get(trade.signal_id) if trade.signal_id is not None else None
        if strategy_id is not None:
            realized_by_strategy[strategy_id] = realized_by_strategy.get(strategy_id, Decimal("0")) + pnl

        if pnl > 0:
            win_count += 1
            if strategy_id is not None:
                wins_by_strategy[strategy_id] = wins_by_strategy.get(strategy_id, 0) + 1
        elif pnl < 0:
            loss_count += 1
            if strategy_id is not None:
                losses_by_strategy[strategy_id] = losses_by_strategy.get(strategy_id, 0) + 1

        remaining_qty = current_qty - sell_qty
        if remaining_qty <= 0:
            positions[trade.asset_id] = (Decimal("0"), Decimal("0"))
        else:
            positions[trade.asset_id] = (remaining_qty, current_avg)

    return realized_pnl, win_count, loss_count, realized_by_asset, realized_by_strategy, wins_by_strategy, losses_by_strategy


def _compute_realized_pnl_by_trade(*, trades: list[Trade]) -> dict[uuid.UUID, Decimal]:
    positions: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, Decimal]] = {}
    realized_by_trade_id: dict[uuid.UUID, Decimal] = {}

    for trade in sorted(trades, key=lambda item: (item.executed_at, item.id)):
        quantity = Decimal(str(trade.quantity))
        price = Decimal(str(trade.price))
        fee = Decimal(str(trade.fee))
        key = (trade.paper_account_id, trade.asset_id)
        current_qty, current_avg = positions.get(key, (Decimal("0"), Decimal("0")))

        if trade.side == "buy":
            total_cost = (current_qty * current_avg) + (quantity * price) + fee
            next_qty = current_qty + quantity
            next_avg = total_cost / next_qty if next_qty > 0 else Decimal("0")
            positions[key] = (next_qty, next_avg)
            continue

        if trade.side != "sell":
            continue

        sell_qty = min(current_qty, quantity)
        if sell_qty <= 0:
            continue

        pnl = (sell_qty * price) - (sell_qty * current_avg) - fee
        realized_by_trade_id[trade.id] = pnl

        remaining_qty = current_qty - sell_qty
        if remaining_qty <= 0:
            positions[key] = (Decimal("0"), Decimal("0"))
        else:
            positions[key] = (remaining_qty, current_avg)

    return realized_by_trade_id


async def _resolve_decision_records_for_signals(
    *,
    db: AsyncSession,
    signal_ids: list[uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID]:
    if not signal_ids:
        return {}

    decision_rows = (
        await db.execute(
            select(DecisionRecord.decision_id, DecisionRecord.timestamp, DecisionRecord.source_lineage)
            .where(DecisionRecord.source_lineage.is_not(None))
            .order_by(DecisionRecord.timestamp.desc())
            .limit(2000)
        )
    ).all()

    signal_texts = {str(signal_id): signal_id for signal_id in signal_ids}
    resolved: dict[uuid.UUID, uuid.UUID] = {}
    for decision_id, _timestamp, source_lineage in decision_rows:
        if not isinstance(source_lineage, dict):
            continue
        signals = source_lineage.get("signals")
        if not isinstance(signals, list):
            continue
        for signal_value in signals:
            if not isinstance(signal_value, str):
                continue
            signal_id = signal_texts.get(signal_value)
            if signal_id is None or signal_id in resolved:
                continue
            resolved[signal_id] = decision_id

    return resolved


def _floor_timestamp(value: datetime, *, interval_minutes: int) -> datetime:
    minute = (value.minute // interval_minutes) * interval_minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def _ceil_timestamp(value: datetime, *, interval_minutes: int) -> datetime:
    floored = _floor_timestamp(value, interval_minutes=interval_minutes)
    if floored == value.replace(second=0, microsecond=0):
        return floored
    return floored + timedelta(minutes=interval_minutes)


def _build_bucket_timestamps(*, start: datetime, end: datetime, interval_minutes: int) -> list[datetime]:
    timestamps: list[datetime] = []
    current = start
    while current <= end:
        timestamps.append(current)
        current += timedelta(minutes=interval_minutes)
    return timestamps


def _build_equity_curve_points(
    *,
    trades: list[Trade],
    starting_balance: Decimal,
    buckets: list[datetime],
) -> list[PaperEquityCurvePoint]:
    cash = Decimal(starting_balance)
    realized = Decimal("0")
    positions: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    marks: dict[uuid.UUID, Decimal] = {}
    points: list[PaperEquityCurvePoint] = []

    trade_index = 0
    trade_count = 0
    ordered_trades = sorted(trades, key=lambda item: (item.executed_at, item.id))

    for bucket in buckets:
        while trade_index < len(ordered_trades) and ordered_trades[trade_index].executed_at <= bucket:
            trade = ordered_trades[trade_index]
            quantity = Decimal(str(trade.quantity))
            price = Decimal(str(trade.price))
            fee = Decimal(str(trade.fee))
            current_qty, current_avg = positions.get(trade.asset_id, (Decimal("0"), Decimal("0")))

            if trade.side == "buy":
                total_cost = (current_qty * current_avg) + (quantity * price) + fee
                next_qty = current_qty + quantity
                next_avg = total_cost / next_qty if next_qty > 0 else Decimal("0")
                positions[trade.asset_id] = (next_qty, next_avg)
                cash -= (quantity * price) + fee
                marks[trade.asset_id] = price
            elif trade.side == "sell":
                sell_qty = min(current_qty, quantity)
                proceeds = (sell_qty * price) - fee
                cash += proceeds
                pnl = (sell_qty * price) - (sell_qty * current_avg) - fee
                realized += pnl
                remaining_qty = current_qty - sell_qty
                if remaining_qty <= 0:
                    positions[trade.asset_id] = (Decimal("0"), Decimal("0"))
                else:
                    positions[trade.asset_id] = (remaining_qty, current_avg)
                marks[trade.asset_id] = price

            trade_count += 1
            trade_index += 1

        unrealized = Decimal("0")
        position_value = Decimal("0")
        for asset_id, (qty, avg_price) in positions.items():
            if qty <= 0:
                continue
            mark = marks.get(asset_id, avg_price)
            position_value += qty * mark
            unrealized += (mark - avg_price) * qty

        points.append(
            PaperEquityCurvePoint(
                timestamp=bucket,
                equity=cash + position_value,
                cash_balance=cash,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                trade_count_at_point=trade_count,
            )
        )

    return points
