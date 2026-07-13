from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"window": 20, "std_multiplier": 2.0}


@dataclass(slots=True)
class BollingerReversionStrategy(Strategy):
    slug: str = "bollinger_reversion"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return hold_signal(reason="Invalid strategy parameters.", timestamp=timestamp, indicators=_indicators(None, None, None, None))

        closes = extract_series(context.candles, "close")
        if closes is None:
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_indicators(None, None, None, None))
        if len(closes) < params["window"] + 1:
            return hold_signal(reason="Insufficient candle history.", timestamp=timestamp, indicators=_indicators(None, None, None, None))

        history = closes[-params["window"] - 1:-1]
        mean = sum(history, start=Decimal("0")) / Decimal(len(history))
        variance = sum(((value - mean) ** 2 for value in history), start=Decimal("0")) / Decimal(len(history))
        std = variance.sqrt() if variance >= 0 else Decimal("0")

        upper = mean + (params["std_multiplier"] * std)
        lower = mean - (params["std_multiplier"] * std)
        current_close = closes[-1]

        indicators = _indicators(mean, upper, lower, std)

        if current_close < lower:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Close moved below lower Bollinger band.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if current_close > upper:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="Close moved above upper Bollinger band.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Close remained inside Bollinger bands.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Decimal | int]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("window", "std_multiplier"),
        numeric_rules={
            "window": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "std_multiplier": NumericParamRule(minimum=Decimal("0.1")),
        },
    )
    return {
        "window": int(Decimal(str(merged["window"]))),
        "std_multiplier": Decimal(str(merged["std_multiplier"])),
    }


def _indicators(mean: Decimal | None, upper: Decimal | None, lower: Decimal | None, std: Decimal | None) -> dict[str, str | None]:
    return {
        "rolling_mean": None if mean is None else str(mean),
        "upper_band": None if upper is None else str(upper),
        "lower_band": None if lower is None else str(lower),
        "std_dev": None if std is None else str(std),
    }


def register_bollinger_reversion_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("bollinger_reversion"):
        registry.register("bollinger_reversion", BollingerReversionStrategy)
    return registry


register_bollinger_reversion_strategy()
