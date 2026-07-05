from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.backtesting.engine import BacktestEngine
from app.services.strategies.base import Signal, Strategy, StrategyContext


@dataclass(slots=True)
class StubStrategy(Strategy):
    slug: str
    default_params: dict[str, str]
    actions_by_index: dict[int, str]

    def generate_signal(self, context: StrategyContext) -> Signal:
        index = len(context.candles) - 1
        action = self.actions_by_index.get(index, "hold")
        return Signal(
            action=action,
            strength=Decimal("1.0") if action != "hold" else Decimal("0.0"),
            reason=f"stub-{action}",
            indicators={"index": index},
            timestamp=context.candles[-1]["open_time"],
        )


def build_candles(closes: list[int | str]) -> list[dict[str, object]]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = []
    for index, close in enumerate(closes):
        candles.append(
            {
                "open_time": start + timedelta(hours=index),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
            }
        )
    return candles


def build_engine(actions_by_index: dict[int, str], initial_capital: str = "100") -> BacktestEngine:
    return BacktestEngine(
        strategy=StubStrategy(slug="stub", default_params={}, actions_by_index=actions_by_index),
        asset_metadata={"symbol": "BTCUSDT", "asset_class": "crypto"},
        interval="1h",
        strategy_parameters={},
        initial_capital=Decimal(initial_capital),
    )


def test_backtest_engine_no_trades() -> None:
    result = build_engine({}).run(build_candles([10, 11, 12]))

    assert result.trades == ()
    assert result.cash == Decimal("100")
    assert result.total_equity == Decimal("100")


def test_backtest_engine_single_profitable_trade() -> None:
    result = build_engine({0: "buy", 2: "sell"}).run(build_candles([10, 12, 15]))

    assert len(result.trades) == 2
    assert result.realized_pnl == Decimal("50")
    assert result.total_equity == Decimal("150")


def test_backtest_engine_single_losing_trade() -> None:
    result = build_engine({0: "buy", 2: "sell"}).run(build_candles([10, 9, 8]))

    assert result.realized_pnl == Decimal("-20")
    assert result.total_equity == Decimal("80")


def test_backtest_engine_multiple_trades() -> None:
    result = build_engine({0: "buy", 1: "sell", 2: "buy", 3: "sell"}).run(build_candles([10, 12, 6, 9]))

    assert len(result.trades) == 4
    assert result.realized_pnl == Decimal("80")
    assert result.total_equity == Decimal("180")


def test_backtest_engine_buy_and_hold() -> None:
    result = build_engine({0: "buy"}).run(build_candles([10, 12, 15]))

    assert len(result.trades) == 1
    assert result.position_quantity == Decimal("10")
    assert result.realized_pnl == Decimal("0")
    assert result.unrealized_pnl == Decimal("50")
    assert result.total_equity == Decimal("150")


def test_backtest_engine_empty_candles() -> None:
    result = build_engine({}).run([])

    assert result.trades == ()
    assert result.equity_curve == ()
    assert result.total_equity == Decimal("100")


def test_backtest_engine_insufficient_history_still_runs() -> None:
    result = build_engine({}).run(build_candles([10]))

    assert len(result.equity_curve) == 1
    assert result.total_equity == Decimal("100")


def test_backtest_engine_deterministic_repeatability() -> None:
    engine = build_engine({0: "buy", 2: "sell", 3: "buy"})
    candles = build_candles([10, 11, 12, 13])

    first = engine.run(candles)
    second = engine.run(candles)

    assert first == second


def test_backtest_engine_enforces_minimum_starting_capital() -> None:
    try:
        build_engine({}, initial_capital="24")
    except ValueError as exc:
        assert str(exc) == "initial_capital must be >= 25."
    else:
        raise AssertionError("Expected initial_capital validation error")