from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ENABLED_PHASE1_ROSTER: tuple[str, ...] = (
    "ma_crossover",
    "momentum",
    "breakout",
    "mean_reversion",
    "rsi_mean_reversion",
    "bollinger_reversion",
    "donchian_breakout",
)


@dataclass(frozen=True)
class StrategyRosterDefinition:
    slug: str
    minimum_history_param: str
    fallback_minimum_history: int


def _def(slug: str, param: str, fallback: int) -> StrategyRosterDefinition:
    return StrategyRosterDefinition(slug=slug, minimum_history_param=param, fallback_minimum_history=fallback)


ROSTER_DEFINITIONS: dict[str, StrategyRosterDefinition] = {
    "ma_crossover": _def("ma_crossover", "slow_period", 50),
    "momentum": _def("momentum", "lookback", 6),
    "breakout": _def("breakout", "lookback", 20),
    "mean_reversion": _def("mean_reversion", "window", 20),
    "rsi_mean_reversion": _def("rsi_mean_reversion", "rsi_period", 14),
    "bollinger_reversion": _def("bollinger_reversion", "window", 20),
    "donchian_breakout": _def("donchian_breakout", "lookback", 20),
}


def minimum_history_required(*, slug: str, params: dict[str, object]) -> int:
    definition = ROSTER_DEFINITIONS[slug]
    raw = params.get(definition.minimum_history_param, definition.fallback_minimum_history)
    try:
        base = int(Decimal(str(raw)))
    except Exception:
        base = definition.fallback_minimum_history
    return max(2, base) + 1
