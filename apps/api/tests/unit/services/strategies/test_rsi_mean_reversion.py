from __future__ import annotations

from app.services.strategies.base import StrategyContext
from app.services.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from datetime import datetime, timedelta, timezone


def build_context(closes, params=None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = [
        {"open_time": start + timedelta(hours=index), "close": value, "high": value, "low": value, "open": value}
        for index, value in enumerate(closes)
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTCUSDT"},
        interval="1h",
        current_position=None,
        strategy_parameters=params or {"rsi_period": 5, "oversold": 30, "overbought": 70},
    )


def test_rsi_mean_reversion_buy_signal() -> None:
    strategy = RsiMeanReversionStrategy()
    signal = strategy.generate_signal(build_context([10, 9, 8, 7, 6, 5, 6]))
    assert signal.action == "buy"


def test_rsi_mean_reversion_sell_signal() -> None:
    strategy = RsiMeanReversionStrategy()
    signal = strategy.generate_signal(build_context([5, 6, 7, 8, 9, 10, 9]))
    assert signal.action == "sell"


def test_rsi_mean_reversion_hold_signal() -> None:
    strategy = RsiMeanReversionStrategy()
    signal = strategy.generate_signal(build_context([1, 2, 1, 2, 1, 2, 1]))
    assert signal.action == "hold"


def test_rsi_mean_reversion_invalid_input() -> None:
    strategy = RsiMeanReversionStrategy()
    signal = strategy.generate_signal(build_context([1, 2, 3, 4, 5, float("nan")]))
    assert signal.action == "hold"


def test_rsi_mean_reversion_deterministic() -> None:
    strategy = RsiMeanReversionStrategy()
    context = build_context([10, 9, 8, 7, 6, 5, 6])
    assert strategy.generate_signal(context) == strategy.generate_signal(context)