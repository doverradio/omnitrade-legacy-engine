from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.strategies.base import StrategyContext
from app.services.strategies.volatility_filter import VolatilityFilterStrategy


def build_context(closes, params=None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = []
    for index, value in enumerate(closes):
        candles.append({
            "open_time": start + timedelta(hours=index),
            "open": value,
            "high": value + 1 if isinstance(value, (int, float)) else value,
            "low": value - 1 if isinstance(value, (int, float)) else value,
            "close": value,
        })
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTCUSDT"},
        interval="1h",
        current_position=None,
        strategy_parameters=params or {"atr_period": 3, "min_atr_pct": 10, "max_atr_pct": 60},
    )


def test_volatility_filter_within_band() -> None:
    strategy = VolatilityFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 12, 14, 16]))
    assert signal.action == "buy"


def test_volatility_filter_outside_band() -> None:
    strategy = VolatilityFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 10.1, 10.2, 10.3], {"atr_period": 3, "min_atr_pct": 20, "max_atr_pct": 40}))
    assert signal.action == "hold"


def test_volatility_filter_invalid_input() -> None:
    strategy = VolatilityFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 12, 14, float("nan")]))
    assert signal.action == "hold"


def test_volatility_filter_deterministic() -> None:
    strategy = VolatilityFilterStrategy()
    context = build_context([10, 12, 14, 16])
    assert strategy.generate_signal(context) == strategy.generate_signal(context)