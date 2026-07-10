from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.candle import Candle
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.paper_account import PaperAccount
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.profit_intelligence import (
    ProfitAnnotationResponse,
    ProfitMetricResponse,
    ProfitMode,
    ProfitRange,
    ProfitSeriesPointResponse,
)


_RANGE_TO_WINDOW: dict[ProfitRange, timedelta | None] = {
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}

_BUCKET_MINUTES: dict[ProfitRange, int] = {
    "24h": 15,
    "72h": 60,
    "7d": 240,
    "30d": 1440,
    "90d": 1440,
    "all": 1440,
}


@dataclass(slots=True)
class _PositionLot:
    quantity: Decimal = Decimal("0")
    gross_cost: Decimal = Decimal("0")
    buy_fees: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class _RealizedTradeOutcome:
    trade_id: uuid.UUID
    timestamp: datetime
    symbol: str
    strategy_id: uuid.UUID | None
    gross_outcome: Decimal
    net_outcome: Decimal
    attributed_fees: Decimal


@dataclass(frozen=True, slots=True)
class _PaperModeState:
    starting_equity: Decimal
    ending_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    fees: Decimal
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    open_position_count: int
    peak_equity: Decimal
    max_drawdown_amount: Decimal
    max_drawdown_percent: Decimal
    annotations: list[ProfitAnnotationResponse]
    equity_series: list[ProfitSeriesPointResponse]
    source_counts: dict[str, int]


def _zero() -> Decimal:
    return Decimal("0")


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _format_decimal(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"))


def _floor_time(value: datetime, minutes: int) -> datetime:
    truncated = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    rounded_minute = (truncated.minute // minutes) * minutes
    return truncated.replace(minute=rounded_minute)


def _build_buckets(start: datetime, end: datetime, minutes: int) -> list[datetime]:
    current = _floor_time(start, minutes)
    buckets: list[datetime] = []
    while current <= end:
        buckets.append(current)
        current = current + timedelta(minutes=minutes)
    if not buckets or buckets[-1] < end:
        buckets.append(end)
    return buckets


async def build_profit_metrics(
    *,
    db: AsyncSession,
    range_value: ProfitRange,
    mode: ProfitMode,
    capital_pool_id: str | None = None,
    validation_run_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    symbol: str | None = None,
) -> ProfitMetricResponse:
    now = datetime.now(timezone.utc)
    window = _RANGE_TO_WINDOW[range_value]
    start_at = None if window is None else now - window

    paper_state = await _build_paper_profit_state(
        db=db,
        start_at=start_at,
        end_at=now,
        strategy_id=strategy_id,
        symbol=symbol,
    )
    live_state = await _build_live_profit_state(db=db, start_at=start_at, end_at=now)

    source_counts = {
        **paper_state.source_counts,
        **{f"live_{key}": value for key, value in live_state["source_counts"].items()},
    }

    if mode == "paper":
        starting_equity = paper_state.starting_equity
        ending_equity = paper_state.ending_equity
        gross_profit = paper_state.gross_profit
        gross_loss = paper_state.gross_loss
        realized_pnl = paper_state.realized_pnl
        unrealized_pnl = paper_state.unrealized_pnl
        fees = paper_state.fees
        net_profit = paper_state.realized_pnl
        total_economic_pnl = paper_state.realized_pnl + paper_state.unrealized_pnl
        peak_equity = paper_state.peak_equity
        max_drawdown_amount = paper_state.max_drawdown_amount
        max_drawdown_percent = paper_state.max_drawdown_percent
        winning_trades = paper_state.winning_trades
        losing_trades = paper_state.losing_trades
        breakeven_trades = paper_state.breakeven_trades
        open_position_count = paper_state.open_position_count
        annotations = paper_state.annotations
        equity_series = paper_state.equity_series
    elif mode == "live":
        starting_equity = live_state["starting_equity"]
        ending_equity = live_state["ending_equity"]
        gross_profit = live_state["gross_profit"]
        gross_loss = live_state["gross_loss"]
        realized_pnl = live_state["realized_pnl"]
        unrealized_pnl = live_state["unrealized_pnl"]
        fees = live_state["fees"]
        net_profit = live_state["realized_pnl"]
        total_economic_pnl = live_state["realized_pnl"] + live_state["unrealized_pnl"]
        peak_equity = live_state["peak_equity"]
        max_drawdown_amount = live_state["max_drawdown_amount"]
        max_drawdown_percent = live_state["max_drawdown_percent"]
        winning_trades = live_state["winning_trades"]
        losing_trades = live_state["losing_trades"]
        breakeven_trades = live_state["breakeven_trades"]
        open_position_count = live_state["open_position_count"]
        annotations = live_state["annotations"]
        equity_series = live_state["equity_series"]
    else:
        starting_equity = paper_state.starting_equity + live_state["starting_equity"]
        ending_equity = paper_state.ending_equity + live_state["ending_equity"]
        gross_profit = paper_state.gross_profit + live_state["gross_profit"]
        gross_loss = paper_state.gross_loss + live_state["gross_loss"]
        realized_pnl = paper_state.realized_pnl + live_state["realized_pnl"]
        unrealized_pnl = paper_state.unrealized_pnl + live_state["unrealized_pnl"]
        fees = paper_state.fees + live_state["fees"]
        net_profit = realized_pnl
        total_economic_pnl = realized_pnl + unrealized_pnl
        peak_equity = paper_state.peak_equity + live_state["peak_equity"]
        max_drawdown_amount = paper_state.max_drawdown_amount + live_state["max_drawdown_amount"]
        max_drawdown_percent = paper_state.max_drawdown_percent
        winning_trades = paper_state.winning_trades + live_state["winning_trades"]
        losing_trades = paper_state.losing_trades + live_state["losing_trades"]
        breakeven_trades = paper_state.breakeven_trades + live_state["breakeven_trades"]
        open_position_count = paper_state.open_position_count + live_state["open_position_count"]
        annotations = paper_state.annotations + live_state["annotations"]
        equity_series = paper_state.equity_series

    return_percent = None
    if starting_equity and starting_equity != _zero():
        return_percent = (net_profit / starting_equity) * Decimal("100")

    trade_count = winning_trades + losing_trades + breakeven_trades
    win_rate = None if trade_count == 0 else (Decimal(winning_trades) / Decimal(trade_count)) * Decimal("100")
    profit_factor = None
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = Decimal("999")

    ranged_outcomes = [
        outcome
        for outcome in sum([
            [annotation.metadata for annotation in paper_state.annotations if annotation.event_type == "PAPER_TRADE_FILLED"]
        ], [])
        if isinstance(outcome, dict)
    ]
    average_win = None if winning_trades == 0 else gross_profit / Decimal(winning_trades)
    average_loss = None if losing_trades == 0 else gross_loss / Decimal(losing_trades)
    largest_win = max((Decimal(str(item.get("net_outcome", "0"))) for item in ranged_outcomes if Decimal(str(item.get("net_outcome", "0"))) > 0), default=None)
    largest_loss = min((Decimal(str(item.get("net_outcome", "0"))) for item in ranged_outcomes if Decimal(str(item.get("net_outcome", "0"))) < 0), default=None)

    calculation_explanation = (
        "Paper profit is computed from durable paper trades and mark-to-market open positions. "
        "Live profit uses durable live accounting records and is zero until live fills exist. "
        "Combined profit is the visible sum of paper and live components."
    )

    return ProfitMetricResponse(
        range=range_value,
        mode=mode,
        start_at=start_at,
        end_at=now,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        fees=fees,
        fees_available=True,
        net_profit=net_profit,
        total_economic_pnl=total_economic_pnl,
        return_percent=return_percent,
        peak_equity=peak_equity,
        max_drawdown_amount=max_drawdown_amount,
        max_drawdown_percent=max_drawdown_percent,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=breakeven_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        average_win=average_win,
        average_loss=average_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        trade_count=trade_count,
        open_position_count=open_position_count,
        equity_series=equity_series,
        profit_series=equity_series,
        annotations=annotations,
        source_counts=source_counts,
        data_completeness=100 if source_counts.get("paper_accounts", 0) > 0 else 0,
        calculation_explanation=calculation_explanation,
        generated_at=now,
    )


async def _build_paper_profit_state(
    *,
    db: AsyncSession,
    start_at: datetime | None,
    end_at: datetime,
    strategy_id: uuid.UUID | None,
    symbol: str | None,
) -> _PaperModeState:
    accounts = (await db.execute(select(PaperAccount).order_by(PaperAccount.created_at.asc()))).scalars().all()
    source_counts = {"paper_accounts": len(accounts), "paper_trades": 0, "signals": 0, "assets": 0}
    total_starting_equity = _zero()
    total_ending_equity = _zero()
    total_realized = _zero()
    total_unrealized = _zero()
    total_fees = _zero()
    gross_profit = _zero()
    gross_loss = _zero()
    winning_trades = 0
    losing_trades = 0
    breakeven_trades = 0
    open_positions = 0
    annotations: list[ProfitAnnotationResponse] = []
    combined_series: dict[datetime, ProfitSeriesPointResponse] = {}
    peak_equity = _zero()
    max_drawdown_amount = _zero()
    max_drawdown_percent = _zero()

    for account in accounts:
        rows = (
            await db.execute(
                select(Trade)
                .where(Trade.paper_account_id == account.id)
                .where(Trade.is_paper.is_(True))
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )
        ).scalars().all()
        strategy_by_signal = await _load_strategy_map(db=db, signal_ids=[item.signal_id for item in rows if item.signal_id is not None])
        symbol_by_asset = await _load_symbol_map(db=db, asset_ids=[item.asset_id for item in rows])

        filtered_rows = [
            item for item in rows
            if (strategy_id is None or strategy_by_signal.get(item.signal_id) == strategy_id)
            and (symbol is None or symbol_by_asset.get(item.asset_id) == symbol)
        ]
        source_counts["paper_trades"] += len(filtered_rows)
        source_counts["signals"] += len({item.signal_id for item in filtered_rows if item.signal_id is not None})
        source_counts["assets"] += len({item.asset_id for item in filtered_rows})

        prices_at_end = await _load_prices_as_of(db=db, asset_ids=[item.asset_id for item in rows], as_of=end_at)
        prices_at_start = await _load_prices_as_of(db=db, asset_ids=[item.asset_id for item in rows], as_of=start_at or account.created_at)

        start_trades = [item for item in rows if start_at is not None and item.executed_at < start_at]
        end_trades = [item for item in rows if item.executed_at <= end_at]

        start_snapshot = _compute_snapshot_from_trades(
            starting_balance=_to_decimal(account.starting_balance),
            trades=start_trades,
            latest_prices_by_asset_id=prices_at_start,
        )
        end_snapshot = _compute_snapshot_from_trades(
            starting_balance=_to_decimal(account.starting_balance),
            trades=end_trades,
            latest_prices_by_asset_id=prices_at_end,
        )
        outcomes = _compute_realized_outcomes(trades=end_trades, symbol_by_asset=symbol_by_asset, strategy_by_signal=strategy_by_signal)
        ranged_outcomes = [item for item in outcomes if start_at is None or item.timestamp >= start_at]

        total_starting_equity += start_snapshot["equity"]
        total_ending_equity += end_snapshot["equity"]
        total_unrealized += end_snapshot["unrealized_pnl"]
        open_positions += end_snapshot["open_position_count"]

        account_realized = sum((item.net_outcome for item in ranged_outcomes), _zero())
        account_fees = sum((item.attributed_fees for item in ranged_outcomes), _zero())
        account_gross_profit = sum((item.gross_outcome for item in ranged_outcomes if item.gross_outcome > 0), _zero())
        account_gross_loss = sum((-item.gross_outcome for item in ranged_outcomes if item.gross_outcome < 0), _zero())
        total_realized += account_realized
        total_fees += account_fees
        gross_profit += account_gross_profit
        gross_loss += account_gross_loss
        winning_trades += sum(1 for item in ranged_outcomes if item.net_outcome > 0)
        losing_trades += sum(1 for item in ranged_outcomes if item.net_outcome < 0)
        breakeven_trades += sum(1 for item in ranged_outcomes if item.net_outcome == 0)

        series = await _build_equity_series(
            db=db,
            starting_balance=_to_decimal(account.starting_balance),
            trades=rows,
            start_at=start_at or account.created_at,
            end_at=end_at,
        )
        for point in series:
            existing = combined_series.get(point.timestamp)
            if existing is None:
                combined_series[point.timestamp] = point
            else:
                combined_series[point.timestamp] = ProfitSeriesPointResponse(
                    timestamp=point.timestamp,
                    paper_equity=(existing.paper_equity or _zero()) + (point.paper_equity or _zero()),
                    live_equity=None,
                    combined_equity=(existing.combined_equity or _zero()) + (point.paper_equity or _zero()),
                    cumulative_realized_pnl=(existing.cumulative_realized_pnl or _zero()) + (point.cumulative_realized_pnl or _zero()),
                    cumulative_unrealized_pnl=(existing.cumulative_unrealized_pnl or _zero()) + (point.cumulative_unrealized_pnl or _zero()),
                    cumulative_fees=(existing.cumulative_fees or _zero()) + (point.cumulative_fees or _zero()),
                    cumulative_net_profit=(existing.cumulative_net_profit or _zero()) + (point.cumulative_net_profit or _zero()),
                    drawdown=(existing.drawdown or _zero()) + (point.drawdown or _zero()),
                    trade_count=existing.trade_count + point.trade_count,
                    source_event_ids=existing.source_event_ids + point.source_event_ids,
                )

        for outcome in ranged_outcomes:
            annotations.append(
                ProfitAnnotationResponse(
                    timestamp=outcome.timestamp,
                    event_type="PAPER_TRADE_FILLED",
                    title=f"{outcome.symbol} trade",
                    description=f"Closed-trade realized outcome for {outcome.symbol}.",
                    severity="green" if outcome.net_outcome >= 0 else "yellow",
                    source_record_id=str(outcome.trade_id),
                    metadata={
                        "symbol": outcome.symbol,
                        "net_outcome": format(outcome.net_outcome, "f"),
                        "gross_outcome": format(outcome.gross_outcome, "f"),
                        "fees": format(outcome.attributed_fees, "f"),
                        "trade_id": str(outcome.trade_id),
                    },
                )
            )

    ordered_series = sorted(combined_series.values(), key=lambda item: item.timestamp)
    for point in ordered_series:
        equity = point.paper_equity or _zero()
        if equity > peak_equity:
            peak_equity = equity
        drawdown_amount = peak_equity - equity
        if drawdown_amount > max_drawdown_amount:
            max_drawdown_amount = drawdown_amount
        if peak_equity > 0:
            drawdown_percent = (drawdown_amount / peak_equity) * Decimal("100")
            if drawdown_percent > max_drawdown_percent:
                max_drawdown_percent = drawdown_percent

    return _PaperModeState(
        starting_equity=total_starting_equity,
        ending_equity=total_ending_equity,
        realized_pnl=total_realized,
        unrealized_pnl=total_unrealized,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        fees=total_fees,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=breakeven_trades,
        open_position_count=open_positions,
        peak_equity=peak_equity,
        max_drawdown_amount=max_drawdown_amount,
        max_drawdown_percent=max_drawdown_percent,
        annotations=sorted(annotations, key=lambda item: item.timestamp),
        equity_series=ordered_series,
        source_counts=source_counts,
    )


async def _build_live_profit_state(*, db: AsyncSession, start_at: datetime | None, end_at: datetime) -> dict[str, object]:
    rows = (
        await db.execute(
            select(LiveAccountingRecord)
            .where(LiveAccountingRecord.recorded_at <= end_at)
            .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.id.asc())
        )
    ).scalars().all()
    ranged = [item for item in rows if start_at is None or item.recorded_at >= start_at]
    realized = sum((_to_decimal(item.net_cash_impact) for item in ranged if item.record_type in {"fill_accounting", "partial_fill_accounting"}), _zero())
    fees = sum((_to_decimal(item.fee_amount) for item in ranged if item.record_type == "fee_attribution"), _zero())
    return {
        "starting_equity": _zero(),
        "ending_equity": _zero(),
        "gross_profit": _zero(),
        "gross_loss": _zero(),
        "realized_pnl": realized,
        "unrealized_pnl": _zero(),
        "fees": fees,
        "peak_equity": _zero(),
        "max_drawdown_amount": _zero(),
        "max_drawdown_percent": _zero(),
        "winning_trades": 0,
        "losing_trades": 0,
        "breakeven_trades": 0,
        "open_position_count": 0,
        "annotations": [],
        "equity_series": [],
        "source_counts": {"live_accounting_records": len(rows)},
    }


async def _load_strategy_map(*, db: AsyncSession, signal_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    unique_signal_ids = sorted({item for item in signal_ids if item is not None}, key=str)
    if not unique_signal_ids:
        return {}
    rows = (await db.execute(select(Signal.id, Signal.strategy_id).where(Signal.id.in_(unique_signal_ids)))).all()
    return {signal_id: strategy_id for signal_id, strategy_id in rows}


async def _load_symbol_map(*, db: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    unique_asset_ids = sorted(set(asset_ids), key=str)
    if not unique_asset_ids:
        return {}
    rows = (await db.execute(select(Asset.id, Asset.symbol).where(Asset.id.in_(unique_asset_ids)))).all()
    return {asset_id: symbol for asset_id, symbol in rows}


async def _load_prices_as_of(*, db: AsyncSession, asset_ids: list[uuid.UUID], as_of: datetime) -> dict[uuid.UUID, Decimal]:
    prices: dict[uuid.UUID, Decimal] = {}
    for asset_id in sorted(set(asset_ids), key=str):
        candle_close = await db.scalar(
            select(Candle.close)
            .where(Candle.asset_id == asset_id)
            .where(Candle.close_time <= as_of)
            .order_by(Candle.close_time.desc())
            .limit(1)
        )
        if candle_close is not None:
            prices[asset_id] = _to_decimal(candle_close)
    return prices


def _compute_snapshot_from_trades(*, starting_balance: Decimal, trades: list[Trade], latest_prices_by_asset_id: dict[uuid.UUID, Decimal]) -> dict[str, Decimal | int]:
    cash = starting_balance
    positions: dict[uuid.UUID, _PositionLot] = defaultdict(_PositionLot)
    for trade in sorted(trades, key=lambda item: (item.executed_at, item.id)):
        quantity = _to_decimal(trade.quantity)
        price = _to_decimal(trade.price)
        fee = _to_decimal(trade.fee)
        lot = positions[trade.asset_id]
        if trade.side == "buy":
            lot.quantity += quantity
            lot.gross_cost += quantity * price
            lot.buy_fees += fee
            cash -= (quantity * price) + fee
        elif trade.side == "sell":
            sell_quantity = min(lot.quantity, quantity)
            if sell_quantity <= 0:
                continue
            allocated_cost = lot.gross_cost * (sell_quantity / lot.quantity) if lot.quantity > 0 else _zero()
            allocated_buy_fees = lot.buy_fees * (sell_quantity / lot.quantity) if lot.quantity > 0 else _zero()
            lot.quantity -= sell_quantity
            lot.gross_cost -= allocated_cost
            lot.buy_fees -= allocated_buy_fees
            cash += (sell_quantity * price) - fee
            if lot.quantity <= 0:
                positions[trade.asset_id] = _PositionLot()
    unrealized = _zero()
    position_value = _zero()
    open_count = 0
    for asset_id, lot in positions.items():
        if lot.quantity <= 0:
            continue
        open_count += 1
        price = latest_prices_by_asset_id.get(asset_id, _zero())
        avg_cost_with_fees = (lot.gross_cost + lot.buy_fees) / lot.quantity if lot.quantity > 0 else _zero()
        position_value += lot.quantity * price
        unrealized += (price - avg_cost_with_fees) * lot.quantity
    equity = cash + position_value
    return {
        "cash": cash,
        "equity": equity,
        "unrealized_pnl": unrealized,
        "open_position_count": open_count,
    }


def _compute_realized_outcomes(*, trades: list[Trade], symbol_by_asset: dict[uuid.UUID, str], strategy_by_signal: dict[uuid.UUID, uuid.UUID]) -> list[_RealizedTradeOutcome]:
    positions: dict[tuple[uuid.UUID, uuid.UUID], _PositionLot] = defaultdict(_PositionLot)
    outcomes: list[_RealizedTradeOutcome] = []
    for trade in sorted(trades, key=lambda item: (item.executed_at, item.id)):
        quantity = _to_decimal(trade.quantity)
        price = _to_decimal(trade.price)
        fee = _to_decimal(trade.fee)
        key = (trade.paper_account_id, trade.asset_id)
        lot = positions[key]
        if trade.side == "buy":
            lot.quantity += quantity
            lot.gross_cost += quantity * price
            lot.buy_fees += fee
            continue
        if trade.side != "sell":
            continue
        sell_quantity = min(lot.quantity, quantity)
        if sell_quantity <= 0:
            continue
        allocated_cost = lot.gross_cost * (sell_quantity / lot.quantity) if lot.quantity > 0 else _zero()
        allocated_buy_fees = lot.buy_fees * (sell_quantity / lot.quantity) if lot.quantity > 0 else _zero()
        gross_outcome = (sell_quantity * price) - allocated_cost
        attributed_fees = allocated_buy_fees + fee
        net_outcome = gross_outcome - attributed_fees
        outcomes.append(
            _RealizedTradeOutcome(
                trade_id=trade.id,
                timestamp=trade.executed_at,
                symbol=symbol_by_asset.get(trade.asset_id, "UNKNOWN"),
                strategy_id=strategy_by_signal.get(trade.signal_id) if trade.signal_id is not None else None,
                gross_outcome=gross_outcome,
                net_outcome=net_outcome,
                attributed_fees=attributed_fees,
            )
        )
        lot.quantity -= sell_quantity
        lot.gross_cost -= allocated_cost
        lot.buy_fees -= allocated_buy_fees
        if lot.quantity <= 0:
            positions[key] = _PositionLot()
    return outcomes


async def _build_equity_series(*, db: AsyncSession, starting_balance: Decimal, trades: list[Trade], start_at: datetime, end_at: datetime) -> list[ProfitSeriesPointResponse]:
    if start_at > end_at:
        return []
    if not trades:
        return [
            ProfitSeriesPointResponse(
                timestamp=start_at,
                paper_equity=starting_balance,
                combined_equity=starting_balance,
                cumulative_realized_pnl=_zero(),
                cumulative_unrealized_pnl=_zero(),
                cumulative_fees=_zero(),
                cumulative_net_profit=_zero(),
                drawdown=_zero(),
                trade_count=0,
            ),
            ProfitSeriesPointResponse(
                timestamp=end_at,
                paper_equity=starting_balance,
                combined_equity=starting_balance,
                cumulative_realized_pnl=_zero(),
                cumulative_unrealized_pnl=_zero(),
                cumulative_fees=_zero(),
                cumulative_net_profit=_zero(),
                drawdown=_zero(),
                trade_count=0,
            ),
        ]
    buckets = _build_buckets(start_at, end_at, _BUCKET_MINUTES[_infer_range(start_at, end_at)])
    series: list[ProfitSeriesPointResponse] = []
    peak = starting_balance
    for bucket in buckets:
        relevant_trades = [item for item in trades if item.executed_at <= bucket]
        asset_ids = [item.asset_id for item in relevant_trades]
        prices = await _load_prices_as_of(db=db, asset_ids=asset_ids, as_of=bucket)
        snapshot = _compute_snapshot_from_trades(
            starting_balance=starting_balance,
            trades=relevant_trades,
            latest_prices_by_asset_id=prices,
        )
        outcomes = _compute_realized_outcomes(trades=relevant_trades, symbol_by_asset={}, strategy_by_signal={})
        realized = sum((item.net_outcome for item in outcomes if item.timestamp <= bucket), _zero())
        fees = sum((item.attributed_fees for item in outcomes if item.timestamp <= bucket), _zero())
        if snapshot["equity"] > peak:
            peak = snapshot["equity"]
        drawdown = peak - snapshot["equity"]
        series.append(
            ProfitSeriesPointResponse(
                timestamp=bucket,
                paper_equity=snapshot["equity"],
                combined_equity=snapshot["equity"],
                cumulative_realized_pnl=realized,
                cumulative_unrealized_pnl=snapshot["unrealized_pnl"],
                cumulative_fees=fees,
                cumulative_net_profit=realized,
                drawdown=drawdown,
                trade_count=len(relevant_trades),
                source_event_ids=[str(item.id) for item in relevant_trades],
            )
        )
    return series


def _infer_range(start_at: datetime, end_at: datetime) -> ProfitRange:
    delta = end_at - start_at
    if delta <= timedelta(hours=24):
        return "24h"
    if delta <= timedelta(hours=72):
        return "72h"
    if delta <= timedelta(days=7):
        return "7d"
    if delta <= timedelta(days=30):
        return "30d"
    if delta <= timedelta(days=90):
        return "90d"
    return "all"
