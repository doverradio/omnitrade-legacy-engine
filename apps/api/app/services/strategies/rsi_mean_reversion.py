from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"rsi_period": 14, "oversold": 30, "overbought": 70}


@dataclass(slots=True)
class RsiMeanReversionStrategy(Strategy):
    slug: str = "rsi_mean_reversion"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return _build_hold("Invalid strategy parameters.", timestamp, None, None)

        closes = extract_series(context.candles, "close")
        if closes is None:
            return _build_hold("Invalid candle data.", timestamp, None, None)
        if len(closes) < params["rsi_period"] + 2:
            return _build_hold("Insufficient candle history.", timestamp, None, None)

        previous_rsi = _compute_rsi(closes[:-1], params["rsi_period"])
        current_rsi = _compute_rsi(closes, params["rsi_period"])
        if previous_rsi is None or current_rsi is None:
            return _build_hold("Insufficient candle history.", timestamp, current_rsi, None)

        rsi_slope = current_rsi - previous_rsi
        indicators = {
            "rsi_value": str(current_rsi),
            "rsi_slope": str(rsi_slope),
        }

        if previous_rsi <= params["oversold"] and rsi_slope > 0:
            return Signal(
                action="buy",
                strength=min(Decimal("1.0"), abs(rsi_slope) / Decimal("100")),
                reason="RSI crossed below oversold and turned back up.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if previous_rsi >= params["overbought"] and rsi_slope < 0:
            return Signal(
                action="sell",
                strength=min(Decimal("1.0"), abs(rsi_slope) / Decimal("100")),
                reason="RSI crossed above overbought and turned back down.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="No RSI reversal detected.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Decimal | int]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("rsi_period", "oversold", "overbought"),
        numeric_rules={
            "rsi_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "oversold": NumericParamRule(minimum=Decimal("0"), maximum=Decimal("100")),
            "overbought": NumericParamRule(minimum=Decimal("0"), maximum=Decimal("100")),
        },
    )
    rsi_period = int(Decimal(str(merged["rsi_period"])))
    oversold = Decimal(str(merged["oversold"]))
    overbought = Decimal(str(merged["overbought"]))
    if oversold >= overbought:
        raise StrategyParameterValidationError("oversold must be less than overbought.")
    return {"rsi_period": rsi_period, "oversold": oversold, "overbought": overbought}


def _compute_rsi(closes: list[Decimal], period: int) -> Decimal | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    window = deltas[-period:]
    gains = [delta for delta in window if delta > 0]
    losses = [abs(delta) for delta in window if delta < 0]
    average_gain = sum(gains, start=Decimal("0")) / Decimal(period)
    average_loss = sum(losses, start=Decimal("0")) / Decimal(period)
    if average_loss == 0:
        return Decimal("100") if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))


def _build_hold(reason: str, timestamp, rsi_value: Decimal | None, rsi_slope: Decimal | None) -> Signal:
    indicators = {
        "rsi_value": None if rsi_value is None else str(rsi_value),
        "rsi_slope": None if rsi_slope is None else str(rsi_slope),
    }
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_rsi_mean_reversion_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("rsi_mean_reversion"):
        registry.register("rsi_mean_reversion", RsiMeanReversionStrategy)
    return registry


register_rsi_mean_reversion_strategy()