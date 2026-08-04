from __future__ import annotations

from decimal import Decimal

from strategy_lab.capital import BALANCED, FULL_COMPOUNDING
from strategy_lab.comparison import (
    ALL_COST_SCENARIOS,
    EXPECTED,
    OPTIMISTIC,
    STRESS,
    build_comparison_rows,
    buy_and_hold_ending_value,
    run_one,
    write_comparison_outputs,
    write_run_outputs,
)
from strategy_lab.tests.helpers import candle


def _uptrend_candles(n=60):
    candles = []
    price = 100.0
    for h in range(n):
        o = price
        c = price * 1.01
        h_ = c * 1.002
        l = o * 0.998
        candles.append(candle(h, o=round(o, 4), h=round(h_, 4), l=round(l, 4), c=round(c, 4)))
        price = c
    return candles


def test_cost_scenarios_are_ordered_optimistic_to_stress():
    assert OPTIMISTIC.fee_pct < EXPECTED.fee_pct < STRESS.fee_pct
    assert OPTIMISTIC.slippage_pct < EXPECTED.slippage_pct < STRESS.slippage_pct
    assert ALL_COST_SCENARIOS == (OPTIMISTIC, EXPECTED, STRESS)


def test_buy_and_hold_matches_hand_computed_value():
    candles = [
        candle(0, o=100, h=101, l=99, c=100),
        candle(1, o=100, h=105, l=99, c=104),
        candle(2, o=104, h=110, l=103, c=108),
    ]
    scenario = EXPECTED  # fee 0.002, slippage 0.0005

    value = buy_and_hold_ending_value(candles, Decimal("100"), scenario)

    effective_entry = Decimal("100") * (Decimal("1") + Decimal("0.0005"))
    quantity = (Decimal("100") * (Decimal("1") - Decimal("0.002"))) / effective_entry
    expected = quantity * Decimal("108")
    assert value == expected


def test_buy_and_hold_on_empty_candles_returns_initial_capital():
    assert buy_and_hold_ending_value([], Decimal("100"), EXPECTED) == Decimal("100")


def test_run_one_produces_consistent_outcome():
    candles = _uptrend_candles(80)

    outcome = run_one(candles, timeframe="1h", cost_scenario=EXPECTED, policy=FULL_COMPOUNDING)

    assert outcome.timeframe == "1h"
    assert outcome.cost_scenario is EXPECTED
    assert outcome.policy is FULL_COMPOUNDING
    assert len(outcome.result.equity_curve) == len(candles)
    # full compounding IS the raw strategy view
    assert outcome.capital.total_economic_value_final == outcome.config.initial_capital * (
        Decimal("1") + outcome.capital.raw_strategy_net_return_pct / Decimal("100")
    )
    assert isinstance(outcome.beat_buy_and_hold, bool)


def test_run_one_is_deterministic_across_repeated_calls():
    candles = _uptrend_candles(80)

    first = run_one(candles, timeframe="1h", cost_scenario=STRESS, policy=BALANCED)
    second = run_one(candles, timeframe="1h", cost_scenario=STRESS, policy=BALANCED)

    assert first.metrics == second.metrics
    assert first.capital.total_economic_value_final == second.capital.total_economic_value_final
    assert first.buy_and_hold_ending_value == second.buy_and_hold_ending_value


def test_build_comparison_rows_has_required_columns():
    candles = _uptrend_candles(80)
    outcome = run_one(candles, timeframe="1h", cost_scenario=EXPECTED, policy=BALANCED)

    rows = build_comparison_rows([outcome])

    assert len(rows) == 1
    required = {
        "timeframe",
        "candle_count",
        "trades",
        "win_rate_pct",
        "gross_return_pct",
        "net_return_pct",
        "ending_trading_capital",
        "withdrawn_profit",
        "tax_reserve",
        "total_economic_value",
        "max_drawdown_pct",
        "profit_factor",
        "longest_losing_streak",
        "fee_drag_pct",
        "buy_and_hold_ending_value",
        "beat_buy_and_hold",
    }
    assert required.issubset(rows[0].keys())


def test_write_run_outputs_and_comparison_outputs_create_expected_files(tmp_path):
    candles = _uptrend_candles(80)
    outcome = run_one(candles, timeframe="1h", cost_scenario=EXPECTED, policy=BALANCED)

    run_dir = tmp_path / "1h" / "expected" / "balanced"
    write_run_outputs(outcome, run_dir)
    assert (run_dir / "trades.csv").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "report.txt").exists()

    comparison_dir = tmp_path / "comparison"
    write_comparison_outputs([outcome], comparison_dir)
    assert (comparison_dir / "comparison.csv").exists()
    assert (comparison_dir / "comparison.json").exists()
    assert (comparison_dir / "comparison.txt").exists()
