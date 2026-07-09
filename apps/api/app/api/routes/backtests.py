from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.schemas.backtest import (
    BacktestListItemResponse,
    BacktestListResponse,
    BacktestResponse,
    BacktestRunAcceptedResponse,
    BacktestRunRequest,
    BacktestTradeListResponse,
    BacktestTradeResponse,
)
from app.services.backtesting.persistence import (
    PersistedBacktestTrade,
    create_backtest_record,
    get_persisted_backtest,
    list_persisted_backtests,
    list_persisted_backtest_trades,
    mark_backtest_running,
    run_backtest_and_persist,
)
from app.services.strategies.registry import StrategyLookupError, strategy_registry

router = APIRouter(prefix="/backtests", tags=["backtests"])

SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h", "1d"}


@router.post("/run", response_model=BacktestRunAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_backtest(
    payload: BacktestRunRequest,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunAcceptedResponse:
    if payload.interval not in SUPPORTED_INTERVALS:
        raise InvalidRequestError(message="Invalid interval", details={"interval": payload.interval})
    if payload.initial_capital < 25:
        raise InvalidRequestError(message="Initial capital must be at least 25", details={"initial_capital": str(payload.initial_capital)})
    if payload.start_time >= payload.end_time:
        raise InvalidRequestError(
            message="Invalid time range",
            details={"start_time": payload.start_time.isoformat(), "end_time": payload.end_time.isoformat()},
        )

    strategy = await db.scalar(select(Strategy).where(Strategy.id == payload.strategy_id))
    if strategy is None:
        raise NotFoundError(message="Strategy not found", details={"strategy_id": str(payload.strategy_id)})

    parameter_set = await db.scalar(select(ParameterSet).where(ParameterSet.id == payload.parameter_set_id))
    if parameter_set is None:
        raise NotFoundError(message="Parameter set not found", details={"parameter_set_id": str(payload.parameter_set_id)})
    if parameter_set.strategy_id != payload.strategy_id:
        raise InvalidRequestError(
            message="Parameter set does not belong to strategy",
            details={"parameter_set_id": str(payload.parameter_set_id), "strategy_id": str(payload.strategy_id)},
        )

    asset = await db.scalar(select(Asset).where(Asset.id == payload.asset_id))
    if asset is None:
        raise NotFoundError(message="Asset not found", details={"asset_id": str(payload.asset_id)})

    try:
        strategy_impl = strategy_registry.get(strategy.slug)
    except StrategyLookupError as exc:
        raise InvalidRequestError(message=str(exc), details={"strategy_slug": strategy.slug}) from exc

    candles = (
        await db.execute(
            select(Candle)
            .where(Candle.asset_id == payload.asset_id)
            .where(Candle.interval == payload.interval)
            .where(Candle.open_time >= payload.start_time)
            .where(Candle.open_time <= payload.end_time)
            .order_by(Candle.open_time.asc())
        )
    ).scalars().all()
    if not candles:
        raise InvalidRequestError(message="Insufficient candle history for the requested range", details={})

    minimum_history = _minimum_history_required(strategy.slug, parameter_set.params)
    if len(candles) < minimum_history:
        raise InvalidRequestError(
            message="Insufficient candle history for the requested range",
            details={"required_candles": minimum_history, "available_candles": len(candles)},
        )

    backtest = await create_backtest_record(
        db,
        strategy_id=payload.strategy_id,
        parameter_set_id=payload.parameter_set_id,
        asset_id=payload.asset_id,
        interval=payload.interval,
        start_time=payload.start_time,
        end_time=payload.end_time,
        initial_capital=payload.initial_capital,
        fee_bps=payload.fee_bps,
        slippage_bps=payload.slippage_bps,
        created_by="system",
    )
    await mark_backtest_running(db, backtest.id)
    await run_backtest_and_persist(
        db,
        backtest_id=backtest.id,
        strategy=strategy_impl,
        asset_metadata={
            "id": str(asset.id),
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
            "supports_fractional": asset.supports_fractional,
        },
        candles=[
            {
                "open_time": candle.open_time,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ],
        strategy_parameters=parameter_set.params,
    )

    return BacktestRunAcceptedResponse(backtest_id=backtest.id, status="running")


@router.get("", response_model=BacktestListResponse)
async def list_backtests(db: AsyncSession = Depends(get_db)) -> BacktestListResponse:
    results = await list_persisted_backtests(db)
    return BacktestListResponse(
        items=[
            BacktestListItemResponse.model_validate(
                {
                    "id": item.id,
                    "status": item.status,
                    "strategy_id": item.strategy_id,
                    "parameter_set_id": item.parameter_set_id,
                    "asset_id": item.asset_id,
                    "interval": item.interval,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "initial_capital": item.initial_capital,
                    "fee_bps": item.fee_bps,
                    "slippage_bps": item.slippage_bps,
                    "metrics": item.metrics,
                    "small_account_warning": item.small_account_warning,
                }
            )
            for item in results
        ],
        next_cursor=None,
    )


@router.get("/{backtest_id}", response_model=BacktestResponse)
async def get_backtest(backtest_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> BacktestResponse:
    result = await get_persisted_backtest(db, backtest_id)
    if result is None:
        raise NotFoundError(message="Backtest not found", details={"backtest_id": str(backtest_id)})

    return BacktestResponse.model_validate(
        {
            "id": result.id,
            "status": result.status,
            "strategy_id": result.strategy_id,
            "parameter_set_id": result.parameter_set_id,
            "asset_id": result.asset_id,
            "initial_capital": result.initial_capital,
            "metrics": result.metrics,
            "small_account_warning": result.small_account_warning,
            "trades": [_serialize_trade(trade) for trade in result.trades],
            "error_detail": result.error_detail,
        }
    )


@router.get("/{backtest_id}/trades", response_model=BacktestTradeListResponse)
async def get_backtest_trades(backtest_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> BacktestTradeListResponse:
    trades = await list_persisted_backtest_trades(db, backtest_id)
    if trades is None:
        raise NotFoundError(message="Backtest not found", details={"backtest_id": str(backtest_id)})

    return BacktestTradeListResponse(
        items=[BacktestTradeResponse.model_validate(_serialize_trade(trade)) for trade in trades],
        next_cursor=None,
    )


def _minimum_history_required(strategy_slug: str, params: dict[str, object]) -> int:
    if strategy_slug == "ma_crossover":
        return int(params.get("slow_period", 50)) + 1
    if strategy_slug == "rsi_mean_reversion":
        return int(params.get("rsi_period", 14)) + 1
    if strategy_slug == "breakout":
        return int(params.get("lookback", 20)) + 1
    if strategy_slug == "volatility_filter":
        return int(params.get("atr_period", 14)) + 1
    if strategy_slug == "trend_regime_filter":
        return max(int(params.get("adx_period", 14)) + 1, int(params.get("ma_slope_period", 50)) + 1)
    return 1


def _serialize_trade(trade: PersistedBacktestTrade) -> dict[str, object]:
    return {
        "side": trade.side,
        "quantity": trade.quantity,
        "price": trade.price,
        "executed_at": trade.executed_at,
        "reason": trade.reason,
    }