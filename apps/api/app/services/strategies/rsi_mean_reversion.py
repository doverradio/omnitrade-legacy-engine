from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"rsi_period": 14, "buy_threshold": 30, "sell_threshold": 70}


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
            return _build_hold("Invalid strategy parameters.", timestamp, None)

        closes = extract_series(context.candles, "close")
        if closes is None:
            return _build_hold("Invalid candle data.", timestamp, None)
        if len(closes) < params["rsi_period"] + 1:
            return _build_hold("Insufficient candle history.", timestamp, None)

        current_rsi = _compute_rsi(closes, params["rsi_period"])
        if current_rsi is None:
            return _build_hold("Insufficient candle history.", timestamp, None)

        indicators = {
            "rsi_value": str(current_rsi),
            "buy_threshold": str(params["buy_threshold"]),
            "sell_threshold": str(params["sell_threshold"]),
        }

        if current_rsi <= params["buy_threshold"]:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="RSI is at or below the buy threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if current_rsi >= params["sell_threshold"]:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="RSI is at or above the sell threshold.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="RSI remained between thresholds.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Decimal | int]:
    merged = _normalize_params({**DEFAULT_PARAMS, **params})
    validate_strategy_params(
        merged,
        required_params=("rsi_period", "buy_threshold", "sell_threshold"),
        numeric_rules={
            "rsi_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "buy_threshold": NumericParamRule(minimum=Decimal("0"), maximum=Decimal("100")),
            "sell_threshold": NumericParamRule(minimum=Decimal("0"), maximum=Decimal("100")),
        },
    )
    rsi_period = int(Decimal(str(merged["rsi_period"])))
    buy_threshold = Decimal(str(merged["buy_threshold"]))
    sell_threshold = Decimal(str(merged["sell_threshold"]))
    if buy_threshold >= sell_threshold:
        raise StrategyParameterValidationError("buy_threshold must be less than sell_threshold.")
    return {"rsi_period": rsi_period, "buy_threshold": buy_threshold, "sell_threshold": sell_threshold}


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    if "buy_threshold" not in normalized and "oversold" in normalized:
        normalized["buy_threshold"] = normalized["oversold"]
    if "sell_threshold" not in normalized and "overbought" in normalized:
        normalized["sell_threshold"] = normalized["overbought"]
    return normalized


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


def _build_hold(reason: str, timestamp, rsi_value: Decimal | None) -> Signal:
    indicators = {
        "rsi_value": None if rsi_value is None else str(rsi_value),
        "buy_threshold": None,
        "sell_threshold": None,
    }
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_rsi_mean_reversion_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("rsi_mean_reversion"):
        registry.register("rsi_mean_reversion", RsiMeanReversionStrategy)
    return registry


register_rsi_mean_reversion_strategy()