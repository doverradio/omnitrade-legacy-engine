from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.backtesting.engine import BacktestResult, BacktestTrade, EquitySnapshot
from app.services.backtesting.metrics import build_equity_curve_data, compute_backtest_metrics


def build_result() -> BacktestResult:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    equity_curve = (
        EquitySnapshot(start, Decimal("0"), Decimal("10"), Decimal("10"), Decimal("0"), Decimal("0"), Decimal("100")),
        EquitySnapshot(start + timedelta(hours=1), Decimal("0"), Decimal("10"), Decimal("10"), Decimal("0"), Decimal("20"), Decimal("120")),
        EquitySnapshot(start + timedelta(hours=2), Decimal("120"), Decimal("0"), None, Decimal("20"), Decimal("0"), Decimal("120")),
        EquitySnapshot(start + timedelta(hours=3), Decimal("120"), Decimal("0"), None, Decimal("20"), Decimal("0"), Decimal("110")),
    )
    trades = (
        BacktestTrade("buy", Decimal("10"), Decimal("10"), start, "buy"),
        BacktestTrade("sell", Decimal("10"), Decimal("12"), start + timedelta(hours=2), "sell"),
    )
    return BacktestResult(
        initial_capital=Decimal("100"),
        cash=Decimal("120"),
        position_quantity=Decimal("0"),
        average_cost_basis=None,
        realized_pnl=Decimal("20"),
        unrealized_pnl=Decimal("0"),
        total_equity=Decimal("120"),
        trades=trades,
        equity_curve=equity_curve,
    )


def test_compute_metrics_total_return() -> None:
    metrics = compute_backtest_metrics(build_result())
    assert metrics.total_return_usd == Decimal("20")
    assert metrics.total_return_pct == Decimal("0.2")


def test_compute_metrics_drawdown() -> None:
    metrics = compute_backtest_metrics(build_result())
    assert metrics.max_drawdown == Decimal("0.08333333333333333333333333333")


def test_compute_metrics_win_rate() -> None:
    metrics = compute_backtest_metrics(build_result())
    assert metrics.win_rate == Decimal("1")


def test_compute_metrics_average_trade() -> None:
    metrics = compute_backtest_metrics(build_result())
    assert metrics.average_trade_usd == Decimal("20")


def test_compute_metrics_fee_drag() -> None:
    metrics = compute_backtest_metrics(build_result(), total_fees=Decimal("2"))
    assert metrics.fee_drag_pct == Decimal("0.1")


def test_build_equity_curve_data() -> None:
    result = build_result()
    equity_curve = build_equity_curve_data(result.equity_curve)
    assert len(equity_curve) == 4
    assert equity_curve[-1].total_equity == Decimal("110")


def test_compute_metrics_small_account_warning() -> None:
    metrics = compute_backtest_metrics(build_result(), total_fees=Decimal("5"), total_slippage=Decimal("1"))
    assert metrics.small_account_warning is not None
    assert metrics.small_account_warning.type == "high_fee_drag"