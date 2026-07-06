from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.trade import Trade

BPS_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True, slots=True)
class InternalSimExecutionResult:
    trade_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    reference_price: Decimal
    executed_price: Decimal
    gross_value: Decimal
    fee_paid: Decimal
    slippage_cost: Decimal
    slippage_bps: Decimal
    fee_bps: Decimal
    execution_venue: str
    cash_before: Decimal
    cash_after: Decimal
    executed_at: datetime


def _to_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round_down_to_step(quantity: Decimal, step: Decimal | None) -> Decimal:
    if step is None or step <= 0:
        return quantity

    increments = (quantity / step).to_integral_value(rounding=ROUND_DOWN)
    return increments * step


async def execute_internal_crypto_fill(
    *,
    db: AsyncSession,
    paper_account_id: uuid.UUID,
    asset_id: uuid.UUID,
    side: str,
    quantity: Decimal,
    fee_bps: Decimal = Decimal("10"),
    slippage_bps: Decimal = Decimal("5"),
    actor: str = "system",
    executed_at: datetime | None = None,
) -> InternalSimExecutionResult:
    resolved_executed_at = executed_at or datetime.now(timezone.utc)

    if side not in {"buy", "sell"}:
        raise InvalidRequestError(message="Invalid side", details={"side": side})

    if quantity <= 0:
        raise InvalidRequestError(message="Quantity must be positive", details={"quantity": format(quantity, "f")})

    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id))
    if account is None:
        raise NotFoundError(message="Paper account not found", details={"paper_account_id": str(paper_account_id)})

    asset = await db.scalar(select(Asset).where(Asset.id == asset_id))
    if asset is None:
        raise NotFoundError(message="Asset not found", details={"asset_id": str(asset_id)})

    if asset.asset_class != "crypto":
        raise InvalidRequestError(
            message="Internal simulator supports crypto assets only",
            details={"asset_id": str(asset_id), "asset_class": asset.asset_class},
        )

    latest_close = await db.scalar(
        select(Candle.close)
        .where(Candle.asset_id == asset_id)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest_close is None:
        raise InvalidRequestError(
            message="No market data available for internal simulation",
            details={"asset_id": str(asset_id)},
        )

    reference_price = _to_decimal(latest_close)
    fee_rate = _to_decimal(fee_bps) / BPS_DENOMINATOR
    slippage_rate = _to_decimal(slippage_bps) / BPS_DENOMINATOR

    rounded_quantity = _round_down_to_step(_to_decimal(quantity), _to_decimal(asset.qty_step_size) if asset.qty_step_size is not None else None)
    if rounded_quantity <= 0:
        raise InvalidRequestError(
            message="Quantity is below exchange step size",
            details={"quantity": format(quantity, "f"), "qty_step_size": format(asset.qty_step_size, "f") if asset.qty_step_size is not None else None},
        )

    executed_price = reference_price * (Decimal("1") + slippage_rate if side == "buy" else Decimal("1") - slippage_rate)
    gross_value = rounded_quantity * executed_price
    fee_paid = gross_value * fee_rate
    slippage_cost = rounded_quantity * abs(executed_price - reference_price)

    if asset.min_order_notional is not None and gross_value < asset.min_order_notional:
        raise InvalidRequestError(
            message="Order notional below minimum",
            details={
                "gross_value": format(gross_value, "f"),
                "min_order_notional": format(asset.min_order_notional, "f"),
            },
        )

    cash_before = _to_decimal(account.current_cash_balance)
    if side == "buy":
        cash_delta = -(gross_value + fee_paid)
        if cash_before + cash_delta < 0:
            raise InvalidRequestError(
                message="Insufficient paper cash balance",
                details={
                    "cash_balance": format(cash_before, "f"),
                    "required": format(gross_value + fee_paid, "f"),
                },
            )
    else:
        held_quantity = await _get_position_quantity(db=db, paper_account_id=paper_account_id, asset_id=asset_id)
        if rounded_quantity > held_quantity:
            raise InvalidRequestError(
                message="Insufficient position quantity for sell",
                details={
                    "requested_quantity": format(rounded_quantity, "f"),
                    "held_quantity": format(held_quantity, "f"),
                },
            )
        cash_delta = gross_value - fee_paid

    cash_after = cash_before + cash_delta

    trade = Trade(
        paper_account_id=paper_account_id,
        asset_id=asset_id,
        side=side,
        quantity=rounded_quantity,
        price=executed_price,
        fee=fee_paid,
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=resolved_executed_at,
    )
    db.add(trade)

    account.current_cash_balance = cash_after

    audit_entry = AuditLog(
        actor=actor,
        action="paper_trade_simulated",
        entity_type="trade",
        entity_id=None,
        before_state={
            "paper_account_id": str(paper_account_id),
            "cash_balance": format(cash_before, "f"),
            "reference_price": format(reference_price, "f"),
        },
        after_state={
            "asset_id": str(asset_id),
            "side": side,
            "quantity": format(rounded_quantity, "f"),
            "executed_price": format(executed_price, "f"),
            "fee": format(fee_paid, "f"),
            "slippage_cost": format(slippage_cost, "f"),
            "slippage_bps": format(_to_decimal(slippage_bps), "f"),
            "fee_bps": format(_to_decimal(fee_bps), "f"),
            "execution_venue": "internal_sim",
            "is_paper": True,
            "cash_balance": format(cash_after, "f"),
        },
    )
    db.add(audit_entry)

    await db.commit()
    await db.refresh(trade)

    audit_entry.entity_id = trade.id
    await db.commit()

    return InternalSimExecutionResult(
        trade_id=trade.id,
        paper_account_id=paper_account_id,
        asset_id=asset_id,
        side=side,
        quantity=rounded_quantity,
        reference_price=reference_price,
        executed_price=executed_price,
        gross_value=gross_value,
        fee_paid=fee_paid,
        slippage_cost=slippage_cost,
        slippage_bps=_to_decimal(slippage_bps),
        fee_bps=_to_decimal(fee_bps),
        execution_venue="internal_sim",
        cash_before=cash_before,
        cash_after=cash_after,
        executed_at=resolved_executed_at,
    )


async def _get_position_quantity(*, db: AsyncSession, paper_account_id: uuid.UUID, asset_id: uuid.UUID) -> Decimal:
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.paper_account_id == paper_account_id)
            .where(Trade.asset_id == asset_id)
            .order_by(Trade.executed_at.asc())
        )
    ).scalars().all()

    quantity = Decimal("0")
    for trade in trades:
        trade_quantity = _to_decimal(trade.quantity)
        if trade.side == "buy":
            quantity += trade_quantity
        elif trade.side == "sell":
            quantity -= trade_quantity

    return max(Decimal("0"), quantity)
