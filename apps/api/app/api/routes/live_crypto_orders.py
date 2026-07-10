from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.schemas.live_crypto_orders import (
    LiveCryptoOrderCancelRequest,
    LiveCryptoOrderDryRunRequest,
    LiveCryptoOrderDryRunResponse,
    LiveCryptoOrderListResponse,
    LiveCryptoOrderPrepareRequest,
    LiveCryptoOrderPrepareResponse,
    LiveCryptoOrderReadinessResponse,
    LiveCryptoOrderReconcileRequest,
    LiveCryptoOrderReconcileResponse,
    LiveCryptoOrderResponse,
    LiveCryptoOrderSubmitRequest,
    LiveCryptoOrderSubmitResponse,
)
from app.services.live_crypto_orders import service

router = APIRouter(prefix="/live-crypto-orders", tags=["live-crypto-orders"])


@router.get("/readiness", response_model=LiveCryptoOrderReadinessResponse)
async def read_live_crypto_order_readiness(
    live_trading_profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderReadinessResponse:
    return await service.get_readiness(db=db, live_trading_profile_id=live_trading_profile_id)


@router.get("", response_model=LiveCryptoOrderListResponse)
async def list_live_crypto_orders(
    live_trading_profile_id: uuid.UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderListResponse:
    items = await service.list_orders(db=db, live_trading_profile_id=live_trading_profile_id, status=status)
    return LiveCryptoOrderListResponse(items=items)


@router.get("/{live_crypto_order_id}", response_model=LiveCryptoOrderResponse)
async def read_live_crypto_order(
    live_crypto_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderResponse:
    return await service.get_order(db=db, live_crypto_order_id=live_crypto_order_id)


@router.post("/prepare-confirmation", response_model=LiveCryptoOrderPrepareResponse)
async def prepare_live_crypto_order_confirmation(
    payload: LiveCryptoOrderPrepareRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderPrepareResponse:
    if current_user["id"] != payload.operator_identity:
        raise UnauthorizedError(message="Authenticated operator identity mismatch", details={})
    return await service.prepare_confirmation(db=db, request=payload)


@router.post("/dry-run", response_model=LiveCryptoOrderDryRunResponse)
async def dry_run_live_crypto_order_confirmation(
    payload: LiveCryptoOrderDryRunRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderDryRunResponse:
    if current_user["id"] != payload.operator_identity:
        raise UnauthorizedError(message="Authenticated operator identity mismatch", details={})
    return await service.dry_run(db=db, request=payload)


@router.post("/submit", response_model=LiveCryptoOrderSubmitResponse)
async def submit_live_crypto_order(
    payload: LiveCryptoOrderSubmitRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderSubmitResponse:
    if current_user["id"] != payload.operator_identity:
        raise UnauthorizedError(message="Authenticated operator identity mismatch", details={})
    return await service.submit(db=db, request=payload)


@router.post("/{live_crypto_order_id}/reconcile", response_model=LiveCryptoOrderReconcileResponse)
async def reconcile_live_crypto_order(
    live_crypto_order_id: uuid.UUID,
    payload: LiveCryptoOrderReconcileRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderReconcileResponse:
    if current_user["id"] != payload.operator_identity:
        raise UnauthorizedError(message="Authenticated operator identity mismatch", details={})
    return await service.reconcile(db=db, live_crypto_order_id=live_crypto_order_id, request=payload)


@router.post("/{live_crypto_order_id}/cancel", response_model=LiveCryptoOrderResponse)
async def cancel_live_crypto_order(
    live_crypto_order_id: uuid.UUID,
    payload: LiveCryptoOrderCancelRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveCryptoOrderResponse:
    if current_user["id"] != payload.operator_identity:
        raise UnauthorizedError(message="Authenticated operator identity mismatch", details={})
    return await service.cancel(db=db, live_crypto_order_id=live_crypto_order_id, request=payload)
