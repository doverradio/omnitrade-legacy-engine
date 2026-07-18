from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.instant_trades import (
    InstantTradeAdoptRequest,
    InstantTradeBuyRequest,
    InstantTradeReceiptResponse,
)
from app.services.instant_trades import service


router = APIRouter(prefix="/instant-trades", tags=["instant-trades"])


@router.post("/buy", response_model=InstantTradeReceiptResponse)
async def instant_buy(
    payload: InstantTradeBuyRequest,
    current_user: dict[str, str] | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InstantTradeReceiptResponse:
    if current_user is None:
        from app.core.errors import UnauthorizedError

        raise UnauthorizedError(message="Authentication required", details={})
    return await service.buy(db=db, request=payload, authenticated_user_id=current_user["id"])


@router.get("/{order_id}", response_model=InstantTradeReceiptResponse)
async def read_instant_trade(
    order_id: uuid.UUID,
    current_user: dict[str, str] | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InstantTradeReceiptResponse:
    if current_user is None:
        from app.core.errors import UnauthorizedError

        raise UnauthorizedError(message="Authentication required", details={})
    return await service.read_receipt(db=db, order_id=order_id, authenticated_user_id=current_user["id"])


@router.post("/{order_id}/adopt-into-autonomous-management", response_model=InstantTradeReceiptResponse)
async def adopt_instant_trade(
    order_id: uuid.UUID,
    payload: InstantTradeAdoptRequest,
    current_user: dict[str, str] | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InstantTradeReceiptResponse:
    if current_user is None:
        from app.core.errors import UnauthorizedError

        raise UnauthorizedError(message="Authentication required", details={})
    return await service.adopt_into_autonomous_management(
        db=db,
        order_id=order_id,
        actor=payload.actor,
        authenticated_user_id=current_user["id"],
    )
