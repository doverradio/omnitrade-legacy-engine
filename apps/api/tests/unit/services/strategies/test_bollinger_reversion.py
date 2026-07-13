from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.strategies.base import StrategyContext
from app.services.strategies.bollinger_reversion import BollingerReversionStrategy


def _context(closes: list[float], params: dict[str, object] | None = None) -> StrategyContext:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = [
        {
            "open_time": start + timedelta(minutes=15 * idx),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1,
        }
        for idx, price in enumerate(closes)
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTC"},
        interval="15m",
        current_position=None,
        strategy_parameters=params or {"window": 4, "std_multiplier": 1.0},
    )


def test_bollinger_reversion_buy_sell_hold() -> None:
    strategy = BollingerReversionStrategy()

    buy = strategy.generate_signal(_context([100, 100, 100, 100, 90]))
    sell = strategy.generate_signal(_context([100, 100, 100, 100, 110]))
    hold = strategy.generate_signal(_context([100, 100, 100, 100, 100]))

    assert buy.action == "buy"
    assert sell.action == "sell"
    assert hold.action == "hold"
