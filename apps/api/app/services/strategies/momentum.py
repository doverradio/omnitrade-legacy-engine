from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"lookback": 6, "threshold_pct": 0.30}


@dataclass(slots=True)
class MomentumStrategy(Strategy):
    slug: str = "momentum"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return hold_signal(reason="Invalid strategy parameters.", timestamp=timestamp, indicators=_base_indicators(None, None))

        closes = extract_series(context.candles, "close")
        if closes is None:
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_base_indicators(None, None))
        if len(closes) < params["lookback"] + 1:
            return hold_signal(reason="Insufficient candle history.", timestamp=timestamp, indicators=_base_indicators(None, None))

        lookback_close = closes[-params["lookback"] - 1]
        latest_close = closes[-1]
        if lookback_close == 0:
            return hold_signal(reason="Invalid candle data.", timestamp=timestamp, indicators=_base_indicators(None, None))

        momentum_pct = ((latest_close - lookback_close) / lookback_close) * Decimal("100")
        indicators = _base_indicators(momentum_pct, params["threshold_pct"])

        if momentum_pct >= params["threshold_pct"]:
            return Signal(
                action="buy",
                strength=min(Decimal("1.0"), abs(momentum_pct) / max(params["threshold_pct"], Decimal("0.01"))),
                reason="Directional momentum exceeded the bullish threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if momentum_pct <= -params["threshold_pct"]:
            return Signal(
                action="sell",
                strength=min(Decimal("1.0"), abs(momentum_pct) / max(params["threshold_pct"], Decimal("0.01"))),
                reason="Directional momentum exceeded the bearish threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Momentum remained inside neutral threshold.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Decimal | int]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("lookback", "threshold_pct"),
        numeric_rules={
            "lookback": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "threshold_pct": NumericParamRule(minimum=Decimal("0.01")),
        },
    )
    return {
        "lookback": int(Decimal(str(merged["lookback"]))),
        "threshold_pct": Decimal(str(merged["threshold_pct"])),
    }


def _base_indicators(momentum_pct: Decimal | None, threshold_pct: Decimal | None) -> dict[str, str | None]:
    return {
        "momentum_pct": None if momentum_pct is None else str(momentum_pct),
        "threshold_pct": None if threshold_pct is None else str(threshold_pct),
    }


def register_momentum_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("momentum"):
        registry.register("momentum", MomentumStrategy)
    return registry


register_momentum_strategy()
