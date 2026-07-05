from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Signal, Strategy, StrategyContext
from app.services.strategies.helpers import hold_signal, resolve_timestamp
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.validation import NumericParamRule, StrategyParameterValidationError, validate_strategy_params


DEFAULT_PARAMS = {"min_strategies_agreeing": 1, "conflict_resolution": "net_strength"}


@dataclass(slots=True)
class EnsembleScorerStrategy(Strategy):
    slug: str = "ensemble_scorer"
    default_params: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = dict(DEFAULT_PARAMS)

    def generate_signal(self, context: StrategyContext) -> Signal:
        timestamp = resolve_timestamp(context)
        try:
            params = _validated_params(dict(context.strategy_parameters))
        except StrategyParameterValidationError:
            return _hold(timestamp, "Invalid strategy parameters.", 0, 0, Decimal("0"), 0)

        component_signals = context.strategy_parameters.get("signals")
        if not isinstance(component_signals, list) or len(component_signals) == 0:
            return _hold(timestamp, "No component signals available.", 0, 0, Decimal("0"), 0)

        filter_signals = context.strategy_parameters.get("filter_signals", [])
        if isinstance(filter_signals, list):
            for filter_signal in filter_signals:
                action = _signal_attr(filter_signal, "action")
                strength = _coerce_strength(_signal_attr(filter_signal, "strength"))
                if action == "hold" and strength == Decimal("0"):
                    return _hold(timestamp, "Filter conditions suppressed ensemble signal.", 0, 0, Decimal("0"), len(component_signals))

        normalized = []
        for item in component_signals:
            action = _signal_attr(item, "action")
            strength = _coerce_strength(_signal_attr(item, "strength"))
            if action not in {"buy", "sell", "hold"} or strength is None:
                return _hold(timestamp, "Invalid component signals.", 0, 0, Decimal("0"), len(component_signals))
            normalized.append((action, strength))

        if params["conflict_resolution"] == "majority_vote":
            return _majority_vote(normalized, params["min_strategies_agreeing"], timestamp)
        return _net_strength(normalized, params["min_strategies_agreeing"], timestamp)


def _validated_params(params: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_PARAMS, **params}
    validate_strategy_params(
        merged,
        required_params=("min_strategies_agreeing", "conflict_resolution"),
        numeric_rules={"min_strategies_agreeing": NumericParamRule(minimum=Decimal("1"), integer_only=True)},
        enum_rules={"conflict_resolution": ("net_strength", "majority_vote")},
    )
    return {
        "min_strategies_agreeing": int(Decimal(str(merged["min_strategies_agreeing"]))),
        "conflict_resolution": str(merged["conflict_resolution"]),
    }


def _net_strength(normalized: list[tuple[str, Decimal]], minimum_agreement: int, timestamp) -> Signal:
    buy_count = sum(1 for action, _ in normalized if action == "buy")
    sell_count = sum(1 for action, _ in normalized if action == "sell")
    net = sum((strength if action == "buy" else -strength if action == "sell" else Decimal("0")) for action, strength in normalized)
    chosen_action = "buy" if net > 0 else "sell" if net < 0 else "hold"
    chosen_count = buy_count if chosen_action == "buy" else sell_count if chosen_action == "sell" else 0
    active_count = sum(1 for action, _ in normalized if action != "hold")
    indicators = {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "net_strength": str(net),
        "active_signal_count": active_count,
        "conflict_resolution": "net_strength",
    }
    if chosen_action == "hold" or chosen_count < minimum_agreement:
        return hold_signal(reason="Insufficient strategy agreement.", timestamp=timestamp, indicators=indicators)
    strength = min(Decimal("1.0"), abs(net) / Decimal(max(active_count, 1)))
    reason = "Ensemble net strength favored buy signals." if chosen_action == "buy" else "Ensemble net strength favored sell signals."
    return Signal(action=chosen_action, strength=strength, reason=reason, indicators=indicators, timestamp=timestamp)


def _majority_vote(normalized: list[tuple[str, Decimal]], minimum_agreement: int, timestamp) -> Signal:
    buy_count = sum(1 for action, _ in normalized if action == "buy")
    sell_count = sum(1 for action, _ in normalized if action == "sell")
    active_count = sum(1 for action, _ in normalized if action != "hold")
    indicators = {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "net_strength": str(sum((strength if action == "buy" else -strength if action == "sell" else Decimal("0")) for action, strength in normalized)),
        "active_signal_count": active_count,
        "conflict_resolution": "majority_vote",
    }
    if buy_count == sell_count or max(buy_count, sell_count) < minimum_agreement:
        return hold_signal(reason="Insufficient strategy agreement.", timestamp=timestamp, indicators=indicators)
    action = "buy" if buy_count > sell_count else "sell"
    strength = Decimal(max(buy_count, sell_count)) / Decimal(max(active_count, 1))
    reason = "Ensemble majority vote favored buy signals." if action == "buy" else "Ensemble majority vote favored sell signals."
    return Signal(action=action, strength=strength, reason=reason, indicators=indicators, timestamp=timestamp)


def _signal_attr(signal: Any, name: str) -> Any:
    if isinstance(signal, dict):
        return signal.get(name)
    return getattr(signal, name, None)


def _coerce_strength(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _hold(timestamp, reason: str, buy_count: int, sell_count: int, net_strength: Decimal, active_count: int) -> Signal:
    indicators = {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "net_strength": str(net_strength),
        "active_signal_count": active_count,
        "conflict_resolution": None,
    }
    return hold_signal(reason=reason, timestamp=timestamp, indicators=indicators)


def register_ensemble_scorer_strategy(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    if not registry.has("ensemble_scorer"):
        registry.register("ensemble_scorer", EnsembleScorerStrategy)
    return registry


register_ensemble_scorer_strategy()