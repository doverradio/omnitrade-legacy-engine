from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"lookback": 20}


@dataclass(slots=True)
class DonchianBreakoutStrategy(Strategy):
    slug: str = "donchian_breakout"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return hold_signal(reason="Invalid strategy parameters.", timestamp=timestamp, indicators=_indicators(None, None))

        highs = extract_series(context.candles, "high")
        lows = extract_series(context.candles, "low")
        closes = extract_series(context.candles, "close")
        if None in (highs, lows, closes):
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_indicators(None, None))

        assert highs is not None and lows is not None and closes is not None
        if len(closes) < params["lookback"] + 1:
            return hold_signal(reason="Insufficient candle history.", timestamp=timestamp, indicators=_indicators(None, None))

        highest_high = max(highs[-params["lookback"] - 1:-1])
        lowest_low = min(lows[-params["lookback"] - 1:-1])
        current_close = closes[-1]
        indicators = _indicators(highest_high, lowest_low)

        if current_close > highest_high:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Close broke above Donchian channel high.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if current_close < lowest_low:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="Close broke below Donchian channel low.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Close remained inside Donchian channel.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, int]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("lookback",),
        numeric_rules={"lookback": NumericParamRule(minimum=Decimal("2"), integer_only=True)},
    )
    return {"lookback": int(Decimal(str(merged["lookback"])))}


def _indicators(highest_high: Decimal | None, lowest_low: Decimal | None) -> dict[str, str | None]:
    return {
        "rolling_high": None if highest_high is None else str(highest_high),
        "rolling_low": None if lowest_low is None else str(lowest_low),
    }


def register_donchian_breakout_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("donchian_breakout"):
        registry.register("donchian_breakout", DonchianBreakoutStrategy)
    return registry


register_donchian_breakout_strategy()
