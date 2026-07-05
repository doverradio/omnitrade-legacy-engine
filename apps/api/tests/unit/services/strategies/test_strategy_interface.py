from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.strategies.base import Signal, Strategy, StrategyContext


class ExampleStrategy:
    slug = "example"
    default_params = {"window": 5}

    def generate_signal(self, context: StrategyContext) -> Signal:
        return Signal(
            action="hold",
            strength=Decimal("0.5"),
            reason="Waiting for confirmation.",
            indicators={"window": context.strategy_parameters["window"]},
            timestamp=datetime(2026, 7, 5, tzinfo=timezone.utc),
        )


def build_context() -> StrategyContext:
    return StrategyContext(
        candles=[{"open": "1", "close": "2"}],
        asset_metadata={"symbol": "BTCUSDT", "asset_class": "crypto"},
        interval="1h",
        current_position={"quantity": "0.1"},
        strategy_parameters={"window": 5},
    )


def test_strategy_protocol_behavior() -> None:
    strategy = ExampleStrategy()

    assert isinstance(strategy, Strategy)
    signal = strategy.generate_signal(build_context())
    assert signal.action == "hold"


def test_strategy_context_is_immutable_to_strategies() -> None:
    context = build_context()

    with pytest.raises(FrozenInstanceError):
        context.interval = "15m"  # type: ignore[misc]

    with pytest.raises(TypeError):
        context.asset_metadata["symbol"] = "ETHUSDT"  # type: ignore[index]

    with pytest.raises(TypeError):
        context.candles[0]["open"] = "5"  # type: ignore[index]