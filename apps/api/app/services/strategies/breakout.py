from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"lookback": 20, "volume_confirmation": True, "min_volume_multiple": 1.5}


@dataclass(slots=True)
class BreakoutStrategy(Strategy):
    slug: str = "breakout"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return _hold(timestamp, "Invalid strategy parameters.", None, None, None)

        candles = context.candles
        if len(candles) < params["lookback"] + 1:
            return _hold(timestamp, "Insufficient candle history.", None, None, None)

        closes = extract_series(candles, "close")
        highs = extract_series(candles, "high")
        lows = extract_series(candles, "low")
        volumes = extract_series(candles, "volume")
        if None in (closes, highs, lows, volumes):
            return _hold(timestamp, "Invalid candle data.", None, None, None)

        assert closes is not None and highs is not None and lows is not None and volumes is not None
        lookback = params["lookback"]
        rolling_high = max(highs[-lookback - 1:-1])
        rolling_low = min(lows[-lookback - 1:-1])
        current_close = closes[-1]
        average_volume = sum(volumes[-lookback - 1:-1], start=Decimal("0")) / Decimal(lookback)
        volume_ratio = None if average_volume == 0 else volumes[-1] / average_volume
        indicators = {
            "rolling_high": str(rolling_high),
            "rolling_low": str(rolling_low),
            "volume_ratio": None if volume_ratio is None else str(volume_ratio),
        }

        if current_close > rolling_high:
            if params["volume_confirmation"] and (volume_ratio is None or volume_ratio < params["min_volume_multiple"]):
                return hold_signal(
                    reason="Breakout lacked volume confirmation.", timestamp=timestamp, indicators=indicators
                )
            return Signal(
                action="buy",
                strength=Decimal("1.0") if volume_ratio is None else min(Decimal("1.0"), volume_ratio / params["min_volume_multiple"]),
                reason="Close broke above the rolling high.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if current_close < rolling_low:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="Close broke below the rolling low.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="No breakout detected.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("lookback", "volume_confirmation", "min_volume_multiple"),
        numeric_rules={
            "lookback": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "min_volume_multiple": NumericParamRule(minimum=Decimal("0")),
        },
    )
    return {
        "lookback": int(Decimal(str(merged["lookback"]))),
        "volume_confirmation": bool(merged["volume_confirmation"]),
        "min_volume_multiple": Decimal(str(merged["min_volume_multiple"])),
    }


def _hold(timestamp, reason: str, rolling_high, rolling_low, volume_ratio) -> Signal:
    indicators = {
        "rolling_high": None if rolling_high is None else str(rolling_high),
        "rolling_low": None if rolling_low is None else str(rolling_low),
        "volume_ratio": None if volume_ratio is None else str(volume_ratio),
    }
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_breakout_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("breakout"):
        registry.register("breakout", BreakoutStrategy)
    return registry


register_breakout_strategy()