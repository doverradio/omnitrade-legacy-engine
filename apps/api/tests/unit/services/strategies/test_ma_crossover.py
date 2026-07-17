from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.strategies.base import StrategyContext
from app.services.strategies.ma_crossover import MovingAverageCrossoverStrategy


def build_context(closes: list[float | str], params: dict[str, object] | None = None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = [
        {
            "open_time": start + timedelta(hours=index),
            "open": value,
            "high": value,
            "low": value,
            "close": value,
        }
        for index, value in enumerate(closes)
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTCUSDT", "asset_class": "crypto"},
        interval="1h",
        current_position=None,
        strategy_parameters=params or {"fast_period": 3, "slow_period": 5, "ma_type": "sma"},
    )


def test_ma_crossover_buy_signal() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([5, 4, 3, 2, 2, 7]))

    assert signal.action == "buy"
    assert signal.reason == "Fast SMA crossed above Slow SMA."
    assert signal.indicators["signal_generated"] == "buy"
    assert signal.indicators["crossover_state"] == "bullish_cross"
    assert signal.indicators["evaluated_conditions"]["buy"] == {
        "previous_fast_ma_lte_previous_slow_ma": True,
        "fast_ma_gt_slow_ma": True,
    }
    assert signal.indicators["selection_explanations"]["buy"].startswith("BUY selected because")
    assert signal.indicators["selection_explanations"]["sell"].startswith("SELL not selected because")
    assert signal.indicators["selection_explanations"]["hold"].startswith("HOLD not selected because")

    trace = signal.indicators["strategy_rule_trace"]
    assert trace["selected_action"] == "BUY"
    assert trace["buy_condition_passed"] is True
    assert trace["sell_condition_passed"] is False
    assert trace["bullish_crossover_detected"] is True
    assert trace["bearish_crossover_detected"] is False


def test_ma_crossover_strategy_rule_trace_schema_is_consistent() -> None:
    strategy = MovingAverageCrossoverStrategy()

    buy = strategy.generate_signal(build_context([5, 4, 3, 2, 2, 7]))
    sell = strategy.generate_signal(build_context([1, 3, 6, 6, 6, 0]))
    hold = strategy.generate_signal(build_context([1, 2, 3, 4, 5, 6]))

    expected_keys = {
        "fast_period",
        "slow_period",
        "previous_fast_ma",
        "previous_slow_ma",
        "current_fast_ma",
        "current_slow_ma",
        "previous_spread",
        "current_spread",
        "bullish_crossover_detected",
        "bearish_crossover_detected",
        "buy_condition_passed",
        "sell_condition_passed",
        "selected_action",
        "distance_to_bullish_crossover",
        "candle_id",
        "candle_close_time",
    }

    assert set(buy.indicators["strategy_rule_trace"].keys()) == expected_keys
    assert set(sell.indicators["strategy_rule_trace"].keys()) == expected_keys
    assert set(hold.indicators["strategy_rule_trace"].keys()) == expected_keys


def test_ma_crossover_strategy_rule_trace_matches_strategy_values() -> None:
    strategy = MovingAverageCrossoverStrategy()
    closes = [5, 4, 3, 2, 2, 7]

    signal = strategy.generate_signal(build_context(closes))
    trace = signal.indicators["strategy_rule_trace"]

    decimal_closes = [Decimal(str(value)) for value in closes]
    expected_previous_fast = sum(decimal_closes[:-1][-3:], start=Decimal("0")) / Decimal("3")
    expected_previous_slow = sum(decimal_closes[:-1][-5:], start=Decimal("0")) / Decimal("5")
    expected_current_fast = sum(decimal_closes[-3:], start=Decimal("0")) / Decimal("3")
    expected_current_slow = sum(decimal_closes[-5:], start=Decimal("0")) / Decimal("5")

    assert trace["previous_fast_ma"] == format(expected_previous_fast, "f")
    assert trace["previous_slow_ma"] == format(expected_previous_slow, "f")
    assert trace["current_fast_ma"] == format(expected_current_fast, "f")
    assert trace["current_slow_ma"] == format(expected_current_slow, "f")
    assert trace["previous_spread"] == format(expected_previous_fast - expected_previous_slow, "f")
    assert trace["current_spread"] == format(expected_current_fast - expected_current_slow, "f")
    assert trace["distance_to_bullish_crossover"] == "0"


def test_ma_crossover_sell_signal() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([1, 3, 6, 6, 6, 0]))

    assert signal.action == "sell"
    assert signal.reason == "Fast SMA crossed below Slow SMA."
    trace = signal.indicators["strategy_rule_trace"]
    assert trace["selected_action"] == "SELL"
    assert trace["buy_condition_passed"] is False
    assert trace["sell_condition_passed"] is True


def test_ma_crossover_hold_signal() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([1, 2, 3, 4, 5, 6]))

    assert signal.action == "hold"
    assert signal.reason == "No crossover detected."
    trace = signal.indicators["strategy_rule_trace"]
    assert trace["selected_action"] == "HOLD"
    assert trace["buy_condition_passed"] is False
    assert trace["sell_condition_passed"] is False


def test_ma_crossover_insufficient_history() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([1, 2, 3, 4]))

    assert signal.action == "hold"
    assert signal.reason == "Insufficient candle history."


def test_ma_crossover_invalid_parameters() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(
        build_context([1, 2, 3, 4, 5, 6], {"fast_period": 5, "slow_period": 5, "ma_type": "sma"})
    )

    assert signal.action == "hold"
    assert signal.reason == "Invalid strategy parameters."


def test_ma_crossover_equal_moving_averages_hold() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([2, 2, 2, 2, 2, 2]))

    assert signal.action == "hold"
    assert signal.reason == "No crossover detected."


def test_ma_crossover_empty_candles() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([]))

    assert signal.action == "hold"
    assert signal.reason == "Insufficient candle history."


def test_ma_crossover_single_candle() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([1]))

    assert signal.action == "hold"
    assert signal.reason == "Insufficient candle history."


def test_ma_crossover_non_monotonic_prices() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([3, 1, 4, 2, 5, 3, 4]))

    assert signal.action in {"buy", "sell", "hold"}


def test_ma_crossover_nan_values_do_not_crash() -> None:
    strategy = MovingAverageCrossoverStrategy()

    signal = strategy.generate_signal(build_context([1, 2, 3, 4, 5, float("nan")]))

    assert signal.action == "hold"
    assert signal.reason == "Invalid candle data."


def test_ma_crossover_trading_actions_unchanged() -> None:
    strategy = MovingAverageCrossoverStrategy()

    assert strategy.generate_signal(build_context([5, 4, 3, 2, 2, 7])).action == "buy"
    assert strategy.generate_signal(build_context([1, 3, 6, 6, 6, 0])).action == "sell"
    assert strategy.generate_signal(build_context([1, 2, 3, 4, 5, 6])).action == "hold"