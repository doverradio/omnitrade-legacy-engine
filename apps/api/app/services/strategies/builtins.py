from __future__ import annotations

from app.services.strategies.breakout import register_breakout_strategy
from app.services.strategies.ensemble_scorer import register_ensemble_scorer_strategy
from app.services.strategies.ma_crossover import register_ma_crossover_strategy
from app.services.strategies.registry import StrategyRegistry, strategy_registry
from app.services.strategies.rsi_mean_reversion import register_rsi_mean_reversion_strategy
from app.services.strategies.trend_regime_filter import register_trend_regime_filter_strategy
from app.services.strategies.volatility_filter import register_volatility_filter_strategy


def register_builtin_strategies(registry: StrategyRegistry = strategy_registry) -> StrategyRegistry:
    register_ma_crossover_strategy(registry)
    register_rsi_mean_reversion_strategy(registry)
    register_breakout_strategy(registry)
    register_volatility_filter_strategy(registry)
    register_trend_regime_filter_strategy(registry)
    register_ensemble_scorer_strategy(registry)
    return registry


register_builtin_strategies()