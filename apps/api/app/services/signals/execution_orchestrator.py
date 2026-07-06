from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.services.data.http_client import AsyncHTTPClient
from app.services.paper.alpaca_paper import submit_alpaca_paper_order
from app.services.paper.internal_sim import execute_internal_crypto_fill


@dataclass(frozen=True, slots=True)
class SignalExecutionRequest:
    signal_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    actor: str = "system"
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalExecutionResult:
    signal_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    execution_status: str
    execution_venue: str
    is_paper: bool
    trade_id: uuid.UUID | None
    broker_order_id: str | None
    venue_status: str | None
    message: str


def _map_common_execution_status(*, venue: str, venue_status: str | None, filled_qty: Decimal | None) -> str:
    if venue == "internal_sim":
        return "executed"

    status = (venue_status or "").lower()
    filled = filled_qty or Decimal("0")

    if status in {"filled", "partially_filled"} and filled > 0:
        return "executed"
    if status in {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "pending_replace",
    }:
        return "pending"
    if status in {"canceled", "expired", "replaced", "rejected", "stopped", "suspended"}:
        return "failed"

    return "pending"


async def orchestrate_paper_signal_execution(
    *,
    db: AsyncSession,
    request: SignalExecutionRequest,
) -> SignalExecutionResult:
    if request.side not in {"buy", "sell"}:
        raise InvalidRequestError(message="Invalid side", details={"side": request.side})

    existing_trade = await db.scalar(
        select(Trade)
        .where(Trade.paper_account_id == request.paper_account_id)
        .where(Trade.signal_id == request.signal_id)
        .where(Trade.is_paper.is_(True))
        .order_by(Trade.executed_at.desc())
        .limit(1)
    )
    if existing_trade is not None:
        duplicate_audit = AuditLog(
            actor=request.actor,
            action="signal_execution_duplicate_skipped",
            entity_type="signal",
            entity_id=request.signal_id,
            before_state={
                "paper_account_id": str(request.paper_account_id),
                "asset_id": str(request.asset_id),
            },
            after_state={
                "trade_id": str(existing_trade.id),
                "execution_venue": existing_trade.execution_venue,
                "is_paper": existing_trade.is_paper,
            },
        )
        db.add(duplicate_audit)
        await db.commit()
        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status="duplicate",
            execution_venue=existing_trade.execution_venue,
            is_paper=True,
            trade_id=existing_trade.id,
            broker_order_id=None,
            venue_status="duplicate",
            message="Duplicate execution prevented for signal",
        )

    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == request.paper_account_id))
    if account is None:
        raise NotFoundError(
            message="Paper account not found",
            details={"paper_account_id": str(request.paper_account_id)},
        )

    asset = await db.scalar(select(Asset).where(Asset.id == request.asset_id))
    if asset is None:
        raise NotFoundError(message="Asset not found", details={"asset_id": str(request.asset_id)})

    if asset.asset_class == "crypto":
        if account.asset_class != "crypto":
            raise InvalidRequestError(
                message="Crypto signal cannot execute on non-crypto paper account",
                details={"paper_account_id": str(account.id), "asset_class": account.asset_class},
            )

        fill = await execute_internal_crypto_fill(
            db=db,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            side=request.side,
            quantity=request.quantity,
            actor=request.actor,
            signal_id=request.signal_id,
        )

        signal_audit = AuditLog(
            actor=request.actor,
            action="signal_execution_orchestrated",
            entity_type="signal",
            entity_id=request.signal_id,
            before_state={
                "asset_class": asset.asset_class,
                "paper_account_id": str(request.paper_account_id),
                "asset_id": str(request.asset_id),
                "quantity": format(request.quantity, "f"),
            },
            after_state={
                "execution_venue": "internal_sim",
                "execution_status": "executed",
                "trade_id": str(fill.trade_id),
                "is_paper": True,
            },
        )
        db.add(signal_audit)
        await db.commit()

        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status="executed",
            execution_venue="internal_sim",
            is_paper=True,
            trade_id=fill.trade_id,
            broker_order_id=None,
            venue_status="filled",
            message="Signal executed via internal crypto simulator",
        )

    if asset.asset_class == "stock":
        if account.asset_class != "stock":
            raise InvalidRequestError(
                message="Stock signal cannot execute on non-stock paper account",
                details={"paper_account_id": str(account.id), "asset_class": account.asset_class},
            )

        if asset.exchange != "alpaca":
            raise InvalidRequestError(
                message="Stock execution requires Alpaca exchange asset",
                details={"exchange": asset.exchange},
            )

        if not asset.supports_fractional and request.quantity != request.quantity.to_integral_value():
            raise InvalidRequestError(
                message="Asset does not support fractional quantity",
                details={"asset_id": str(asset.id), "quantity": format(request.quantity, "f")},
            )

        settings = get_settings()
        async with AsyncHTTPClient() as client:
            venue_result = await submit_alpaca_paper_order(
                settings=settings,
                client=client,
                symbol=asset.symbol,
                side=request.side,
                quantity=request.quantity,
                client_order_id=request.client_order_id,
            )

        trade_id: uuid.UUID | None = None
        if venue_result.filled_qty > 0 and venue_result.filled_avg_price is not None:
            trade = Trade(
                paper_account_id=request.paper_account_id,
                signal_id=request.signal_id,
                asset_id=request.asset_id,
                side=venue_result.side,
                quantity=venue_result.filled_qty,
                price=venue_result.filled_avg_price,
                fee=Decimal("0"),
                is_paper=True,
                execution_venue="alpaca_paper",
                executed_at=_parse_iso_timestamp(venue_result.filled_at) or datetime.now(timezone.utc),
            )
            db.add(trade)
            await db.commit()
            await db.refresh(trade)
            trade_id = trade.id

        mapped_status = _map_common_execution_status(
            venue="alpaca_paper",
            venue_status=venue_result.status,
            filled_qty=venue_result.filled_qty,
        )

        signal_audit = AuditLog(
            actor=request.actor,
            action="signal_execution_orchestrated",
            entity_type="signal",
            entity_id=request.signal_id,
            before_state={
                "asset_class": asset.asset_class,
                "paper_account_id": str(request.paper_account_id),
                "asset_id": str(request.asset_id),
                "quantity": format(request.quantity, "f"),
            },
            after_state={
                "execution_venue": "alpaca_paper",
                "execution_status": mapped_status,
                "trade_id": str(trade_id) if trade_id is not None else None,
                "broker_order_id": venue_result.broker_order_id,
                "venue_status": venue_result.status,
                "is_paper": True,
            },
        )
        db.add(signal_audit)
        await db.commit()

        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status=mapped_status,
            execution_venue="alpaca_paper",
            is_paper=True,
            trade_id=trade_id,
            broker_order_id=venue_result.broker_order_id,
            venue_status=venue_result.status,
            message="Signal submitted to Alpaca paper adapter",
        )

    raise InvalidRequestError(
        message="Unsupported asset class for paper execution orchestration",
        details={"asset_class": asset.asset_class},
    )


def _parse_iso_timestamp(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None

    value = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
