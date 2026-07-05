from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.strategies.base import StrategyContext
from app.services.strategies.breakout import BreakoutStrategy


def build_context(closes, highs=None, lows=None, volumes=None, params=None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    highs = highs or closes
    lows = lows or closes
    volumes = volumes or [1] * len(closes)
    candles = [
        {
            "open_time": start + timedelta(hours=index),
            "open": closes[index],
            "high": highs[index],
            "low": lows[index],
            "close": closes[index],
            "volume": volumes[index],
        }
        for index in range(len(closes))
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTCUSDT"},
        interval="1h",
        current_position=None,
        strategy_parameters=params or {"lookback": 3, "volume_confirmation": True, "min_volume_multiple": 1.2},
    )


def test_breakout_buy_signal() -> None:
    strategy = BreakoutStrategy()
    signal = strategy.generate_signal(build_context([1, 2, 3, 5], volumes=[1, 1, 1, 3]))
    assert signal.action == "buy"


def test_breakout_sell_signal() -> None:
    strategy = BreakoutStrategy()
    signal = strategy.generate_signal(build_context([5, 4, 3, 1]))
    assert signal.action == "sell"


def test_breakout_hold_without_volume_confirmation() -> None:
    strategy = BreakoutStrategy()
    signal = strategy.generate_signal(build_context([1, 2, 3, 5], volumes=[1, 1, 1, 1]))
    assert signal.action == "hold"


def test_breakout_invalid_input() -> None:
    strategy = BreakoutStrategy()
    signal = strategy.generate_signal(build_context([1, 2, 3, float("nan")]))
    assert signal.action == "hold"


def test_breakout_deterministic() -> None:
    strategy = BreakoutStrategy()
    context = build_context([1, 2, 3, 5], volumes=[1, 1, 1, 3])
    assert strategy.generate_signal(context) == strategy.generate_signal(context)