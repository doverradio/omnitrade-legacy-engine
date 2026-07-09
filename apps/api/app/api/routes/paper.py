from __future__ import annotations

import logging
import uuid
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
from app.models.trade import Trade
from app.schemas.paper import (
    CreatePaperAccountRequest,
    CreatePaperAccountResponse,
    ExecuteSignalRequest,
    ExecuteSignalResponse,
    PaperPipelineHealthResponse,
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
