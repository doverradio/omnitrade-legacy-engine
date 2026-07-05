from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.registry import StrategyRegistry, strategy_registry


@dataclass(slots=True)
class PlaceholderStrategy(Strategy):
    slug: str
    default_params: dict[str, Any]

    def generate_signal(self, context: StrategyContext) -> Signal:
        raise NotImplementedError(f"Strategy '{self.slug}' has not been implemented yet.")


PLACEHOLDER_STRATEGIES: dict[str, dict[str, Any]] = {
    "rsi_mean_reversion": {"rsi_period": 14, "oversold": 30, "overbought": 70},
    "breakout": {"lookback": 20, "volume_confirmation": True, "min_volume_multiple": 1.5},
    "volatility_filter": {"atr_period": 14, "min_atr_pct": 0.2, "max_atr_pct": 5.0},
    "trend_regime_filter": {"adx_period": 14, "adx_trend_threshold": 25, "ma_slope_period": 50},
    "ensemble_scorer": {"min_strategies_agreeing": 1, "conflict_resolution": "net_strength"},
}


def register_placeholder_strategies(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    for slug, default_params in PLACEHOLDER_STRATEGIES.items():
        if registry.has(slug):
            continue

        registry.register(
            slug,
            lambda slug=slug, default_params=default_params: PlaceholderStrategy(
                slug=slug,
                default_params=dict(default_params),
            ),
        )

    return registry


register_placeholder_strategies()