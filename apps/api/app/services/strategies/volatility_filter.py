from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"atr_period": 14, "min_atr_pct": 0.2, "max_atr_pct": 5.0}


@dataclass(slots=True)
class VolatilityFilterStrategy(Strategy):
    slug: str = "volatility_filter"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return _hold(timestamp, "Invalid strategy parameters.", None, None)

        candles = context.candles
        if len(candles) < params["atr_period"] + 1:
            return _hold(timestamp, "Insufficient candle history.", None, None)

        highs = extract_series(candles, "high")
        lows = extract_series(candles, "low")
        closes = extract_series(candles, "close")
        if None in (highs, lows, closes):
            return _hold(timestamp, "Invalid candle data.", None, None)

        assert highs is not None and lows is not None and closes is not None
        true_ranges: list[Decimal] = []
        for index in range(1, len(candles)):
            high = highs[index]
            low = lows[index]
            previous_close = closes[index - 1]
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

        atr_period = params["atr_period"]
        atr = sum(true_ranges[-atr_period:], start=Decimal("0")) / Decimal(atr_period)
        current_close = closes[-1]
        atr_pct = Decimal("0") if current_close == 0 else (atr / current_close) * Decimal("100")
        indicators = {"atr": str(atr), "atr_pct": str(atr_pct)}

        if params["min_atr_pct"] <= atr_pct <= params["max_atr_pct"]:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Volatility is within the acceptable band.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Volatility is outside the acceptable band.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("atr_period", "min_atr_pct", "max_atr_pct"),
        numeric_rules={
            "atr_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "min_atr_pct": NumericParamRule(minimum=Decimal("0")),
            "max_atr_pct": NumericParamRule(minimum=Decimal("0")),
        },
    )
    atr_period = int(Decimal(str(merged["atr_period"])))
    min_atr_pct = Decimal(str(merged["min_atr_pct"]))
    max_atr_pct = Decimal(str(merged["max_atr_pct"]))
    if min_atr_pct > max_atr_pct:
        raise StrategyParameterValidationError("min_atr_pct must be less than or equal to max_atr_pct.")
    return {"atr_period": atr_period, "min_atr_pct": min_atr_pct, "max_atr_pct": max_atr_pct}


def _hold(timestamp, reason: str, atr: Decimal | None, atr_pct: Decimal | None) -> Signal:
    indicators = {"atr": None if atr is None else str(atr), "atr_pct": None if atr_pct is None else str(atr_pct)}
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_volatility_filter_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("volatility_filter"):
        registry.register("volatility_filter", VolatilityFilterStrategy)
    return registry


register_volatility_filter_strategy()