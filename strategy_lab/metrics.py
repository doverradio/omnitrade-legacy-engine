"""Performance statistics computed from a completed SimulationResult.

All money/price figures stay in Decimal. Sharpe/Sortino are computed as
simple PER-TRADE (non-annualized) ratios -- annualizing would require an
assumption about trade frequency this simulator has no basis for, so rather
than fabricate one, these are reported plainly for what they are.

Maximum Favorable/Adverse Excursion are reported as the average across
trades of each trade's own MFE/MAE (per-trade values are also in the CSV
trade log). MFE/MAE are computed from candle highs/lows seen DURING a trade
purely for reporting -- they are never used as decision inputs.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from .engine import SimulationResult
from .strategy import TradeRecord

_ZERO = Decimal("0")


@dataclass(frozen=True)
class SimulationMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Optional[Decimal]
    gross_return_pct: Decimal
    net_return_pct: Decimal
    fees_paid: Decimal
    estimated_slippage: Decimal
    average_trade_pct: Optional[Decimal]
    average_holding_candles: Optional[Decimal]
    max_drawdown_pct: Decimal
    max_consecutive_losses: int
    largest_winner_pct: Optional[Decimal]
    largest_loser_pct: Optional[Decimal]
    average_mfe_pct: Optional[Decimal]
    average_mae_pct: Optional[Decimal]
    profit_factor: Optional[Decimal]
    sharpe_ratio_per_trade: Optional[float]
    sortino_ratio_per_trade: Optional[float]


def compute_metrics(result: SimulationResult) -> SimulationMetrics:
    trades = result.trades
    total_trades = len(trades)
    winning = [t for t in trades if t.net_return_pct > 0]
    losing = [t for t in trades if t.net_return_pct <= 0]

    win_rate_pct = (
        (Decimal(len(winning)) / Decimal(total_trades)) * Decimal("100") if total_trades else None
    )

    gross_return_pct = _compound(t.gross_return_pct for t in trades) * Decimal("100")
    net_return_pct = (
        (result.final_equity / result.initial_capital) - Decimal("1")
    ) * Decimal("100")

    fees_paid = sum((t.entry_fee + t.exit_fee for t in trades), _ZERO)
    estimated_slippage = sum((t.entry_slippage_cost + t.exit_slippage_cost for t in trades), _ZERO)

    average_trade_pct = (
        (sum((t.net_return_pct for t in trades), _ZERO) / Decimal(total_trades)) * Decimal("100")
        if total_trades
        else None
    )
    average_holding_candles = (
        Decimal(sum(t.holding_candles for t in trades)) / Decimal(total_trades)
        if total_trades
        else None
    )

    max_drawdown_pct = _max_drawdown_pct(result.equity_curve, result.initial_capital)
    max_consecutive_losses = _max_consecutive_losses(trades)

    largest_winner_pct = max((t.net_return_pct for t in trades), default=None)
    if largest_winner_pct is not None:
        largest_winner_pct *= Decimal("100")
    largest_loser_pct = min((t.net_return_pct for t in trades), default=None)
    if largest_loser_pct is not None:
        largest_loser_pct *= Decimal("100")

    average_mfe_pct = _average_pct(
        ((t.highest_price_during_trade - t.raw_fill_price) / t.raw_fill_price for t in trades),
        total_trades,
    )
    average_mae_pct = _average_pct(
        ((t.lowest_price_during_trade - t.raw_fill_price) / t.raw_fill_price for t in trades),
        total_trades,
    )

    profit_factor = _profit_factor(trades)

    net_returns = [float(t.net_return_pct) for t in trades]
    sharpe_ratio_per_trade = _sharpe(net_returns)
    sortino_ratio_per_trade = _sortino(net_returns)

    return SimulationMetrics(
        total_trades=total_trades,
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate_pct=win_rate_pct,
        gross_return_pct=gross_return_pct,
        net_return_pct=net_return_pct,
        fees_paid=fees_paid,
        estimated_slippage=estimated_slippage,
        average_trade_pct=average_trade_pct,
        average_holding_candles=average_holding_candles,
        max_drawdown_pct=max_drawdown_pct,
        max_consecutive_losses=max_consecutive_losses,
        largest_winner_pct=largest_winner_pct,
        largest_loser_pct=largest_loser_pct,
        average_mfe_pct=average_mfe_pct,
        average_mae_pct=average_mae_pct,
        profit_factor=profit_factor,
        sharpe_ratio_per_trade=sharpe_ratio_per_trade,
        sortino_ratio_per_trade=sortino_ratio_per_trade,
    )


def _compound(pct_values) -> Decimal:
    total = Decimal("1")
    for value in pct_values:
        total *= Decimal("1") + value
    return total - Decimal("1")


def _average_pct(values, total_trades: int) -> Optional[Decimal]:
    if not total_trades:
        return None
    total = sum(values, _ZERO)
    return (total / Decimal(total_trades)) * Decimal("100")


def _max_drawdown_pct(equity_curve: Sequence[Decimal], initial_capital: Decimal) -> Decimal:
    if not equity_curve:
        return _ZERO
    peak = initial_capital
    max_dd = _ZERO
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown > max_dd:
                max_dd = drawdown
    return max_dd * Decimal("100")


def _max_consecutive_losses(trades: Sequence[TradeRecord]) -> int:
    longest = 0
    current = 0
    for trade in trades:
        if trade.net_return_pct <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _profit_factor(trades: Sequence[TradeRecord]) -> Optional[Decimal]:
    gains = sum((t.equity_after - t.equity_before for t in trades if t.equity_after > t.equity_before), _ZERO)
    losses = sum((t.equity_before - t.equity_after for t in trades if t.equity_after < t.equity_before), _ZERO)
    if losses == 0:
        return None
    return gains / losses


def _sharpe(returns: List[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    return statistics.mean(returns) / stdev


def _sortino(returns: List[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    downside = [r for r in returns if r < 0]
    if len(downside) < 2:
        return None
    downside_stdev = statistics.stdev(downside)
    if downside_stdev == 0:
        return None
    return statistics.mean(returns) / downside_stdev
