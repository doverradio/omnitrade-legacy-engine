from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from strategy_lab.candles import Candle

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def D(value) -> Decimal:
    return Decimal(str(value))


def candle(hour: int, o, h, l, c, v="1") -> Candle:
    return Candle(
        timestamp=_EPOCH + timedelta(hours=hour),
        open=D(o),
        high=D(h),
        low=D(l),
        close=D(c),
        volume=D(v),
    )
