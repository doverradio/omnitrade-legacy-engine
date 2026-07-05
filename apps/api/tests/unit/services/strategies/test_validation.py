from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.strategies.validation import (
    NumericParamRule,
    StrategyParameterValidationError,
    validate_enum_param,
    validate_numeric_param,
    validate_required_params,
    validate_strategy_params,
)


def test_parameter_validation_helpers_accept_valid_values() -> None:
    params = {"fast_period": 10, "slow_period": 50, "ma_type": "sma"}

    validate_required_params(params, ("fast_period", "slow_period", "ma_type"))
    assert validate_numeric_param(params, "fast_period", minimum=Decimal("1"), integer_only=True) == Decimal("10")
    assert validate_enum_param(params, "ma_type", ("sma", "ema")) == "sma"
    validate_strategy_params(
        params,
        required_params=("fast_period", "slow_period", "ma_type"),
        numeric_rules={
            "fast_period": NumericParamRule(minimum=Decimal("1"), integer_only=True),
            "slow_period": NumericParamRule(minimum=Decimal("2"), integer_only=True),
        },
        enum_rules={"ma_type": ("sma", "ema")},
    )


def test_parameter_validation_rejects_missing_param() -> None:
    with pytest.raises(StrategyParameterValidationError, match="Missing required"):
        validate_required_params({"fast_period": 10}, ("fast_period", "slow_period"))


def test_parameter_validation_rejects_invalid_numeric_range() -> None:
    with pytest.raises(StrategyParameterValidationError, match="must be >= 1"):
        validate_numeric_param({"fast_period": 0}, "fast_period", minimum=Decimal("1"), integer_only=True)


def test_parameter_validation_rejects_invalid_enum_value() -> None:
    with pytest.raises(StrategyParameterValidationError, match="must be one of"):
        validate_enum_param({"ma_type": "wma"}, "ma_type", ("sma", "ema"))