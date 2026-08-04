"""Running the same candles through the same strategy/config must always
produce identical results."""
from __future__ import annotations

from decimal import Decimal

from strategy_lab.config import SimulationConfig
from strategy_lab.costs import CostModel
from strategy_lab.engine import run_simulation
from strategy_lab.strategies.trailing_limit_v1 import TrailingLimitV1Strategy
from strategy_lab.tests.helpers import candle

_CANDLES = [
    candle(0, o=100, h=101, l=99, c=100),
    candle(1, o=99.5, h=100, l=98, c=99.5),
    candle(2, o=100, h=103, l=99, c=101),
    candle(3, o=101, h=101.2, l=100.95, c=101),
    candle(4, o=102, h=102, l=100, c=100.5),
    candle(5, o=100, h=101, l=99, c=100),
    candle(6, o=99.5, h=100, l=98, c=99.5),
    candle(7, o=98, h=99.5, l=97, c=99),
    candle(8, o=98, h=99, l=97, c=98),
]


def _run_once():
    config = SimulationConfig(trailing_distance_pct=Decimal("0.02"))
    strategy = TrailingLimitV1Strategy(config=config)
    return run_simulation(_CANDLES, strategy, config, CostModel.from_config(config))


def test_repeated_runs_produce_identical_results():
    first = _run_once()
    second = _run_once()

    assert first.trades == second.trades
    assert first.equity_curve == second.equity_curve
    assert first.final_equity == second.final_equity
    assert first.ended_in_position == second.ended_in_position


def test_input_candles_are_immutable_and_unmodified():
    before = list(_CANDLES)
    _run_once()
    assert _CANDLES == before
