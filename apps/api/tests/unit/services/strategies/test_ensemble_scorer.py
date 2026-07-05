from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.services.strategies.base import Signal, StrategyContext
from app.services.strategies.ensemble_scorer import EnsembleScorerStrategy


def build_context(params=None) -> StrategyContext:
    return StrategyContext(
        candles=[{"open_time": datetime(2026, 7, 1, tzinfo=timezone.utc), "close": 1}],
        asset_metadata={"symbol": "BTCUSDT"},
        interval="1h",
        current_position=None,
        strategy_parameters=params
        or {
            "min_strategies_agreeing": 1,
            "conflict_resolution": "net_strength",
            "signals": [
                Signal(action="buy", strength=Decimal("0.8"), reason="a", indicators={"x": 1}, timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc)),
                Signal(action="buy", strength=Decimal("0.6"), reason="b", indicators={"x": 1}, timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc)),
            ],
        },
    )


def test_ensemble_scorer_net_strength_buy() -> None:
    strategy = EnsembleScorerStrategy()
    signal = strategy.generate_signal(build_context())
    assert signal.action == "buy"


def test_ensemble_scorer_majority_vote_sell() -> None:
    strategy = EnsembleScorerStrategy()
    context = build_context({
        "min_strategies_agreeing": 1,
        "conflict_resolution": "majority_vote",
        "signals": [
            {"action": "sell", "strength": "0.8"},
            {"action": "sell", "strength": "0.6"},
            {"action": "buy", "strength": "0.2"},
        ],
    })
    signal = strategy.generate_signal(context)
    assert signal.action == "sell"


def test_ensemble_scorer_insufficient_agreement_hold() -> None:
    strategy = EnsembleScorerStrategy()
    context = build_context({
        "min_strategies_agreeing": 2,
        "conflict_resolution": "net_strength",
        "signals": [
            {"action": "buy", "strength": "0.8"},
            {"action": "sell", "strength": "0.2"},
        ],
    })
    signal = strategy.generate_signal(context)
    assert signal.action == "hold"


def test_ensemble_scorer_invalid_input() -> None:
    strategy = EnsembleScorerStrategy()
    signal = strategy.generate_signal(build_context({"min_strategies_agreeing": 1, "conflict_resolution": "net_strength", "signals": [{"action": "weird"}]}))
    assert signal.action == "hold"


def test_ensemble_scorer_deterministic() -> None:
    strategy = EnsembleScorerStrategy()
    context = build_context()
    assert strategy.generate_signal(context) == strategy.generate_signal(context)