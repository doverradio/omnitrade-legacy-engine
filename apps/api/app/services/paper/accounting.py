from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.candle import Candle
from app.models.trade import Trade


@dataclass(frozen=True, slots=True)
class PositionAccounting:
    asset_id: uuid.UUID
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    position_value: Decimal
    unrealized_pnl_usd: Decimal
    unrealized_pnl_pct: Decimal


@dataclass(frozen=True, slots=True)
class AccountAccountingSnapshot:
    cash_balance: Decimal
    position_value: Decimal
    equity: Decimal
    equity_return_usd: Decimal
    equity_return_pct: Decimal
    positions: tuple[PositionAccounting, ...]


@dataclass(slots=True)
class _PositionState:
    quantity: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")


def _to_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def compute_account_snapshot(
    *,
    starting_balance: Decimal,
    trades: list[Trade],
    symbols_by_asset_id: dict[uuid.UUID, str],
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal],
) -> AccountAccountingSnapshot:
    cash = _to_decimal(starting_balance)
    positions: dict[uuid.UUID, _PositionState] = {}

    for trade in sorted(trades, key=lambda item: item.executed_at):
        asset_state = positions.setdefault(trade.asset_id, _PositionState())
        quantity = _to_decimal(trade.quantity)
        price = _to_decimal(trade.price)
        fee = _to_decimal(trade.fee)

        if trade.side == "buy":
            total_cost = (asset_state.quantity * asset_state.avg_entry_price) + (quantity * price) + fee
            new_quantity = asset_state.quantity + quantity
            asset_state.quantity = new_quantity
            asset_state.avg_entry_price = total_cost / new_quantity if new_quantity > 0 else Decimal("0")
            cash -= (quantity * price) + fee
            continue

        if trade.side == "sell":
            sell_quantity = min(asset_state.quantity, quantity)
            proceeds = (sell_quantity * price) - fee
            cash += proceeds
            remaining_quantity = asset_state.quantity - sell_quantity
            asset_state.quantity = remaining_quantity
            if remaining_quantity <= 0:
                asset_state.quantity = Decimal("0")
                asset_state.avg_entry_price = Decimal("0")

    position_rows: list[PositionAccounting] = []
    total_position_value = Decimal("0")

    for asset_id, state in positions.items():
        if state.quantity <= 0:
            continue

        latest_price = latest_prices_by_asset_id.get(asset_id, state.avg_entry_price)
        position_value = state.quantity * latest_price
        unrealized_usd = (latest_price - state.avg_entry_price) * state.quantity
        unrealized_pct = Decimal("0")
        if state.avg_entry_price > 0:
            unrealized_pct = (latest_price - state.avg_entry_price) / state.avg_entry_price

        total_position_value += position_value
        position_rows.append(
            PositionAccounting(
                asset_id=asset_id,
                symbol=symbols_by_asset_id.get(asset_id, "UNKNOWN"),
                quantity=state.quantity,
                avg_entry_price=state.avg_entry_price,
                position_value=position_value,
                unrealized_pnl_usd=unrealized_usd,
                unrealized_pnl_pct=unrealized_pct,
            )
        )

    equity = cash + total_position_value
    equity_return_usd = equity - starting_balance
    equity_return_pct = Decimal("0")
    if starting_balance > 0:
        equity_return_pct = equity_return_usd / starting_balance

    return AccountAccountingSnapshot(
        cash_balance=cash,
        position_value=total_position_value,
        equity=equity,
        equity_return_usd=equity_return_usd,
        equity_return_pct=equity_return_pct,
        positions=tuple(sorted(position_rows, key=lambda row: row.symbol)),
    )


async def build_account_snapshot(*, db: AsyncSession, paper_account_id: uuid.UUID, starting_balance: Decimal) -> AccountAccountingSnapshot:
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.paper_account_id == paper_account_id)
            .order_by(Trade.executed_at.asc())
        )
    ).scalars().all()

    symbols_by_asset_id: dict[uuid.UUID, str] = {}
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal] = {}

    asset_ids = sorted({trade.asset_id for trade in trades}, key=str)
    for asset_id in asset_ids:
        symbol = await db.scalar(select(Asset.symbol).where(Asset.id == asset_id))
        if isinstance(symbol, str):
            symbols_by_asset_id[asset_id] = symbol

        latest_close = await db.scalar(
            select(Candle.close)
            .where(Candle.asset_id == asset_id)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if isinstance(latest_close, Decimal):
            latest_prices_by_asset_id[asset_id] = latest_close

    return compute_account_snapshot(
        starting_balance=starting_balance,
        trades=trades,
        symbols_by_asset_id=symbols_by_asset_id,
        latest_prices_by_asset_id=latest_prices_by_asset_id,
    )
