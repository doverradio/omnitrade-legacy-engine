from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.schemas.crypto_order_previews import (
    CryptoOrderPreviewCancelRequest,
    CryptoOrderPreviewCreateRequest,
    CryptoOrderPreviewDetailResponse,
    CryptoOrderPreviewListResponse,
    CryptoOrderPreviewReadinessResponse,
    CryptoOrderPreviewRefreshRequest,
)
from app.services.crypto_order_previews import (
    cancel_crypto_order_preview,
    create_crypto_order_preview,
    get_crypto_order_preview,
    get_crypto_order_preview_readiness,
    list_crypto_order_previews,
    refresh_crypto_order_preview,
)

router = APIRouter(prefix="/crypto-order-previews", tags=["crypto-order-previews"])


@router.get("", response_model=CryptoOrderPreviewListResponse)
async def list_previews(
    limit: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CryptoOrderPreviewListResponse:
    return await list_crypto_order_previews(db=db, limit=limit)


@router.get("/readiness", response_model=CryptoOrderPreviewReadinessResponse)
async def preview_readiness() -> CryptoOrderPreviewReadinessResponse:
    return await get_crypto_order_preview_readiness()


@router.post("", response_model=CryptoOrderPreviewDetailResponse)
async def create_preview(
    payload: CryptoOrderPreviewCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> CryptoOrderPreviewDetailResponse:
    return await create_crypto_order_preview(db=db, request=payload, actor=current_user["id"])


@router.get("/{preview_id}", response_model=CryptoOrderPreviewDetailResponse)
async def get_preview(
    preview_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CryptoOrderPreviewDetailResponse:
    return await get_crypto_order_preview(db=db, preview_id=preview_id)


@router.post("/{preview_id}/refresh", response_model=CryptoOrderPreviewDetailResponse)
async def refresh_preview(
    preview_id: uuid.UUID,
    payload: CryptoOrderPreviewRefreshRequest | None = None,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> CryptoOrderPreviewDetailResponse:
    return await refresh_crypto_order_preview(db=db, preview_id=preview_id, payload=payload, actor=current_user["id"])


@router.post("/{preview_id}/cancel", response_model=CryptoOrderPreviewDetailResponse)
async def cancel_preview(
    preview_id: uuid.UUID,
    payload: CryptoOrderPreviewCancelRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> CryptoOrderPreviewDetailResponse:
    return await cancel_crypto_order_preview(db=db, preview_id=preview_id, payload=payload, actor=current_user["id"])
