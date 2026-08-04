"""The deterministic candle-replay engine.

DETERMINISTIC FILL POLICY (documented, applied consistently):

1. No-look-ahead for order pricing: a resting order's price is set from a
   candle's CLOSE. Since a close is only known once the candle has fully
   completed, the order is only eligible to fill starting the NEXT candle --
   never the same candle whose close set the price.

2. Touch-fills-at-level, gap-fills-at-open: when a candle's low/high touches
   an order/stop level, the fill is assumed to occur exactly at that level.
   If the candle instead GAPS through the level (open already past it), the
   fill occurs at the candle's open, since that is the best price actually
   obtainable in that scenario. Concretely, for a BUY LIMIT at price P:
   fill_price = min(candle.open, P) whenever candle.low <= P. The same
   min() formula also correctly models a SELL stop at level F:
   fill_price = min(candle.open, F) whenever candle.low <= F (a gap down
   through a sell stop fills at the worse/lower open; a mere touch fills at
   F). This is one formula reused for both, applied consistently.

3. Close-based exits (the two-consecutive-declining-closes rule) fill at
   that candle's close -- there is no touch/gap ambiguity for a signal that
   is itself defined by the close.

4. Intra-candle ambiguity policy (`config.intra_candle_ambiguity_policy`):
   whenever a candle's OHLC range makes it impossible to know which of two
   mutually-exclusive events happened first, this setting decides how the
   engine resolves it. Where finer-grained (lower-timeframe) stored candle
   data could resolve the true intra-candle order, that evidence should be
   preferred over either assumption below -- this engine does not yet
   consume lower-timeframe data to do so; that is a documented limitation,
   not a silent gap (see README "Known limitations").

   PESSIMISTIC (the default, and the only policy used for the authoritative
   Strategy #001 report -- always assumes the adverse outcome):
     a. On the candle a BUY LIMIT fills, immediately also check that same
        candle for an exit (normally the fill candle is skipped). This
        catches the case where the same candle that filled the entry also
        dropped far enough to breach the resulting initial stop -- without
        this, such a loss would be silently understated as a healthy open
        position. See the `pessimistic` branch right after `open_position`
        is called in `run_simulation`.
     b. On every later candle while in a position: check the trailing/
        initial stop floor against this candle's LOW using the floor level
        as it stood at the END of the PREVIOUS candle (i.e. this candle's
        own high can never raise the floor and then be judged against that
        just-raised floor) BEFORE rolling the floor forward. A candle that
        could be read as "activate trailing, then get stopped at the new
        higher floor" OR "get stopped at the old floor" is always resolved
        as the latter -- the adverse stop, not the favorable trail.

   OPTIMISTIC (the original V1 behavior, kept as a named, testable
   alternative -- always assumes the favorable outcome):
     a. The fill candle is never itself checked for an exit.
     b. On every later candle: roll the floor forward using this candle's
        HIGH first, THEN check the (possibly just-raised) floor against
        this candle's low -- giving the position the benefit of the doubt
        that the favorable move happened before the adverse one.

   Under both policies, the declining-closes exit uses this candle's close
   (a known, non-ambiguous value -- there is no ordering question for a
   signal defined purely by the close) and fires after the floor check.

5. Touch-fills-at-level, gap-fills-at-open (recap of #2) applies uniformly:
   this is what makes the pessimistic same-candle stop-out in #4a resolve
   to exactly the stop level itself (never a worse or better price) --
   because the stop is always computed as a percentage below the fill
   price, and the fill price is always <= that candle's open, the
   min(open, floor) formula used for every stop/floor check always
   evaluates to `floor` in this specific same-candle case.

6. Compounding model: engine.py always compounds 100% of current equity
   into each trade (this IS the "full compounding" capital policy). Capital
   allocation choices -- how much to deploy per trade, and what to do with
   realized profit -- are handled as a separate post-processing pass in
   capital.py, deliberately kept out of this file. See capital.py's module
   docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from .candles import Candle
from .config import SimulationConfig
from .costs import CostModel
from .strategy import (
    ExitResult,
    FillResult,
    LimitOrder,
    PositionState,
    Strategy,
    TradeRecord,
)

_ONE = Decimal("1")


@dataclass(frozen=True)
class SimulationResult:
    trades: List[TradeRecord]
    equity_curve: List[Decimal]  # equity value as of the END of each candle, same length as `candles`
    initial_capital: Decimal
    final_equity: Decimal
    ended_in_position: bool


def run_simulation(
    candles: Sequence[Candle],
    strategy: Strategy,
    config: SimulationConfig,
    cost_model: Optional[CostModel] = None,
) -> SimulationResult:
    if cost_model is None:
        cost_model = CostModel.from_config(config)

    trades: List[TradeRecord] = []
    equity: Decimal = config.initial_capital
    equity_curve: List[Decimal] = []
    seen_candles: List[Candle] = []  # completed candles strictly before the one being processed

    resting_order: Optional[LimitOrder] = None
    position: Optional[PositionState] = None
    open_trade: Optional["_OpenTrade"] = None

    pessimistic = config.intra_candle_ambiguity_policy == "pessimistic"

    for index, candle in enumerate(candles):
        if position is not None and open_trade is not None:
            open_trade.highest_seen = max(open_trade.highest_seen, candle.high)
            open_trade.lowest_seen = min(open_trade.lowest_seen, candle.low)

            prior_closes = tuple(c.close for c in seen_candles)
            if not pessimistic:
                # OPTIMISTIC: give the benefit of the doubt that a favorable
                # trailing update happened before any adverse breach.
                position = strategy.update_position_state(position, candle)

            exit_result = strategy.check_exit(position, candle, prior_closes)
            if exit_result is not None:
                trade, equity = _finalize_trade(
                    open_trade=open_trade,
                    exit_result=exit_result,
                    exit_candle_index=index,
                    exit_candle=candle,
                    equity_before=equity,
                    cost_model=cost_model,
                )
                trades.append(trade)
                position = None
                open_trade = None
            elif pessimistic:
                position = strategy.update_position_state(position, candle)

        if position is None:
            filled: Optional[FillResult] = None
            if resting_order is not None and index > resting_order.placed_after_candle_index:
                if candle.low <= resting_order.price:
                    raw_fill_price = min(candle.open, resting_order.price)
                    filled = FillResult(fill_price=raw_fill_price, candle_index=index)

            history_including_this_candle = tuple(seen_candles) + (candle,)
            if filled is not None:
                position = strategy.open_position(filled, history_including_this_candle)
                open_trade = _open_trade(
                    resting_order=resting_order,
                    fill=filled,
                    candle=candle,
                    equity_before=equity,
                    cost_model=cost_model,
                )
                resting_order = None

                if pessimistic:
                    prior_closes = tuple(c.close for c in seen_candles)
                    same_candle_exit = strategy.check_exit(position, candle, prior_closes)
                    if same_candle_exit is not None:
                        trade, equity = _finalize_trade(
                            open_trade=open_trade,
                            exit_result=same_candle_exit,
                            exit_candle_index=index,
                            exit_candle=candle,
                            equity_before=equity,
                            cost_model=cost_model,
                        )
                        trades.append(trade)
                        position = None
                        open_trade = None
            else:
                new_price = strategy.propose_entry_price(history_including_this_candle)
                resting_order = LimitOrder(price=new_price, placed_after_candle_index=index)

        seen_candles.append(candle)
        equity_curve.append(equity)

    return SimulationResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=config.initial_capital,
        final_equity=equity,
        ended_in_position=position is not None,
    )


@dataclass
class _OpenTrade:
    entry_candle_index: int
    entry_timestamp: object
    entry_order_price: Decimal
    raw_fill_price: Decimal
    effective_entry_price: Decimal
    entry_fee: Decimal
    entry_slippage_cost: Decimal
    quantity: Decimal
    equity_before: Decimal
    highest_seen: Decimal
    lowest_seen: Decimal


def _open_trade(
    resting_order: Optional[LimitOrder],
    fill: FillResult,
    candle: Candle,
    equity_before: Decimal,
    cost_model: CostModel,
) -> _OpenTrade:
    effective_entry_price = cost_model.effective_buy_price(fill.fill_price)
    quantity = (equity_before * (_ONE - cost_model.fee_pct)) / effective_entry_price
    entry_fee = equity_before * cost_model.fee_pct
    entry_slippage_cost = quantity * (effective_entry_price - fill.fill_price)
    entry_order_price = resting_order.price if resting_order is not None else fill.fill_price
    return _OpenTrade(
        entry_candle_index=fill.candle_index,
        entry_timestamp=candle.timestamp,
        entry_order_price=entry_order_price,
        raw_fill_price=fill.fill_price,
        effective_entry_price=effective_entry_price,
        entry_fee=entry_fee,
        entry_slippage_cost=entry_slippage_cost,
        quantity=quantity,
        equity_before=equity_before,
        highest_seen=candle.high,
        lowest_seen=candle.low,
    )


def _finalize_trade(
    open_trade: _OpenTrade,
    exit_result: ExitResult,
    exit_candle_index: int,
    exit_candle: Candle,
    equity_before: Decimal,
    cost_model: CostModel,
) -> "tuple[TradeRecord, Decimal]":
    raw_exit_price = exit_result.exit_price
    effective_exit_price = cost_model.effective_sell_price(raw_exit_price)
    gross_proceeds = open_trade.quantity * effective_exit_price
    exit_fee = gross_proceeds * cost_model.fee_pct
    net_proceeds = gross_proceeds * (_ONE - cost_model.fee_pct)
    exit_slippage_cost = open_trade.quantity * (raw_exit_price - effective_exit_price)

    equity_after = net_proceeds
    net_return_pct = (equity_after / open_trade.equity_before) - _ONE
    gross_return_pct = (raw_exit_price / open_trade.raw_fill_price) - _ONE

    trade = TradeRecord(
        entry_candle_index=open_trade.entry_candle_index,
        entry_timestamp=open_trade.entry_timestamp,
        entry_order_price=open_trade.entry_order_price,
        raw_fill_price=open_trade.raw_fill_price,
        effective_entry_price=open_trade.effective_entry_price,
        entry_fee=open_trade.entry_fee,
        entry_slippage_cost=open_trade.entry_slippage_cost,
        exit_candle_index=exit_candle_index,
        exit_timestamp=exit_candle.timestamp,
        raw_exit_price=raw_exit_price,
        effective_exit_price=effective_exit_price,
        exit_fee=exit_fee,
        exit_slippage_cost=exit_slippage_cost,
        exit_reason=exit_result.reason,
        quantity=open_trade.quantity,
        equity_before=open_trade.equity_before,
        equity_after=equity_after,
        gross_return_pct=gross_return_pct,
        net_return_pct=net_return_pct,
        highest_price_during_trade=open_trade.highest_seen,
        lowest_price_during_trade=open_trade.lowest_seen,
        holding_candles=exit_candle_index - open_trade.entry_candle_index,
    )
    return trade, equity_after
