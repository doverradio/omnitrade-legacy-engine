from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.strategies.base import Strategy


StrategyFactory = Callable[[], Strategy]


class StrategyLookupError(LookupError):
    pass


class StrategyRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, StrategyFactory] = {}

    def register(self, slug: str, factory: StrategyFactory) -> None:
        if slug in self._registry:
            raise ValueError(f"Strategy slug '{slug}' is already registered.")
        self._registry[slug] = factory

    def register_factory(self, slug: str) -> Callable[[StrategyFactory], StrategyFactory]:
        def decorator(factory: StrategyFactory) -> StrategyFactory:
            self.register(slug, factory)
            return factory

        return decorator

    def get(self, slug: str) -> Strategy:
        if slug not in self._registry:
            raise StrategyLookupError(f"Unknown strategy slug: {slug}")
        return self._registry[slug]()

    def has(self, slug: str) -> bool:
        return slug in self._registry

    def registered_slugs(self) -> tuple[str, ...]:
        return tuple(sorted(self._registry.keys()))


strategy_registry = StrategyRegistry()