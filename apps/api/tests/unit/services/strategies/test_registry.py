from __future__ import annotations

import pytest

from app.services.strategies.builtins import register_builtin_strategies
from app.services.strategies.registry import StrategyLookupError, StrategyRegistry


def test_registry_registration_and_lookup() -> None:
    registry = StrategyRegistry()
    register_builtin_strategies(registry)

    strategy = registry.get("ma_crossover")

    assert strategy.slug == "ma_crossover"
    assert registry.has("ensemble_scorer")


def test_registry_invalid_lookup_raises_clear_error() -> None:
    registry = StrategyRegistry()

    with pytest.raises(StrategyLookupError, match="Unknown strategy slug"):
        registry.get("does_not_exist")


def test_registry_registers_all_documented_placeholder_strategies() -> None:
    registry = StrategyRegistry()
    register_builtin_strategies(registry)

    assert registry.registered_slugs() == (
        "bollinger_reversion",
        "breakout",
        "donchian_breakout",
        "ensemble_scorer",
        "ma_crossover",
        "mean_reversion",
        "momentum",
        "rsi_mean_reversion",
        "trend_regime_filter",
        "volatility_filter",
    )