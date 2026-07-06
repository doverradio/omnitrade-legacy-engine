from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.schemas.paper import (
    AlpacaPaperOrderResponse,
    CreatePaperAccountRequest,
    CreatePaperAccountResponse,
    PaperAccountResponse,
    PositionResponse,
    ResetPaperAccountRequest,
    ResetPaperAccountResponse,
    SubmitAlpacaPaperOrderRequest,
)
from app.services.data.http_client import AsyncHTTPClient
from app.services.paper.accounting import build_account_snapshot
from app.services.paper.alpaca_paper import get_alpaca_paper_order, submit_alpaca_paper_order

router = APIRouter(prefix="/paper", tags=["paper"])
logger = logging.getLogger(__name__)

_DEFAULT_OWNER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaperAccountResponse:
    account = await _load_account(db=db, account_id=account_id)

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )

    return PaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=snapshot.cash_balance,
        equity=snapshot.equity,
        equity_return_usd=snapshot.equity_return_usd,
        equity_return_pct=snapshot.equity_return_pct,
        positions=[
            PositionResponse(
                asset_id=position.asset_id,
                symbol=position.symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                unrealized_pnl_usd=position.unrealized_pnl_usd,
                unrealized_pnl_pct=position.unrealized_pnl_pct,
            )
            for position in snapshot.positions
        ],
    )


@router.post("/account", response_model=CreatePaperAccountResponse, status_code=201)
async def create_paper_account(
    payload: CreatePaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> CreatePaperAccountResponse:
    if payload.asset_class not in {"crypto", "stock"}:
        raise InvalidRequestError(message="Invalid asset_class", details={"asset_class": payload.asset_class})

    if payload.starting_balance < Decimal("25"):
        raise InvalidRequestError(
            message="starting_balance must be at least 25",
            details={"starting_balance": format(payload.starting_balance, "f")},
        )

    account = PaperAccount(
        owner_user_id=_DEFAULT_OWNER_USER_ID,
        name=payload.name.strip(),
        asset_class=payload.asset_class,
        starting_balance=payload.starting_balance,
        current_cash_balance=payload.starting_balance,
        is_active=True,
    )

    db.add(account)
    await db.commit()
    await db.refresh(account)

    return CreatePaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=account.current_cash_balance,
        is_active=account.is_active,
    )


@router.post("/reset", response_model=ResetPaperAccountResponse)
async def reset_paper_account(
    payload: ResetPaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> ResetPaperAccountResponse:
    if payload.confirm is not True:
        raise InvalidRequestError(message="Reset requires confirm=true", details={"confirm": payload.confirm})

    account = await _load_account(db=db, account_id=payload.account_id)

    await db.execute(delete(Trade).where(Trade.paper_account_id == account.id))
    account.current_cash_balance = account.starting_balance
    await db.commit()

    return ResetPaperAccountResponse(
        account_id=account.id,
        current_cash_balance=account.current_cash_balance,
        positions=[],
    )


@router.post("/orders/alpaca", response_model=AlpacaPaperOrderResponse, status_code=201)
async def submit_stock_paper_order(
    payload: SubmitAlpacaPaperOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> AlpacaPaperOrderResponse:
    account = await _load_account(db=db, account_id=payload.account_id)
    asset = await db.scalar(select(Asset).where(Asset.id == payload.asset_id))
    if asset is None:
        raise NotFoundError(message="Asset not found", details={"asset_id": str(payload.asset_id)})

    if account.asset_class != "stock" or asset.asset_class != "stock":
        raise InvalidRequestError(message="Alpaca paper orders are supported for stocks only", details={})

    if asset.exchange != "alpaca":
        raise InvalidRequestError(message="Asset exchange must be alpaca for Alpaca paper orders", details={"exchange": asset.exchange})

    if payload.quantity <= 0:
        raise InvalidRequestError(
            message="Quantity must be positive",
            details={"quantity": format(payload.quantity, "f")},
        )

    if not asset.supports_fractional and payload.quantity != payload.quantity.to_integral_value():
        raise InvalidRequestError(
            message="Asset does not support fractional quantity",
            details={"asset_id": str(asset.id), "quantity": format(payload.quantity, "f")},
        )

    settings = get_settings()
    async with AsyncHTTPClient() as client:
        result = await submit_alpaca_paper_order(
            settings=settings,
            client=client,
            symbol=asset.symbol,
            side=payload.side,
            quantity=payload.quantity,
            client_order_id=payload.client_order_id,
        )

    executed_quantity = result.filled_qty
    if executed_quantity > 0 and result.filled_avg_price is not None:
        trade = Trade(
            paper_account_id=account.id,
            asset_id=asset.id,
            side=result.side,
            quantity=executed_quantity,
            price=result.filled_avg_price,
            fee=Decimal("0"),
            is_paper=True,
            execution_venue="alpaca_paper",
            executed_at=_parse_iso_timestamp(result.filled_at) or datetime.now(timezone.utc),
        )
        db.add(trade)
        await db.commit()

    audit_entry = AuditLog(
        actor="system",
        action="paper_trade_submitted",
        entity_type="paper_account",
        entity_id=account.id,
        before_state={
            "account_id": str(account.id),
            "asset_id": str(asset.id),
            "quantity": format(payload.quantity, "f"),
        },
        after_state={
            "broker_order_id": result.broker_order_id,
            "status": result.status,
            "execution_venue": result.execution_venue,
            "is_paper": result.is_paper,
            "filled_quantity": format(result.filled_qty, "f"),
        },
    )
    db.add(audit_entry)
    await db.commit()

    logger.info(
        "Submitted Alpaca paper order: broker_order_id=%s account_id=%s asset_id=%s status=%s",
        result.broker_order_id,
        account.id,
        asset.id,
        result.status,
    )

    return AlpacaPaperOrderResponse(
        broker_order_id=result.broker_order_id,
        account_id=account.id,
        asset_id=asset.id,
        status=result.status,
        symbol=result.symbol,
        side=result.side,
        type=result.type,
        time_in_force=result.time_in_force,
        quantity=result.qty,
        filled_quantity=result.filled_qty,
        filled_avg_price=result.filled_avg_price,
        submitted_at=result.submitted_at,
        filled_at=result.filled_at,
        execution_venue=result.execution_venue,
        is_paper=result.is_paper,
    )


@router.get("/orders/alpaca/{broker_order_id}", response_model=AlpacaPaperOrderResponse)
async def get_stock_paper_order_status(
    broker_order_id: str,
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AlpacaPaperOrderResponse:
    account = await _load_account(db=db, account_id=account_id)
    asset = await db.scalar(select(Asset).where(Asset.id == asset_id))
    if asset is None:
        raise NotFoundError(message="Asset not found", details={"asset_id": str(asset_id)})

    settings = get_settings()
    async with AsyncHTTPClient() as client:
        result = await get_alpaca_paper_order(
            settings=settings,
            client=client,
            broker_order_id=broker_order_id,
        )

    logger.info(
        "Fetched Alpaca paper order status: broker_order_id=%s account_id=%s status=%s",
        broker_order_id,
        account.id,
        result.status,
    )

    return AlpacaPaperOrderResponse(
        broker_order_id=result.broker_order_id,
        account_id=account.id,
        asset_id=asset.id,
        status=result.status,
        symbol=result.symbol,
        side=result.side,
        type=result.type,
        time_in_force=result.time_in_force,
        quantity=result.qty,
        filled_quantity=result.filled_qty,
        filled_avg_price=result.filled_avg_price,
        submitted_at=result.submitted_at,
        filled_at=result.filled_at,
        execution_venue=result.execution_venue,
        is_paper=result.is_paper,
    )


async def _load_account(*, db: AsyncSession, account_id: uuid.UUID | None) -> PaperAccount:
    if account_id is not None:
        account = await db.scalar(select(PaperAccount).where(PaperAccount.id == account_id))
        if account is None:
            raise NotFoundError(message="Paper account not found", details={"account_id": str(account_id)})
        return account

    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.desc())
        .limit(1)
    )
    if account is None:
        raise NotFoundError(message="Paper account not found", details={})

    return account


def _parse_iso_timestamp(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    value = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
