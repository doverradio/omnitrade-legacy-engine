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


PLACEHOLDER_STRATEGIES: dict[str, dict[str, Any]] = {}


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