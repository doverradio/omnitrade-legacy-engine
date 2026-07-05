from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from app.services.strategies.base import Signal, StrategyContext, coerce_decimal


def resolve_timestamp(context: StrategyContext) -> datetime:
    if context.candles:
        candidate = context.candles[-1].get("open_time") or context.candles[-1].get("timestamp")
        if isinstance(candidate, datetime):
            return candidate
    return datetime.now(timezone.utc)


def extract_candle_decimal(candle: Any, field: str) -> Decimal | None:
    if isinstance(candle, dict):
        return coerce_decimal(candle.get(field))
    return coerce_decimal(candle.get(field)) if hasattr(candle, "get") else None


def extract_series(candles: Iterable[Any], field: str) -> list[Decimal] | None:
    values: list[Decimal] = []
    for candle in candles:
        value = extract_candle_decimal(candle, field)
        if value is None:
            return None
        values.append(value)
    return values


def simple_moving_average(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window, start=Decimal("0")) / Decimal(period)


def hold_signal(
    *,
    reason: str,
    timestamp: datetime,
    indicators: dict[str, Any],
    strength: Decimal = Decimal("0.0"),
) -> Signal:
    return Signal(
        action="hold",
        strength=strength,
        reason=reason,
        indicators=indicators,
        timestamp=timestamp,
    )