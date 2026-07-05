from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import extract_series, hold_signal, resolve_timestamp, simple_moving_average
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"adx_period": 14, "adx_trend_threshold": 25, "ma_slope_period": 50}


@dataclass(slots=True)
class TrendRegimeFilterStrategy(Strategy):
    slug: str = "trend_regime_filter"
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

        closes = extract_series(context.candles, "close")
        if closes is None:
            return _hold(timestamp, "Invalid candle data.", None, None, None)
        minimum_history = max(params["adx_period"] + 1, params["ma_slope_period"] + 1)
        if len(closes) < minimum_history:
            return _hold(timestamp, "Insufficient candle history.", None, None, None)

        current_ma = simple_moving_average(closes, params["ma_slope_period"])
        previous_ma = simple_moving_average(closes[:-1], params["ma_slope_period"])
        returns = []
        for index in range(len(closes) - params["adx_period"], len(closes)):
            previous_close = closes[index - 1]
            if previous_close == 0:
                returns.append(Decimal("0"))
            else:
                returns.append(abs((closes[index] - previous_close) / previous_close) * Decimal("100"))
        adx_proxy = sum(returns, start=Decimal("0")) / Decimal(params["adx_period"])
        if current_ma is None or previous_ma is None:
            return _hold(timestamp, "Insufficient candle history.", None, None, None)

        ma_slope = current_ma - previous_ma
        indicators = {
            "adx_proxy": str(adx_proxy),
            "current_ma": str(current_ma),
            "previous_ma": str(previous_ma),
            "ma_slope": str(ma_slope),
        }

        if adx_proxy >= params["adx_trend_threshold"] and ma_slope > 0:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Market regime classified as trending_up.",
                indicators=indicators,
                timestamp=timestamp,
            )

        if adx_proxy >= params["adx_trend_threshold"] and ma_slope < 0:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="Market regime classified as trending_down.",
                indicators=indicators,
                timestamp=timestamp,
            )

        return hold_signal(reason="Market regime classified as ranging.", timestamp=timestamp, indicators=indicators)


def _validated_params(params: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("adx_period", "adx_trend_threshold", "ma_slope_period"),
        numeric_rules={
            "adx_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
            "adx_trend_threshold": NumericParamRule(minimum=Decimal("0")),
            "ma_slope_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
        },
    )
    return {
        "adx_period": int(Decimal(str(merged["adx_period"]))),
        "adx_trend_threshold": Decimal(str(merged["adx_trend_threshold"])),
        "ma_slope_period": int(Decimal(str(merged["ma_slope_period"]))),
    }


def _hold(timestamp, reason: str, adx_proxy: Decimal | None, current_ma: Decimal | None, previous_ma: Decimal | None) -> Signal:
    indicators = {
        "adx_proxy": None if adx_proxy is None else str(adx_proxy),
        "current_ma": None if current_ma is None else str(current_ma),
        "previous_ma": None if previous_ma is None else str(previous_ma),
        "ma_slope": None if current_ma is None or previous_ma is None else str(current_ma - previous_ma),
    }
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_trend_regime_filter_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("trend_regime_filter"):
        registry.register("trend_regime_filter", TrendRegimeFilterStrategy)
    return registry


register_trend_regime_filter_strategy()