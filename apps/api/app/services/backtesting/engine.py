from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.strategies.base import Strategy, StrategyContext, coerce_decimal


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    side: str
    quantity: Decimal
    price: Decimal
    executed_at: Any
    reason: str


@dataclass(frozen=True, slots=True)
class EquitySnapshot:
    timestamp: Any
    cash: Decimal
    position_quantity: Decimal
    average_cost_basis: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_capital: Decimal
    cash: Decimal
    position_quantity: Decimal
    average_cost_basis: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquitySnapshot, ...]


@dataclass(slots=True)
class _PositionState:
    quantity: Decimal = Decimal("0")
    average_cost_basis: Decimal | None = None


class BacktestEngine:
    def __init__(
        self,
        *,
        strategy: Strategy,
        asset_metadata: dict[str, Any],
        interval: str,
        strategy_parameters: dict[str, Any],
        initial_capital: Decimal | str | int,
    ) -> None:
        capital = Decimal(str(initial_capital))
        if capital < Decimal("25"):
            raise ValueError("initial_capital must be >= 25.")

        self._strategy = strategy
        self._asset_metadata = dict(asset_metadata)
        self._interval = interval
        self._strategy_parameters = dict(strategy_parameters)
        self._initial_capital = capital

    def run(self, candles: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> BacktestResult:
        cash = self._initial_capital
        realized_pnl = Decimal("0")
        position = _PositionState()
        trades: list[BacktestTrade] = []
        equity_curve: list[EquitySnapshot] = []

        for index, candle in enumerate(candles):
            price = coerce_decimal(candle.get("close")) if isinstance(candle, dict) else None
            if price is None:
                raise ValueError("Each candle must include a valid close price.")

            current_position = None
            if position.quantity > 0 and position.average_cost_basis is not None:
                current_position = {
                    "quantity": str(position.quantity),
                    "average_cost_basis": str(position.average_cost_basis),
                }

            context = StrategyContext(
                candles=list(candles[: index + 1]),
                asset_metadata=self._asset_metadata,
                interval=self._interval,
                current_position=current_position,
                strategy_parameters=self._strategy_parameters,
            )
            signal = self._strategy.generate_signal(context)

            if signal.action == "buy" and cash > Decimal("0") and position.quantity == Decimal("0"):
                quantity = cash / price
                position.quantity = quantity
                position.average_cost_basis = price
                cash = Decimal("0")
                trades.append(
                    BacktestTrade(
                        side="buy",
                        quantity=quantity,
                        price=price,
                        executed_at=candle.get("open_time") or candle.get("timestamp"),
                        reason=signal.reason,
                    )
                )
            elif signal.action == "sell" and position.quantity > Decimal("0") and position.average_cost_basis is not None:
                cash_from_sale = position.quantity * price
                realized_pnl += (price - position.average_cost_basis) * position.quantity
                trades.append(
                    BacktestTrade(
                        side="sell",
                        quantity=position.quantity,
                        price=price,
                        executed_at=candle.get("open_time") or candle.get("timestamp"),
                        reason=signal.reason,
                    )
                )
                cash += cash_from_sale
                position = _PositionState()

            unrealized_pnl = Decimal("0")
            if position.quantity > Decimal("0") and position.average_cost_basis is not None:
                unrealized_pnl = (price - position.average_cost_basis) * position.quantity
            total_equity = cash + (position.quantity * price)

            equity_curve.append(
                EquitySnapshot(
                    timestamp=candle.get("open_time") or candle.get("timestamp"),
                    cash=cash,
                    position_quantity=position.quantity,
                    average_cost_basis=position.average_cost_basis,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    total_equity=total_equity,
                )
            )

        final_unrealized = equity_curve[-1].unrealized_pnl if equity_curve else Decimal("0")
        final_equity = equity_curve[-1].total_equity if equity_curve else cash

        return BacktestResult(
            initial_capital=self._initial_capital,
            cash=cash,
            position_quantity=position.quantity,
            average_cost_basis=position.average_cost_basis,
            realized_pnl=realized_pnl,
            unrealized_pnl=final_unrealized,
            total_equity=final_equity,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
        )