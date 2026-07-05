from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest import Backtest
from app.models.backtest_trade import BacktestTrade as BacktestTradeModel
from app.services.backtesting.engine import BacktestEngine
from app.services.backtesting.metrics import BacktestMetrics, compute_backtest_metrics
from app.services.strategies.base import Strategy


@dataclass(frozen=True, slots=True)
class PersistedBacktestTrade:
    side: str
    quantity: str
    price: str
    executed_at: datetime | Any
    reason: str | None


@dataclass(frozen=True, slots=True)
class PersistedBacktestResult:
    id: str
    status: str
    strategy_id: str
    parameter_set_id: str
    asset_id: str
    interval: str
    start_time: datetime
    end_time: datetime
    initial_capital: str
    fee_bps: str
    slippage_bps: str
    metrics: dict[str, Any] | None
    small_account_warning: dict[str, Any] | None
    trades: tuple[PersistedBacktestTrade, ...]
    error_detail: str | None = None


async def create_backtest_record(
    session: AsyncSession,
    *,
    strategy_id: uuid.UUID,
    parameter_set_id: uuid.UUID,
    asset_id: uuid.UUID,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    initial_capital: Decimal,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    created_by: str,
) -> Backtest:
    backtest = Backtest(
        strategy_id=strategy_id,
        parameter_set_id=parameter_set_id,
        asset_id=asset_id,
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        initial_capital=initial_capital,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        status="pending",
        created_by=created_by,
    )
    session.add(backtest)
    await session.commit()
    await session.refresh(backtest)
    return backtest


async def mark_backtest_running(session: AsyncSession, backtest_id: uuid.UUID) -> Backtest:
    backtest = await _get_backtest(session, backtest_id)
    backtest.status = "running"
    await session.commit()
    await session.refresh(backtest)
    return backtest


async def mark_backtest_failed(
    session: AsyncSession, backtest_id: uuid.UUID, *, error_detail: str
) -> PersistedBacktestResult:
    backtest = await _get_backtest(session, backtest_id)
    backtest.status = "failed"
    backtest.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(backtest)
    return _serialize_backtest(backtest, trades=(), error_detail=error_detail)


async def run_backtest_and_persist(
    session: AsyncSession,
    *,
    backtest_id: uuid.UUID,
    strategy: Strategy,
    asset_metadata: dict[str, Any],
    candles: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    strategy_parameters: dict[str, Any],
) -> PersistedBacktestResult:
    backtest = await _get_backtest(session, backtest_id)
    try:
        engine = BacktestEngine(
            strategy=strategy,
            asset_metadata=asset_metadata,
            interval=backtest.interval,
            strategy_parameters=strategy_parameters,
            initial_capital=backtest.initial_capital,
        )
        engine_result = engine.run(candles)
        metrics = compute_backtest_metrics(
            engine_result,
            total_fees=Decimal("0"),
            total_slippage=Decimal("0"),
        )

        backtest.status = "completed"
        backtest.metrics = _serialize_metrics(metrics)
        backtest.small_account_warning = _serialize_warning(metrics)
        backtest.completed_at = datetime.now(timezone.utc)

        for trade in engine_result.trades:
            session.add(
                BacktestTradeModel(
                    backtest_id=backtest.id,
                    side=trade.side,
                    quantity=trade.quantity,
                    price=trade.price,
                    executed_at=trade.executed_at,
                    reason=trade.reason,
                )
            )

        await session.commit()

        await session.refresh(backtest)
        trades = await _fetch_trades(session, backtest.id)
        return _serialize_backtest(backtest, trades=trades)
    except Exception as exc:
        await session.rollback()
        return await mark_backtest_failed(session, backtest_id, error_detail=str(exc))


async def get_persisted_backtest(session: AsyncSession, backtest_id: uuid.UUID) -> PersistedBacktestResult | None:
    result = await session.execute(select(Backtest).where(Backtest.id == backtest_id))
    backtest = result.scalar_one_or_none()
    if backtest is None:
        return None
    trades = await _fetch_trades(session, backtest.id)
    return _serialize_backtest(backtest, trades=trades)


async def list_persisted_backtests(session: AsyncSession) -> tuple[PersistedBacktestResult, ...]:
    result = await session.execute(select(Backtest).order_by(Backtest.created_at.desc()))
    backtests = result.scalars().all()
    serialized: list[PersistedBacktestResult] = []
    for backtest in backtests:
        serialized.append(_serialize_backtest(backtest, trades=()))
    return tuple(serialized)


async def list_persisted_backtest_trades(
    session: AsyncSession, backtest_id: uuid.UUID
) -> tuple[PersistedBacktestTrade, ...] | None:
    result = await session.execute(select(Backtest.id).where(Backtest.id == backtest_id))
    exists = result.scalar_one_or_none()
    if exists is None:
        return None
    return await _fetch_trades(session, backtest_id)


def _serialize_metrics(metrics: BacktestMetrics) -> dict[str, Any]:
    return {
        "total_return_usd": str(metrics.total_return_usd),
        "total_return_pct": str(metrics.total_return_pct),
        "win_rate": str(metrics.win_rate),
        "max_drawdown": str(metrics.max_drawdown),
        "sharpe_like": str(metrics.sharpe_like),
        "trade_count": metrics.trade_count,
        "average_trade_usd": str(metrics.average_trade_usd),
        "fee_drag_pct": str(metrics.fee_drag_pct),
    }


def _serialize_warning(metrics: BacktestMetrics) -> dict[str, Any] | None:
    if metrics.small_account_warning is None:
        return None
    return {
        "type": metrics.small_account_warning.type,
        "detail": metrics.small_account_warning.detail,
    }


async def _get_backtest(session: AsyncSession, backtest_id: uuid.UUID) -> Backtest:
    result = await session.execute(select(Backtest).where(Backtest.id == backtest_id))
    backtest = result.scalar_one()
    return backtest


async def _fetch_trades(session: AsyncSession, backtest_id: uuid.UUID) -> tuple[PersistedBacktestTrade, ...]:
    result = await session.execute(
        select(BacktestTradeModel).where(BacktestTradeModel.backtest_id == backtest_id).order_by(BacktestTradeModel.executed_at)
    )
    trades = result.scalars().all()
    return tuple(
        PersistedBacktestTrade(
            side=trade.side,
            quantity=str(trade.quantity),
            price=str(trade.price),
            executed_at=trade.executed_at,
            reason=trade.reason,
        )
        for trade in trades
    )


def _serialize_backtest(
    backtest: Backtest,
    *,
    trades: tuple[PersistedBacktestTrade, ...],
    error_detail: str | None = None,
) -> PersistedBacktestResult:
    return PersistedBacktestResult(
        id=str(backtest.id),
        status=backtest.status,
        strategy_id=str(backtest.strategy_id),
        parameter_set_id=str(backtest.parameter_set_id),
        asset_id=str(backtest.asset_id),
        interval=backtest.interval,
        start_time=backtest.start_time,
        end_time=backtest.end_time,
        initial_capital=str(backtest.initial_capital),
        fee_bps=str(backtest.fee_bps),
        slippage_bps=str(backtest.slippage_bps),
        metrics=backtest.metrics,
        small_account_warning=backtest.small_account_warning,
        trades=trades,
        error_detail=error_detail,
    )