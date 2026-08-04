from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from strategy_lab.candles import Candle

from .schema import validate_rule_document


@dataclass(frozen=True)
class EvaluationContext:
    strategy_state: str = "flat"
    capital: Decimal = Decimal("100")
    baseline_capital: Decimal = Decimal("100")


@dataclass(frozen=True)
class RuleEvaluation:
    matched: bool
    action: str
    action_value: str | None
    condition_values: dict[str, Any]
    thresholds: dict[str, Any]


def evaluate_rule(document: Mapping[str, Any], candles: Sequence[Candle], context: EvaluationContext) -> RuleEvaluation:
    normalized = validate_rule_document(document)
    if not candles:
        raise ValueError("rule evaluation requires at least one completed candle")
    values: dict[str, Any] = {}
    thresholds: dict[str, Any] = {}
    matched = _evaluate_node(normalized["when"], candles, context, values, thresholds, "when")
    return RuleEvaluation(
        matched=matched,
        action=str(normalized["then"]["action"]),
        action_value=normalized["then"].get("value"),
        condition_values=values,
        thresholds=thresholds,
    )


def _evaluate_node(node, candles, context, values, thresholds, path: str) -> bool:
    if "all" in node:
        return all(_evaluate_node(item, candles, context, values, thresholds, f"{path}.all[{index}]") for index, item in enumerate(node["all"]))
    if "any" in node:
        return any(_evaluate_node(item, candles, context, values, thresholds, f"{path}.any[{index}]") for index, item in enumerate(node["any"]))
    if "not" in node:
        return not _evaluate_node(node["not"], candles, context, values, thresholds, f"{path}.not")
    actual = _feature_value(str(node["feature"]), candles, int(node["lookback"]), context)
    expected = _reference_value(str(node["reference"]), candles, int(node["lookback"])) if "reference" in node else node["value"]
    values[path] = _json_value(actual)
    thresholds[path] = expected
    return _compare(actual, expected, str(node["operator"]), str(node["feature"]), candles, int(node["lookback"]), context)


def _feature_value(feature: str, candles: Sequence[Candle], lookback: int, context: EvaluationContext):
    window = candles[-lookback:]
    if len(window) < lookback:
        return None
    latest = window[-1]
    closes = [item.close for item in window]
    if feature == "close":
        return latest.close
    if feature == "close_above_previous_high":
        return Decimal(int(len(candles) > 1 and latest.close > candles[-2].high))
    if feature == "close_below_previous_low":
        return Decimal(int(len(candles) > 1 and latest.close < candles[-2].low))
    if feature == "consecutive_higher_closes":
        return Decimal(_consecutive(closes, increasing=True))
    if feature == "consecutive_lower_closes":
        return Decimal(_consecutive(closes, increasing=False))
    if feature == "higher_lows":
        return Decimal(int(all(window[index].low > window[index - 1].low for index in range(1, len(window)))))
    if feature == "lower_highs":
        return Decimal(int(all(window[index].high < window[index - 1].high for index in range(1, len(window)))))
    if feature == "slope":
        return _slope(closes)
    if feature == "rolling_range_pct":
        return _range_pct(window)
    if feature in {"volatility_contraction_pct", "volatility_expansion_pct"}:
        if len(candles) < lookback * 2:
            return None
        previous = candles[-lookback * 2:-lookback]
        baseline = _range_pct(previous)
        if baseline == 0:
            return Decimal("0")
        change = (_range_pct(window) - baseline) / baseline * Decimal("100")
        return -change if feature == "volatility_contraction_pct" else change
    if feature == "volatility_percentile":
        ranges = [(item.high - item.low) / item.close * Decimal("100") for item in window if item.close]
        return Decimal(sum(item <= ranges[-1] for item in ranges)) / Decimal(len(ranges)) * Decimal("100")
    if feature == "short_window_momentum":
        if len(candles) <= lookback or candles[-lookback - 1].close == 0:
            return None
        return (latest.close / candles[-lookback - 1].close - Decimal("1")) * Decimal("100")
    if feature in {"momentum_acceleration", "momentum_deceleration"}:
        if len(candles) < lookback + 2:
            return None
        current = latest.close - candles[-2].close
        prior = candles[-2].close - candles[-3].close
        change = current - prior
        return -change if feature == "momentum_deceleration" else change
    if feature in {"rapid_recovery", "rapid_decline"}:
        momentum = (latest.close / window[0].close - Decimal("1")) * Decimal("100") if window[0].close else Decimal("0")
        return momentum if feature == "rapid_recovery" else -momentum
    volumes = [item.volume for item in window]
    mean_volume = sum(volumes, Decimal("0")) / Decimal(len(volumes))
    median_volume = sorted(volumes)[len(volumes) // 2]
    if feature == "volume_above_rolling_mean":
        return Decimal(int(latest.volume > mean_volume))
    if feature == "volume_above_rolling_median":
        return Decimal(int(latest.volume > median_volume))
    if feature == "volume_expansion_pct":
        return (latest.volume / mean_volume - Decimal("1")) * Decimal("100") if mean_volume else Decimal("0")
    if feature in {"price_volume_confirmation", "price_volume_divergence"}:
        price_up = latest.close > window[0].close
        volume_up = latest.volume > mean_volume
        confirmation = price_up == volume_up
        return Decimal(int(confirmation if feature == "price_volume_confirmation" else not confirmation))
    if feature == "strategy_state":
        return context.strategy_state
    if feature == "capital_vs_baseline_pct":
        return (context.capital / context.baseline_capital - Decimal("1")) * Decimal("100") if context.baseline_capital else Decimal("0")
    raise ValueError("unsupported feature")


def _compare(actual, expected, operator: str, feature: str, candles, lookback: int, context) -> bool:
    if actual is None:
        return False
    if feature == "strategy_state":
        return operator == "==" and actual == expected
    if operator == "between":
        lower, upper = (Decimal(str(item)) for item in expected)
        return lower <= actual <= upper
    expected_decimal = Decimal(str(expected))
    if operator == "<": return actual < expected_decimal
    if operator == "<=": return actual <= expected_decimal
    if operator == ">": return actual > expected_decimal
    if operator == ">=": return actual >= expected_decimal
    if operator == "==": return actual == expected_decimal
    if operator in {"crosses_above", "crosses_below"}:
        if len(candles) < lookback + 1:
            return False
        previous = _feature_value(feature, candles[:-1], lookback, context)
        if previous is None:
            return False
        return (previous <= expected_decimal < actual) if operator == "crosses_above" else (previous >= expected_decimal > actual)
    raise ValueError("unsupported operator")


def _reference_value(reference: str, candles: Sequence[Candle], lookback: int):
    previous = candles[-lookback - 1:-1]
    if not previous:
        return Decimal("0")
    if reference == "previous_high": return max(item.high for item in previous)
    if reference == "previous_low": return min(item.low for item in previous)
    closes = [item.close for item in previous]
    if reference == "rolling_mean": return sum(closes, Decimal("0")) / Decimal(len(closes))
    if reference == "rolling_median": return sorted(closes)[len(closes) // 2]
    raise ValueError("unsupported reference")


def _consecutive(values: Sequence[Decimal], *, increasing: bool) -> int:
    count = 0
    for previous, current in zip(reversed(values[:-1]), reversed(values[1:])):
        if (current > previous) if increasing else (current < previous): count += 1
        else: break
    return count


def _slope(values: Sequence[Decimal]) -> Decimal:
    count = Decimal(len(values))
    x_mean = (count - Decimal("1")) / Decimal("2")
    y_mean = sum(values, Decimal("0")) / count
    numerator = sum((Decimal(index) - x_mean) * (value - y_mean) for index, value in enumerate(values))
    denominator = sum((Decimal(index) - x_mean) ** 2 for index in range(len(values)))
    return numerator / denominator if denominator else Decimal("0")


def _range_pct(candles: Sequence[Candle]) -> Decimal:
    mean_close = sum((item.close for item in candles), Decimal("0")) / Decimal(len(candles))
    return (max(item.high for item in candles) - min(item.low for item in candles)) / mean_close * Decimal("100") if mean_close else Decimal("0")


def _json_value(value):
    return None if value is None else str(value) if isinstance(value, Decimal) else value