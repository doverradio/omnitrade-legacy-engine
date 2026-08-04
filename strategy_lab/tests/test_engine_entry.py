"""Entry-mode behavior: immediate placement, per-candle replacement, fills,
and no-look-ahead."""
from __future__ import annotations

from decimal import Decimal

from strategy_lab.config import SimulationConfig
from strategy_lab.costs import CostModel
from strategy_lab.engine import run_simulation
from strategy_lab.strategies.trailing_limit_v1 import TrailingLimitV1Strategy
from strategy_lab.strategy import ExitReason
from strategy_lab.tests.helpers import candle

_ZERO_COST = SimulationConfig(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))


def _run(candles, config=_ZERO_COST):
    strategy = TrailingLimitV1Strategy(config=config)
    return run_simulation(candles, strategy, config, CostModel.from_config(config))


def test_immediate_entry_and_fill_has_no_lookahead():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),  # order = 100*0.99 = 99, active from candle 1
        # low 98.5 <= 99 -> fills at min(open, price) = 99; stays above the
        # resulting initial stop (98.01) so the pessimistic same-candle
        # check (see test_intra_candle_ambiguity_policy.py) does not fire here
        candle(1, o=99.5, h=100, l=98.5, c=99.5),
        candle(2, o=97, h=97, l=95, c=96),  # initial stop breach: 99*0.99 = 98.01 >= low(95)
    ]

    result = _run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # If the order were (incorrectly) evaluated on the SAME candle whose close
    # set its price, candle 0's own low (99) would also satisfy it and the
    # fill would be recorded at candle_index 0, not 1.
    assert trade.entry_candle_index == 1
    assert trade.raw_fill_price == Decimal("99")
    assert trade.entry_order_price == Decimal("99")
    assert trade.exit_reason == ExitReason.TRAILING_FLOOR_BREACHED
    assert trade.raw_exit_price == Decimal("97")


def test_entry_order_is_replaced_every_candle_until_fill():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),  # order = 99, active from candle 1
        candle(1, o=100, h=100.5, l=99.1, c=99.2),  # 99.1 > 99 -> no fill; replaced -> 99.2*0.99 = 98.208
        candle(2, o=98.9, h=99, l=98.3, c=98.4),  # 98.3 > 98.208 -> no fill; replaced -> 98.4*0.99 = 97.416
        candle(3, o=97.5, h=97.8, l=97, c=97.3),  # low 97 <= 97.416 -> fills at min(97.5, 97.416) = 97.416
        candle(4, o=95.5, h=96, l=95, c=95.5),  # floor breach to close out the trade
    ]

    result = _run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_candle_index == 3
    assert trade.entry_order_price == Decimal("97.416")
    assert trade.raw_fill_price == Decimal("97.416")


def test_no_fill_while_flat_and_price_never_touches_order():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),  # order = 99
        candle(1, o=100, h=101, l=99.5, c=100),  # low never <= order price -> stays flat
        candle(2, o=100, h=101, l=99.5, c=100),
    ]

    result = _run(candles)

    assert result.trades == []
    assert result.ended_in_position is False
