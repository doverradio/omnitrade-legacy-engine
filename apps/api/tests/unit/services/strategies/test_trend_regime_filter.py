from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.strategies.base import StrategyContext
from app.services.strategies.trend_regime_filter import TrendRegimeFilterStrategy


def build_context(closes, params=None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = [
        {"open_time": start + timedelta(hours=index), "open": value, "high": value, "low": value, "close": value}
        for index, value in enumerate(closes)
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTCUSDT"},
        interval="1h",
        current_position=None,
        strategy_parameters=params or {"adx_period": 3, "adx_trend_threshold": 5, "ma_slope_period": 3},
    )


def test_trend_regime_filter_trending_up() -> None:
    strategy = TrendRegimeFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 12, 14, 16]))
    assert signal.action == "buy"


def test_trend_regime_filter_trending_down() -> None:
    strategy = TrendRegimeFilterStrategy()
    signal = strategy.generate_signal(build_context([16, 14, 12, 10]))
    assert signal.action == "sell"


def test_trend_regime_filter_ranging() -> None:
    strategy = TrendRegimeFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 10.1, 10.0, 10.1], {"adx_period": 3, "adx_trend_threshold": 20, "ma_slope_period": 3}))
    assert signal.action == "hold"


def test_trend_regime_filter_invalid_input() -> None:
    strategy = TrendRegimeFilterStrategy()
    signal = strategy.generate_signal(build_context([10, 11, 12, float("nan")]))
    assert signal.action == "hold"


def test_trend_regime_filter_deterministic() -> None:
    strategy = TrendRegimeFilterStrategy()
    context = build_context([10, 12, 14, 16])
    assert strategy.generate_signal(context) == strategy.generate_signal(context)