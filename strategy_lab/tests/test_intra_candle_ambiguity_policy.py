"""Proves the (default) pessimistic intra-candle ambiguity policy: when an
entry and its protective stop are both reachable within the same candle, or
a favorable trailing update and an adverse breach are both reachable within
one candle, the engine always assumes the adverse outcome. Also proves the
named "optimistic" alternative still exists and behaves oppositely, so the
policy is genuinely configurable and testable in both directions."""
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


def test_pessimistic_default_is_intra_candle_ambiguity_policy():
    assert SimulationConfig().intra_candle_ambiguity_policy == "pessimistic"


def test_config_rejects_unknown_ambiguity_policy():
    import pytest

    with pytest.raises(ValueError):
        SimulationConfig(intra_candle_ambiguity_policy="hopeful")


def test_pessimistic_default_catches_same_candle_entry_and_stop_out():
    # Candle 1 fills the entry (low touches the order price) AND, within
    # that same candle, drops far enough to also breach the resulting
    # initial stop (99 * 0.99 = 98.01). Understating this as a healthy open
    # position (the old behavior) would hide a real loss.
    candles = [
        candle(0, o=100, h=101, l=99, c=100),      # order = 99
        candle(1, o=99.5, h=100, l=90, c=99.5),    # fills at 99; low 90 <= stop 98.01 -> same-candle stop-out
    ]

    result = _run(candles)  # pessimistic is the default

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_candle_index == 1
    assert trade.exit_candle_index == 1
    assert trade.holding_candles == 0
    assert trade.exit_reason == ExitReason.TRAILING_FLOOR_BREACHED
    # min(open=99.5, floor=98.01) -- open is above the floor, so it is a
    # clean touch, not a gap: exit lands exactly on the stop level.
    assert trade.raw_exit_price == Decimal("98.01")
    assert result.ended_in_position is False


def test_pessimistic_same_candle_check_does_not_fire_when_stop_not_reached():
    # Same shape as above, but the entry candle's low stays above the
    # resulting initial stop -- no same-candle exit should be recorded, and
    # the position should still be open for a later candle to close it.
    candles = [
        candle(0, o=100, h=101, l=99, c=100),        # order = 99
        candle(1, o=99.5, h=100, l=98.5, c=99.5),    # fills at 99; low 98.5 > stop 98.01 -> no same-candle exit
        candle(2, o=100, h=101, l=99, c=100),        # still no breach
    ]

    result = _run(candles)

    assert result.trades == []
    assert result.ended_in_position is True


def test_pessimistic_and_optimistic_resolve_the_same_ambiguous_candle_differently():
    # After entry (fill=99, initial stop F0 = 98.01), candle 2 both reaches
    # a low (97) that breaches F0, AND a high (103) that would activate
    # trailing and raise the floor to F1 = 103*0.99 = 101.97 (trailing
    # distance overridden to 1% here for round numbers). Pessimistic must
    # record the exit at the OLD, lower floor F0; optimistic must record it
    # at the NEW, raised floor F1 -- proving the two policies genuinely
    # diverge on the same input.
    candles = [
        candle(0, o=100, h=101, l=99, c=100),           # order = 99
        candle(1, o=99.5, h=100, l=98.5, c=99.5),       # fills at 99; F0 = 98.01
        candle(2, o=102, h=103, l=97, c=101),           # ambiguous: low breaches F0, high would raise to F1
    ]

    pessimistic = _run(candles, trailing_distance_pct=Decimal("0.01"))
    optimistic = _run(candles, trailing_distance_pct=Decimal("0.01"), intra_candle_ambiguity_policy="optimistic")

    assert len(pessimistic.trades) == 1
    assert pessimistic.trades[0].raw_exit_price == Decimal("98.01")
    assert pessimistic.trades[0].exit_reason == ExitReason.TRAILING_FLOOR_BREACHED

    assert len(optimistic.trades) == 1
    assert optimistic.trades[0].raw_exit_price == Decimal("101.97")
    assert optimistic.trades[0].exit_reason == ExitReason.TRAILING_FLOOR_BREACHED

    assert pessimistic.final_equity < optimistic.final_equity
