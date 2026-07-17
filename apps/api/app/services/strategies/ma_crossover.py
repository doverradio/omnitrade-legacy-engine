from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.services.strategies.base import (
    Signal,
    Strategy,
    StrategyContext,
    build_indicator_snapshot,
    coerce_decimal,
)
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import (
    NumericParamRule,
    StrategyParameterValidationError,
    validate_strategy_params,
)


DEFAULT_PARAMS = {"fast_period": 10, "slow_period": 50, "ma_type": "sma"}
_UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class MovingAverageCrossoverStrategy(Strategy):
    slug: str = "ma_crossover"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = _resolve_timestamp(context)
        trace_fast_period, trace_slow_period = _trace_periods(context)

        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return _hold_signal(
                reason="Invalid strategy parameters.",
                timestamp=timestamp,
                indicators=_indicators_with_strategy_rule_trace(
                    indicators=build_indicator_snapshot(
                        fast_ma=None,
                        slow_ma=None,
                        previous_fast_ma=None,
                        previous_slow_ma=None,
                    ),
                    context=context,
                    selected_action="HOLD",
                    fast_period=trace_fast_period,
                    slow_period=trace_slow_period,
                    previous_fast_ma=None,
                    previous_slow_ma=None,
                    current_fast_ma=None,
                    current_slow_ma=None,
                ),
            )

        candles = context.candles
        slow_period = params["slow_period"]
        if len(candles) < slow_period + 1:
            return _hold_signal(
                reason="Insufficient candle history.",
                timestamp=timestamp,
                indicators=_indicators_with_strategy_rule_trace(
                    indicators=build_indicator_snapshot(
                        fast_ma=None,
                        slow_ma=None,
                        previous_fast_ma=None,
                        previous_slow_ma=None,
                    ),
                    context=context,
                    selected_action="HOLD",
                    fast_period=fast_period if "fast_period" in locals() else trace_fast_period,
                    slow_period=slow_period,
                    previous_fast_ma=None,
                    previous_slow_ma=None,
                    current_fast_ma=None,
                    current_slow_ma=None,
                ),
            )

        closes = [_extract_close(candle) for candle in candles]
        if any(close is None for close in closes):
            return _hold_signal(
                reason="Invalid candle data.",
                timestamp=timestamp,
                indicators=_indicators_with_strategy_rule_trace(
                    indicators=build_indicator_snapshot(
                        fast_ma=None,
                        slow_ma=None,
                        previous_fast_ma=None,
                        previous_slow_ma=None,
                    ),
                    context=context,
                    selected_action="HOLD",
                    fast_period=fast_period if "fast_period" in locals() else trace_fast_period,
                    slow_period=slow_period,
                    previous_fast_ma=None,
                    previous_slow_ma=None,
                    current_fast_ma=None,
                    current_slow_ma=None,
                ),
            )

        numeric_closes = [close for close in closes if close is not None]
        fast_period = params["fast_period"]

        previous_fast_ma = _simple_moving_average(numeric_closes[:-1], fast_period)
        previous_slow_ma = _simple_moving_average(numeric_closes[:-1], slow_period)
        fast_ma = _simple_moving_average(numeric_closes, fast_period)
        slow_ma = _simple_moving_average(numeric_closes, slow_period)

        indicators = _build_strategy_evidence(
            fast_ma=fast_ma,
            slow_ma=slow_ma,
            previous_fast_ma=previous_fast_ma,
            previous_slow_ma=previous_slow_ma,
        )

        if None in (fast_ma, slow_ma, previous_fast_ma, previous_slow_ma):
            return Signal(
                action="hold",
                strength=Decimal("0.0"),
                reason="Insufficient candle history.",
                indicators=_indicators_with_strategy_rule_trace(
                    indicators=indicators,
                    context=context,
                    selected_action="HOLD",
                    fast_period=fast_period,
                    slow_period=slow_period,
                    previous_fast_ma=previous_fast_ma,
                    previous_slow_ma=previous_slow_ma,
                    current_fast_ma=fast_ma,
                    current_slow_ma=slow_ma,
                ),
                timestamp=timestamp,
            )

        assert fast_ma is not None
        assert slow_ma is not None
        assert previous_fast_ma is not None
        assert previous_slow_ma is not None

        if previous_fast_ma <= previous_slow_ma and fast_ma > slow_ma:
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Fast SMA crossed above Slow SMA.",
                indicators={
                    **_indicators_with_strategy_rule_trace(
                        indicators=indicators,
                        context=context,
                        selected_action="BUY",
                        fast_period=fast_period,
                        slow_period=slow_period,
                        previous_fast_ma=previous_fast_ma,
                        previous_slow_ma=previous_slow_ma,
                        current_fast_ma=fast_ma,
                        current_slow_ma=slow_ma,
                    ),
                    **_selection_evidence(action="buy", buy_selected=True, sell_selected=False),
                },
                timestamp=timestamp,
            )

        if previous_fast_ma >= previous_slow_ma and fast_ma < slow_ma:
            return Signal(
                action="sell",
                strength=Decimal("1.0"),
                reason="Fast SMA crossed below Slow SMA.",
                indicators={
                    **_indicators_with_strategy_rule_trace(
                        indicators=indicators,
                        context=context,
                        selected_action="SELL",
                        fast_period=fast_period,
                        slow_period=slow_period,
                        previous_fast_ma=previous_fast_ma,
                        previous_slow_ma=previous_slow_ma,
                        current_fast_ma=fast_ma,
                        current_slow_ma=slow_ma,
                    ),
                    **_selection_evidence(action="sell", buy_selected=False, sell_selected=True),
                },
                timestamp=timestamp,
            )

        return Signal(
            action="hold",
            strength=Decimal("0.0"),
            reason="No crossover detected.",
            indicators={
                **_indicators_with_strategy_rule_trace(
                    indicators=indicators,
                    context=context,
                    selected_action="HOLD",
                    fast_period=fast_period,
                    slow_period=slow_period,
                    previous_fast_ma=previous_fast_ma,
                    previous_slow_ma=previous_slow_ma,
                    current_fast_ma=fast_ma,
                    current_slow_ma=slow_ma,
                ),
                **_selection_evidence(action="hold", buy_selected=False, sell_selected=False),
            },
            timestamp=timestamp,
        )


def _validated_params(params: dict[str, Any]) -> dict[str, int | str]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("fast_period", "slow_period", "ma_type"),
        numeric_rules={
            "fast_period": NumericParamRule(minimum=Decimal("1"), integer_only=True),
            "slow_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
        },
        enum_rules={"ma_type": ("sma",)},
    )

    fast_period = int(Decimal(str(merged["fast_period"])))
    slow_period = int(Decimal(str(merged["slow_period"])))
    if fast_period >= slow_period:
        raise StrategyParameterValidationError("fast_period must be less than slow_period.")

    return {"fast_period": fast_period, "slow_period": slow_period, "ma_type": str(merged["ma_type"])}


def _extract_close(candle: Any) -> Decimal | None:
    if isinstance(candle, dict):
        return coerce_decimal(candle.get("close"))
    return coerce_decimal(candle.get("close")) if hasattr(candle, "get") else None


def _simple_moving_average(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window, start=Decimal("0")) / Decimal(period)


def _resolve_timestamp(context: StrategyContext) -> datetime:
    if context.candles:
        candidate = context.candles[-1].get("open_time") or context.candles[-1].get("timestamp")
        if isinstance(candidate, datetime):
            return candidate
    return datetime.now(timezone.utc)


def _hold_signal(*, reason: str, timestamp: datetime, indicators: dict[str, Any] | None = None) -> Signal:
    return Signal(
        action="hold",
        strength=Decimal("0.0"),
        reason=reason,
        indicators=indicators
        if isinstance(indicators, dict)
        else build_indicator_snapshot(
            fast_ma=None,
            slow_ma=None,
            previous_fast_ma=None,
            previous_slow_ma=None,
        ),
        timestamp=timestamp,
    )


def _trace_periods(context: StrategyContext) -> tuple[int | None, int | None]:
    params = dict(context.strategy_parameters or {})
    fast = _coerce_int(params.get("fast_period"))
    slow = _coerce_int(params.get("slow_period"))
    return fast, slow


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(Decimal(str(value)))
    except Exception:
        return None


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return _UNKNOWN
    return format(value, "f")


def _period_text(value: int | None) -> str:
    if value is None:
        return _UNKNOWN
    return str(value)


def _bool_or_unknown(value: bool | None) -> bool | str:
    if value is None:
        return _UNKNOWN
    return value


def _candle_trace_identity(context: StrategyContext) -> tuple[str, str]:
    if not context.candles:
        return _UNKNOWN, _UNKNOWN
    last = context.candles[-1]
    if not isinstance(last, dict):
        return _UNKNOWN, _UNKNOWN
    candle_id = str(last.get("candle_id") or _UNKNOWN)
    close_time_raw = last.get("close_time")
    if isinstance(close_time_raw, datetime):
        close_time = close_time_raw.astimezone(timezone.utc).isoformat() if close_time_raw.tzinfo else close_time_raw.replace(tzinfo=timezone.utc).isoformat()
    else:
        close_time = str(close_time_raw or _UNKNOWN)
    return candle_id, close_time


def _indicators_with_strategy_rule_trace(
    *,
    indicators: dict[str, Any],
    context: StrategyContext,
    selected_action: str,
    fast_period: int | None,
    slow_period: int | None,
    previous_fast_ma: Decimal | None,
    previous_slow_ma: Decimal | None,
    current_fast_ma: Decimal | None,
    current_slow_ma: Decimal | None,
) -> dict[str, Any]:
    previous_spread = None
    if previous_fast_ma is not None and previous_slow_ma is not None:
        previous_spread = previous_fast_ma - previous_slow_ma

    current_spread = None
    if current_fast_ma is not None and current_slow_ma is not None:
        current_spread = current_fast_ma - current_slow_ma

    bullish: bool | None = None
    bearish: bool | None = None
    if previous_fast_ma is not None and previous_slow_ma is not None and current_fast_ma is not None and current_slow_ma is not None:
        bullish = previous_fast_ma <= previous_slow_ma and current_fast_ma > current_slow_ma
        bearish = previous_fast_ma >= previous_slow_ma and current_fast_ma < current_slow_ma

    distance_to_bullish = _UNKNOWN
    if current_spread is not None:
        if current_spread > Decimal("0"):
            distance_to_bullish = "0"
        else:
            distance_to_bullish = format(Decimal("0") - current_spread, "f")

    candle_id, candle_close_time = _candle_trace_identity(context)

    strategy_rule_trace = {
        "fast_period": _period_text(fast_period),
        "slow_period": _period_text(slow_period),
        "previous_fast_ma": _decimal_text(previous_fast_ma),
        "previous_slow_ma": _decimal_text(previous_slow_ma),
        "current_fast_ma": _decimal_text(current_fast_ma),
        "current_slow_ma": _decimal_text(current_slow_ma),
        "previous_spread": _decimal_text(previous_spread),
        "current_spread": _decimal_text(current_spread),
        "bullish_crossover_detected": _bool_or_unknown(bullish),
        "bearish_crossover_detected": _bool_or_unknown(bearish),
        "buy_condition_passed": _bool_or_unknown(bullish),
        "sell_condition_passed": _bool_or_unknown(bearish),
        "selected_action": selected_action,
        "distance_to_bullish_crossover": distance_to_bullish,
        "candle_id": candle_id,
        "candle_close_time": candle_close_time,
    }
    return {**indicators, "strategy_rule_trace": strategy_rule_trace}


def _build_strategy_evidence(
    *,
    fast_ma: Decimal | None,
    slow_ma: Decimal | None,
    previous_fast_ma: Decimal | None,
    previous_slow_ma: Decimal | None,
) -> dict[str, Any]:
    indicators = build_indicator_snapshot(
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        previous_fast_ma=previous_fast_ma,
        previous_slow_ma=previous_slow_ma,
    )

    buy_previous_fast_ma_lte_previous_slow_ma = previous_fast_ma is not None and previous_slow_ma is not None and previous_fast_ma <= previous_slow_ma
    buy_fast_ma_gt_slow_ma = fast_ma is not None and slow_ma is not None and fast_ma > slow_ma
    sell_previous_fast_ma_gte_previous_slow_ma = previous_fast_ma is not None and previous_slow_ma is not None and previous_fast_ma >= previous_slow_ma
    sell_fast_ma_lt_slow_ma = fast_ma is not None and slow_ma is not None and fast_ma < slow_ma

    buy_selected = buy_previous_fast_ma_lte_previous_slow_ma and buy_fast_ma_gt_slow_ma
    sell_selected = sell_previous_fast_ma_gte_previous_slow_ma and sell_fast_ma_lt_slow_ma

    if buy_selected:
        crossover_state = "bullish_cross"
    elif sell_selected:
        crossover_state = "bearish_cross"
    else:
        crossover_state = "no_crossover"

    indicators.update(
        {
            "crossover_state": crossover_state,
            "signal_generated": "unknown",
            "evaluated_conditions": {
                "buy": {
                    "previous_fast_ma_lte_previous_slow_ma": buy_previous_fast_ma_lte_previous_slow_ma,
                    "fast_ma_gt_slow_ma": buy_fast_ma_gt_slow_ma,
                },
                "sell": {
                    "previous_fast_ma_gte_previous_slow_ma": sell_previous_fast_ma_gte_previous_slow_ma,
                    "fast_ma_lt_slow_ma": sell_fast_ma_lt_slow_ma,
                },
            },
            "selection_explanations": {
                "buy": None,
                "sell": None,
                "hold": None,
            },
        }
    )
    return indicators


def _selection_evidence(*, action: str, buy_selected: bool, sell_selected: bool) -> dict[str, Any]:
    if action == "buy":
        return {
            "signal_generated": "buy",
            "selection_explanations": {
                "buy": "BUY selected because previous_fast_ma <= previous_slow_ma and fast_ma > slow_ma evaluated to true.",
                "sell": "SELL not selected because previous_fast_ma >= previous_slow_ma and fast_ma < slow_ma evaluated to false.",
                "hold": "HOLD not selected because the bullish crossover conditions were satisfied.",
            },
        }
    if action == "sell":
        return {
            "signal_generated": "sell",
            "selection_explanations": {
                "buy": "BUY not selected because previous_fast_ma <= previous_slow_ma and fast_ma > slow_ma evaluated to false.",
                "sell": "SELL selected because previous_fast_ma >= previous_slow_ma and fast_ma < slow_ma evaluated to true.",
                "hold": "HOLD not selected because the bearish crossover conditions were satisfied.",
            },
        }
    return {
        "signal_generated": "hold",
        "selection_explanations": {
            "buy": "BUY not selected because bullish crossover conditions were not satisfied.",
            "sell": "SELL not selected because bearish crossover conditions were not satisfied.",
            "hold": "HOLD selected because neither bullish nor bearish crossover conditions were satisfied.",
        },
    }


def register_ma_crossover_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("ma_crossover"):
        registry.register("ma_crossover", MovingAverageCrossoverStrategy)
    return registry


register_ma_crossover_strategy()