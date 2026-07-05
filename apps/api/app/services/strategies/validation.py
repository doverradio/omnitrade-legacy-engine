from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class StrategyParameterValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NumericParamRule:
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    integer_only: bool = False


def validate_required_params(params: dict[str, Any], required_params: list[str] | tuple[str, ...]) -> None:
    missing = [name for name in required_params if name not in params]
    if missing:
        raise StrategyParameterValidationError(
            f"Missing required strategy parameter(s): {', '.join(missing)}"
        )


def validate_numeric_param(
    params: dict[str, Any],
    name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    integer_only: bool = False,
) -> Decimal:
    if name not in params:
        raise StrategyParameterValidationError(f"Missing required strategy parameter: {name}")

    raw_value = params[name]
    try:
        numeric_value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise StrategyParameterValidationError(
            f"Strategy parameter '{name}' must be numeric."
        ) from exc

    if integer_only and numeric_value != numeric_value.to_integral_value():
        raise StrategyParameterValidationError(
            f"Strategy parameter '{name}' must be an integer value."
        )

    if minimum is not None and numeric_value < minimum:
        raise StrategyParameterValidationError(
            f"Strategy parameter '{name}' must be >= {minimum}."
        )

    if maximum is not None and numeric_value > maximum:
        raise StrategyParameterValidationError(
            f"Strategy parameter '{name}' must be <= {maximum}."
        )

    return numeric_value


def validate_enum_param(
    params: dict[str, Any], name: str, allowed_values: list[str] | tuple[str, ...]
) -> str:
    if name not in params:
        raise StrategyParameterValidationError(f"Missing required strategy parameter: {name}")

    value = params[name]
    if value not in allowed_values:
        allowed_text = ", ".join(allowed_values)
        raise StrategyParameterValidationError(
            f"Strategy parameter '{name}' must be one of: {allowed_text}."
        )
    return str(value)


def validate_strategy_params(
    params: dict[str, Any],
    *,
    required_params: list[str] | tuple[str, ...] = (),
    numeric_rules: dict[str, NumericParamRule] | None = None,
    enum_rules: dict[str, list[str] | tuple[str, ...]] | None = None,
) -> None:
    validate_required_params(params, required_params)

    for name, rule in (numeric_rules or {}).items():
        validate_numeric_param(
            params,
            name,
            minimum=rule.minimum,
            maximum=rule.maximum,
            integer_only=rule.integer_only,
        )

    for name, allowed_values in (enum_rules or {}).items():
        validate_enum_param(params, name, allowed_values)