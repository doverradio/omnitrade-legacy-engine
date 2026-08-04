from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

RULE_SCHEMA_VERSION = "1.0.0"

SUPPORTED_FEATURES = frozenset({
    "close",
    "close_above_previous_high",
    "close_below_previous_low",
    "consecutive_higher_closes",
    "consecutive_lower_closes",
    "higher_lows",
    "lower_highs",
    "slope",
    "rolling_range_pct",
    "volatility_contraction_pct",
    "volatility_expansion_pct",
    "volatility_percentile",
    "short_window_momentum",
    "momentum_acceleration",
    "momentum_deceleration",
    "rapid_recovery",
    "rapid_decline",
    "volume_above_rolling_mean",
    "volume_above_rolling_median",
    "volume_expansion_pct",
    "price_volume_confirmation",
    "price_volume_divergence",
    "strategy_state",
    "capital_vs_baseline_pct",
})
SUPPORTED_OPERATORS = frozenset({"<", "<=", ">", ">=", "==", "between", "crosses_above", "crosses_below"})
SUPPORTED_ACTIONS = frozenset({
    "ALLOW_LONG_ENTRY",
    "BLOCK_LONG_ENTRY",
    "WAIT_FOR_CONFIRMATION",
    "CHANGE_ENTRY_OFFSET",
    "EXIT_POSITION",
    "ACTIVATE_TRAILING",
    "CHANGE_TRAILING_DISTANCE",
    "CHANGE_POSITION_DEPLOYMENT",
})
STRATEGY_STATES = frozenset({
    "flat",
    "entry_pending",
    "position_open",
    "profit_mode_active",
    "capital_below_baseline",
    "capital_above_baseline",
    "stop_established",
})


class RuleValidationError(ValueError):
    pass


def validate_rule_document(document: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown(document, {"schema_version", "when", "then", "risk_controls"}, "rule")
    if document.get("schema_version") != RULE_SCHEMA_VERSION:
        raise RuleValidationError(f"schema_version must be {RULE_SCHEMA_VERSION}")
    when = document.get("when")
    then = document.get("then")
    if not isinstance(when, Mapping) or not isinstance(then, Mapping):
        raise RuleValidationError("when and then must be objects")
    normalized_when = _validate_condition(when, "when")
    _reject_unknown(then, {"action", "value"}, "then")
    action = then.get("action")
    if action not in SUPPORTED_ACTIONS:
        raise RuleValidationError("unsupported action")
    normalized_then: dict[str, Any] = {"action": action}
    if action in {"CHANGE_ENTRY_OFFSET", "CHANGE_TRAILING_DISTANCE", "CHANGE_POSITION_DEPLOYMENT"}:
        if "value" not in then:
            raise RuleValidationError(f"{action} requires value")
        normalized_then["value"] = _decimal_text(then["value"], "then.value")
    elif "value" in then:
        raise RuleValidationError(f"{action} does not accept value")
    risk_controls = document.get("risk_controls", {})
    if not isinstance(risk_controls, Mapping):
        raise RuleValidationError("risk_controls must be an object")
    _reject_unknown(risk_controls, {"minimum_occurrences", "maximum_drawdown_pct", "final_test_used_for_tuning"}, "risk_controls")
    minimum_occurrences = int(risk_controls.get("minimum_occurrences", 5))
    if minimum_occurrences < 1:
        raise RuleValidationError("minimum_occurrences must be positive")
    maximum_drawdown_pct = _decimal_text(risk_controls.get("maximum_drawdown_pct", "25"), "risk_controls.maximum_drawdown_pct")
    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "when": normalized_when,
        "then": normalized_then,
        "risk_controls": {
            "minimum_occurrences": minimum_occurrences,
            "maximum_drawdown_pct": maximum_drawdown_pct,
            "final_test_used_for_tuning": bool(risk_controls.get("final_test_used_for_tuning", False)),
        },
    }


def _validate_condition(node: Mapping[str, Any], path: str) -> dict[str, Any]:
    boolean_keys = [key for key in ("all", "any", "not") if key in node]
    if boolean_keys:
        if len(boolean_keys) != 1 or len(node) != 1:
            raise RuleValidationError(f"{path} must contain exactly one Boolean operator")
        key = boolean_keys[0]
        value = node[key]
        if key == "not":
            if not isinstance(value, Mapping):
                raise RuleValidationError(f"{path}.not must be an object")
            return {"not": _validate_condition(value, f"{path}.not")}
        if not isinstance(value, list) or not value:
            raise RuleValidationError(f"{path}.{key} must be a non-empty array")
        if not all(isinstance(item, Mapping) for item in value):
            raise RuleValidationError(f"{path}.{key} entries must be objects")
        return {key: [_validate_condition(item, f"{path}.{key}[{index}]") for index, item in enumerate(value)]}

    _reject_unknown(node, {"feature", "operator", "value", "reference", "lookback"}, path)
    feature = node.get("feature")
    operator = node.get("operator")
    if feature not in SUPPORTED_FEATURES:
        raise RuleValidationError(f"unsupported feature at {path}")
    if operator not in SUPPORTED_OPERATORS:
        raise RuleValidationError(f"unsupported operator at {path}")
    lookback = int(node.get("lookback", 1))
    if lookback < 1 or lookback > 500:
        raise RuleValidationError(f"lookback at {path} must be between 1 and 500 completed candles")
    reference = node.get("reference")
    if reference is not None and (not isinstance(reference, str) or any(token in reference.lower() for token in ("future", "next", "lead"))):
        raise RuleValidationError(f"look-ahead reference rejected at {path}")
    if "value" not in node and reference is None:
        raise RuleValidationError(f"{path} requires value or reference")
    if "value" in node and reference is not None:
        raise RuleValidationError(f"{path} cannot contain both value and reference")
    normalized: dict[str, Any] = {"feature": feature, "operator": operator, "lookback": lookback}
    if reference is not None:
        if reference not in {"previous_high", "previous_low", "rolling_mean", "rolling_median"}:
            raise RuleValidationError(f"unsupported reference at {path}")
        normalized["reference"] = reference
    elif operator == "between":
        value = node["value"]
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise RuleValidationError(f"between at {path} requires two values")
        normalized["value"] = [_decimal_text(item, f"{path}.value") for item in value]
    else:
        value = node["value"]
        if feature == "strategy_state":
            if value not in STRATEGY_STATES:
                raise RuleValidationError(f"unsupported strategy state at {path}")
            normalized["value"] = value
        else:
            normalized["value"] = _decimal_text(value, f"{path}.value")
    return normalized


def _decimal_text(value: Any, path: str) -> str:
    if isinstance(value, bool):
        raise RuleValidationError(f"{path} must be numeric")
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise RuleValidationError(f"{path} must be numeric") from exc


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise RuleValidationError(f"unsupported field(s) at {path}: {', '.join(sorted(unknown))}")