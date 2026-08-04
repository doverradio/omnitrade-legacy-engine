"""Position-mode behavior: initial stop, trailing floor (activation and
monotonic non-decrease), and the declining-closes exit."""
from __future__ import annotations

from decimal import Decimal

from strategy_lab.config import SimulationConfig
from strategy_lab.costs import CostModel
from strategy_lab.engine import run_simulation
from strategy_lab.strategies.trailing_limit_v1 import TrailingLimitV1Strategy
from strategy_lab.strategy import ExitReason
from strategy_lab.tests.helpers import candle

_ZERO_COST_KWARGS = dict(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))


def _run(candles, **overrides):
    config = SimulationConfig(**{**_ZERO_COST_KWARGS, **overrides})
    strategy = TrailingLimitV1Strategy(config=config)
    return run_simulation(candles, strategy, config, CostModel.from_config(config))


def test_initial_stop_exit_before_trailing_activation():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),          # order = 99
        # fills at 99; initial stop = 99*0.99 = 98.01; low stays above it so
        # the pessimistic same-candle check does not fire on this candle
        candle(1, o=99.5, h=100, l=98.5, c=99.5),
        candle(2, o=97, h=97, l=95, c=96),             # low 95 <= 98.01 -> breach at min(97, 98.01) = 97
    ]

    result = _run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == ExitReason.TRAILING_FLOOR_BREACHED
    assert trade.raw_exit_price == Decimal("97")
    assert trade.holding_candles == 1


def test_trailing_floor_activates_and_never_decreases_on_pullback():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),           # order = 99
        # fills at 99; low stays above the resulting initial stop (98.01) so
        # the pessimistic same-candle check does not fire on this candle
        candle(1, o=99.5, h=100, l=98.5, c=99.5),
        # activation price = 99*1.03 = 101.97; pre-candle floor here = initial
        # stop 98.01, so low=99 does not breach; high=103 activates trailing
        # and sets the floor (for the NEXT candle) to 103*0.98 = 100.94
        candle(2, o=100, h=103, l=99, c=101),
        # pullback: high=101.2 (< 103) does NOT lower the floor back down to
        # 101.2*0.98 = 99.176 -- it must stay at 100.94
        candle(3, o=101, h=101.2, l=100.95, c=101),
        # breach with open >= floor so the fill lands exactly on the floor,
        # proving its value: min(102, 100.94) = 100.94
        candle(4, o=102, h=102, l=100, c=100.5),
    ]

    result = _run(candles, trailing_distance_pct=Decimal("0.02"))

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == ExitReason.TRAILING_FLOOR_BREACHED
    assert trade.raw_exit_price == Decimal("100.94")


def test_declining_closes_exit_requires_default_two_consecutive_declines():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),      # order = 99; closes_history now [100]
        candle(1, o=99.5, h=100, l=98, c=99.5),    # fills at 99; closes_history now [100, 99.5] (one decline)
        candle(2, o=98, h=99.5, l=97, c=99),       # closes: [..., 99.5, 99] -> 99 < 99.5 < 100: two declines
    ]

    result = _run(candles, initial_stop_pct=Decimal("0.5"))  # keep the floor far away

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == ExitReason.DECLINING_CLOSES
    assert trade.raw_exit_price == Decimal("99")
    assert trade.exit_candle_index == 2


def test_declining_closes_respects_configured_required_count():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),
        candle(1, o=99.5, h=100, l=98, c=99.5),   # fills at 99; closes [100, 99.5]
        candle(2, o=98, h=99.5, l=97, c=99),      # 2 declines present, but requirement is 3 -> no exit yet
        candle(3, o=98, h=99, l=97, c=98),        # third consecutive decline (98 < 99 < 99.5 < 100) -> exit
    ]

    result = _run(candles, initial_stop_pct=Decimal("0.5"), required_declining_candles=3)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == ExitReason.DECLINING_CLOSES
    assert trade.exit_candle_index == 3


def test_optimistic_policy_never_checks_entry_candle_for_exit():
    # Under the (non-default) "optimistic" policy, candle 1 both fills the
    # entry AND has a low that would breach the eventual initial stop --
    # but the entry candle is never itself checked for an exit under this
    # policy. See test_intra_candle_ambiguity_policy.py for the (default)
    # pessimistic behavior, which DOES catch this case.
    candles = [
        candle(0, o=100, h=101, l=99, c=100),           # order = 99
        candle(1, o=99.5, h=100, l=50, c=99.5),         # fills at 99 despite an extreme low
        candle(2, o=100, h=101, l=99, c=100),           # no breach (initial stop 98.01 not touched)
    ]

    result = _run(candles, intra_candle_ambiguity_policy="optimistic")

    assert result.trades == []
    assert result.ended_in_position is True
