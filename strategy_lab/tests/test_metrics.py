from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from strategy_lab.engine import SimulationResult
from strategy_lab.metrics import compute_metrics
from strategy_lab.strategy import ExitReason, TradeRecord

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _trade(
    index: int,
    equity_before,
    equity_after,
    gross_return_pct,
    raw_fill_price,
    raw_exit_price,
    highest,
    lowest,
    holding_candles: int,
    entry_fee=Decimal("0"),
    exit_fee=Decimal("0"),
    entry_slippage_cost=Decimal("0"),
    exit_slippage_cost=Decimal("0"),
) -> TradeRecord:
    equity_before = Decimal(str(equity_before))
    equity_after = Decimal(str(equity_after))
    raw_fill_price = Decimal(str(raw_fill_price))
    raw_exit_price = Decimal(str(raw_exit_price))
    return TradeRecord(
        entry_candle_index=index,
        entry_timestamp=_EPOCH + timedelta(hours=index),
        entry_order_price=raw_fill_price,
        raw_fill_price=raw_fill_price,
        effective_entry_price=raw_fill_price,
        entry_fee=Decimal(str(entry_fee)),
        entry_slippage_cost=Decimal(str(entry_slippage_cost)),
        exit_candle_index=index + holding_candles,
        exit_timestamp=_EPOCH + timedelta(hours=index + holding_candles),
        raw_exit_price=raw_exit_price,
        effective_exit_price=raw_exit_price,
        exit_fee=Decimal(str(exit_fee)),
        exit_slippage_cost=Decimal(str(exit_slippage_cost)),
        exit_reason=ExitReason.DECLINING_CLOSES,
        quantity=equity_before / raw_fill_price,
        equity_before=equity_before,
        equity_after=equity_after,
        gross_return_pct=Decimal(str(gross_return_pct)),
        net_return_pct=(equity_after / equity_before) - Decimal("1"),
        highest_price_during_trade=Decimal(str(highest)),
        lowest_price_during_trade=Decimal(str(lowest)),
        holding_candles=holding_candles,
    )


def _result(trades, equity_curve, initial_capital="10000"):
    equity_curve = [Decimal(str(v)) for v in equity_curve]
    return SimulationResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=Decimal(initial_capital),
        final_equity=equity_curve[-1] if equity_curve else Decimal(initial_capital),
        ended_in_position=False,
    )


def test_metrics_on_three_trades_two_losses_one_win():
    trades = [
        _trade(0, 10000, 10500, "0.05", 100, 105, 110, 99, holding_candles=2,
               entry_fee=10, exit_fee=10, entry_slippage_cost=2, exit_slippage_cost=2),
        _trade(3, 10500, 10290, "-0.02", 105, 102.9, 106, 101, holding_candles=1,
               entry_fee=5, exit_fee=5, entry_slippage_cost=1, exit_slippage_cost=1),
        _trade(5, 10290, 10084.2, "-0.02", 102.9, 100.842, 103, 100, holding_candles=3,
               entry_fee=5, exit_fee=5, entry_slippage_cost=1, exit_slippage_cost=1),
    ]
    result = _result(trades, equity_curve=[10000, 12000, 9000], initial_capital="10000")

    metrics = compute_metrics(result)

    assert metrics.total_trades == 3
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 2
    assert float(metrics.win_rate_pct) == pytest.approx(100 / 3)
    assert metrics.fees_paid == Decimal("40")
    assert metrics.estimated_slippage == Decimal("8")
    assert metrics.max_consecutive_losses == 2
    assert metrics.max_drawdown_pct == Decimal("25.00")  # (12000-9000)/12000
    assert metrics.largest_winner_pct == Decimal("5.00")  # 0.05 * 100 for trade 1's net return
    assert float(metrics.largest_loser_pct) == pytest.approx(-2.0)
    expected_profit_factor = Decimal("500") / (Decimal("210") + Decimal("205.8"))
    assert metrics.profit_factor == expected_profit_factor
    assert metrics.sortino_ratio_per_trade is None  # both losses are identical -> zero downside stdev

    net_returns = [0.05, -0.02, -0.02]
    expected_sharpe = statistics.mean(net_returns) / statistics.stdev(net_returns)
    assert metrics.sharpe_ratio_per_trade == pytest.approx(expected_sharpe)


def test_metrics_with_no_trades_are_all_none_or_zero():
    result = _result([], equity_curve=[10000, 10000], initial_capital="10000")

    metrics = compute_metrics(result)

    assert metrics.total_trades == 0
    assert metrics.win_rate_pct is None
    assert metrics.average_trade_pct is None
    assert metrics.average_holding_candles is None
    assert metrics.largest_winner_pct is None
    assert metrics.largest_loser_pct is None
    assert metrics.profit_factor is None
    assert metrics.sharpe_ratio_per_trade is None
    assert metrics.sortino_ratio_per_trade is None
    assert metrics.fees_paid == Decimal("0")
    assert metrics.max_drawdown_pct == Decimal("0")


def test_sharpe_and_sortino_require_at_least_two_trades():
    trades = [_trade(0, 10000, 10500, "0.05", 100, 105, 110, 99, holding_candles=2)]
    result = _result(trades, equity_curve=[10000, 10500], initial_capital="10000")

    metrics = compute_metrics(result)

    assert metrics.sharpe_ratio_per_trade is None
    assert metrics.sortino_ratio_per_trade is None
