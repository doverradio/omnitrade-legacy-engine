from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext

from app.services.backtesting.engine import BacktestResult, BacktestTrade, EquitySnapshot


FEE_DRAG_WARNING_THRESHOLD = Decimal("0.20")
ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class SmallAccountWarning:
    type: str
    detail: str


@dataclass(frozen=True, slots=True)
class EquityCurvePoint:
    timestamp: object
    cash: Decimal
    position_quantity: Decimal
    average_cost_basis: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_return_usd: Decimal
    total_return_pct: Decimal
    trade_count: int
    win_rate: Decimal
    average_trade_usd: Decimal
    max_drawdown: Decimal
    sharpe_like: Decimal
    fee_drag_pct: Decimal
    equity_curve: tuple[EquityCurvePoint, ...]
    small_account_warning: SmallAccountWarning | None


def build_equity_curve_data(equity_curve: tuple[EquitySnapshot, ...] | list[EquitySnapshot]) -> tuple[EquityCurvePoint, ...]:
    return tuple(
        EquityCurvePoint(
            timestamp=snapshot.timestamp,
            cash=snapshot.cash,
            position_quantity=snapshot.position_quantity,
            average_cost_basis=snapshot.average_cost_basis,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            total_equity=snapshot.total_equity,
        )
        for snapshot in equity_curve
    )


def compute_backtest_metrics(
    result: BacktestResult,
    *,
    total_fees: Decimal | str | int = ZERO,
    total_slippage: Decimal | str | int = ZERO,
) -> BacktestMetrics:
    fees = Decimal(str(total_fees))
    slippage = Decimal(str(total_slippage))
    total_return_usd = result.total_equity - result.initial_capital
    total_return_pct = ZERO if result.initial_capital == 0 else total_return_usd / result.initial_capital

    trade_pnls = _pair_trade_pnls(result.trades)
    trade_count = len(trade_pnls)
    win_rate = ZERO if trade_count == 0 else Decimal(sum(1 for pnl in trade_pnls if pnl > 0)) / Decimal(trade_count)
    average_trade_usd = ZERO if trade_count == 0 else sum(trade_pnls, start=ZERO) / Decimal(trade_count)
    max_drawdown = _compute_max_drawdown(result.equity_curve)
    sharpe_like = _compute_sharpe_like(result.equity_curve)

    gross_gains = sum((pnl for pnl in trade_pnls if pnl > 0), start=ZERO)
    total_costs = fees + slippage
    fee_drag_pct = ZERO if gross_gains <= 0 else total_costs / gross_gains
    warning = _build_small_account_warning(fee_drag_pct)

    return BacktestMetrics(
        total_return_usd=total_return_usd,
        total_return_pct=total_return_pct,
        trade_count=trade_count,
        win_rate=win_rate,
        average_trade_usd=average_trade_usd,
        max_drawdown=max_drawdown,
        sharpe_like=sharpe_like,
        fee_drag_pct=fee_drag_pct,
        equity_curve=build_equity_curve_data(result.equity_curve),
        small_account_warning=warning,
    )


def _pair_trade_pnls(trades: tuple[BacktestTrade, ...]) -> list[Decimal]:
    pnls: list[Decimal] = []
    open_buy: BacktestTrade | None = None
    for trade in trades:
        if trade.side == "buy":
            open_buy = trade
            continue
        if trade.side == "sell" and open_buy is not None:
            quantity = min(open_buy.quantity, trade.quantity)
            pnls.append((trade.price - open_buy.price) * quantity)
            open_buy = None
    return pnls


def _compute_max_drawdown(equity_curve: tuple[EquitySnapshot, ...]) -> Decimal:
    if not equity_curve:
        return ZERO
    peak = equity_curve[0].total_equity
    max_drawdown = ZERO
    for snapshot in equity_curve:
        if snapshot.total_equity > peak:
            peak = snapshot.total_equity
        if peak > 0:
            drawdown = (peak - snapshot.total_equity) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
    return max_drawdown


def _compute_sharpe_like(equity_curve: tuple[EquitySnapshot, ...]) -> Decimal:
    if len(equity_curve) < 2:
        return ZERO
    returns: list[Decimal] = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous.total_equity == 0:
            returns.append(ZERO)
        else:
            returns.append((current.total_equity - previous.total_equity) / previous.total_equity)
    if not returns:
        return ZERO

    average_return = sum(returns, start=ZERO) / Decimal(len(returns))
    variance = sum((entry - average_return) ** 2 for entry in returns) / Decimal(len(returns))
    if variance == 0:
        return ZERO

    std_dev = variance.sqrt(context=getcontext())
    if std_dev == 0:
        return ZERO
    return average_return / std_dev


def _build_small_account_warning(fee_drag_pct: Decimal) -> SmallAccountWarning | None:
    if fee_drag_pct > FEE_DRAG_WARNING_THRESHOLD:
        percentage = (fee_drag_pct * Decimal("100")).quantize(Decimal("0.01"))
        return SmallAccountWarning(
            type="high_fee_drag",
            detail=f"Fees consumed {percentage}% of gross backtest gains at this starting balance.",
        )
    return None