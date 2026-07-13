from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.strategies.base import StrategyContext
from app.services.strategies.donchian_breakout import DonchianBreakoutStrategy


def _context(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None, params: dict[str, object] | None = None) -> StrategyContext:
    highs = highs or closes
    lows = lows or closes
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = [
        {
            "open_time": start + timedelta(minutes=15 * idx),
            "open": closes[idx],
            "high": highs[idx],
            "low": lows[idx],
            "close": closes[idx],
            "volume": 1,
        }
        for idx in range(len(closes))
    ]
    return StrategyContext(
        candles=candles,
        asset_metadata={"symbol": "BTC"},
        interval="15m",
        current_position=None,
        strategy_parameters=params or {"lookback": 3},
    )


def test_donchian_breakout_buy_sell_hold() -> None:
    strategy = DonchianBreakoutStrategy()

    buy = strategy.generate_signal(_context([100, 101, 102, 105]))
    sell = strategy.generate_signal(_context([105, 104, 103, 100]))
    hold = strategy.generate_signal(_context([100, 101, 100.5, 100.8]))

    assert buy.action == "buy"
    assert sell.action == "sell"
    assert hold.action == "hold"
