from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"window": 20, "deviation_pct": 1.00}


@dataclass(slots=True)
class MeanReversionStrategy(Strategy):
    slug: str = "mean_reversion"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return hold_signal(reason="Invalid strategy parameters.", timestamp=timestamp, indicators=_indicators(None, None, None))

        closes = extract_series(context.candles, "close")
        if closes is None:
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_indicators(None, None, None))
        if len(closes) < params["window"] + 1:
            return hold_signal(reason="Insufficient candle history.", timestamp=timestamp, indicators=_indicators(None, None, None))

        history = closes[-params["window"] - 1:-1]
        rolling_mean = sum(history, start=Decimal("0")) / Decimal(len(history))
        current_close = closes[-1]
        if rolling_mean == 0:
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_indicators(None, None, None))

        deviation_pct = ((current_close - rolling_mean) / rolling_mean) * Decimal("100")
        indicators = _indicators(rolling_mean, deviation_pct, params["deviation_pct"])

        if deviation_pct <= -params["deviation_pct"]:
            return Signal(
                action="buy",
                strength=min(Decimal("1.0"), abs(deviation_pct) / max(params["deviation_pct"], Decimal("0.01"))),
                reason="Price deviated below rolling mean beyond threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if deviation_pct >= params["deviation_pct"]:
            return Signal(
                action="sell",
                strength=min(Decimal("1.0"), abs(deviation_pct) / max(params["deviation_pct"], Decimal("0.01"))),
                reason="Price deviated above rolling mean beyond threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Price remained near rolling mean.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Decimal | int]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("window", "deviation_pct"),
        numeric_rules={
            "window": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "deviation_pct": NumericParamRule(minimum=Decimal("0.01")),
        },
    )
    return {
        "window": int(Decimal(str(merged["window"]))),
        "deviation_pct": Decimal(str(merged["deviation_pct"])),
    }


def _indicators(rolling_mean: Decimal | None, deviation_pct: Decimal | None, threshold: Decimal | None) -> dict[str, str | None]:
    return {
        "rolling_mean": None if rolling_mean is None else str(rolling_mean),
        "deviation_pct": None if deviation_pct is None else str(deviation_pct),
        "deviation_threshold_pct": None if threshold is None else str(threshold),
    }


def register_mean_reversion_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("mean_reversion"):
        registry.register("mean_reversion", MeanReversionStrategy)
    return registry


register_mean_reversion_strategy()
