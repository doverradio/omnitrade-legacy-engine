from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError
from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.models.asset_commissioning_run import AssetCommissioningRun
from app.schemas.asset_commissioning import (
    AssetCommissioningPreviewRequest,
    AssetCommissioningPreviewResponse,
    AssetCommissioningRequest,
    AssetCommissioningResponse,
    AssetReadinessResponse,
)
from app.services.asset_commissioning import (
    commission_asset,
    get_asset_readiness,
    get_commissioning_status,
    preview_asset_commissioning,
)

router = APIRouter(prefix="/operator/assets", tags=["asset-commissioning"])


def _to_response(run: AssetCommissioningRun) -> AssetCommissioningResponse:
    return AssetCommissioningResponse(
        commissioning_id=run.commissioning_id,
        provider=run.provider,
        product_id=run.product_id,
        campaign_id=run.campaign_id,
        environment=run.environment,
        status=run.status,
        stages=run.stages,
        asset_id=run.asset_id,
        mandate_version_id=run.mandate_version_id,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.post("/commission/preview", response_model=AssetCommissioningPreviewResponse)
async def post_asset_commissioning_preview(
    payload: AssetCommissioningPreviewRequest,
    db: AsyncSession = Depends(get_db),
) -> AssetCommissioningPreviewResponse:
    result = await preview_asset_commissioning(
        db=db, provider=payload.provider, product_id=payload.product_id,
        campaign_id=payload.campaign_id, environment=payload.environment,
    )
    return AssetCommissioningPreviewResponse(**result)


@router.post("/commission", response_model=AssetCommissioningResponse, status_code=201)
async def post_asset_commissioning(
    payload: AssetCommissioningRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AssetCommissioningResponse:
    run = await commission_asset(
        db=db, provider=payload.provider, product_id=payload.product_id, campaign_id=payload.campaign_id,
        environment=payload.environment, activate=payload.activate, idempotency_key=payload.idempotency_key,
        actor=current_user["id"],
    )
    return _to_response(run)


@router.get("/commission/{commissioning_id}", response_model=AssetCommissioningResponse)
async def get_asset_commissioning_status(
    commissioning_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AssetCommissioningResponse:
    run = await get_commissioning_status(db=db, commissioning_id=commissioning_id)
    return _to_response(run)


@router.get("/{product_id}/readiness", response_model=AssetReadinessResponse)
async def get_asset_readiness_endpoint(
    product_id: str,
    campaign_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> AssetReadinessResponse:
    resolved_campaign_id = campaign_id or get_settings().automatic_mandate_package_activation_campaign_id
    if resolved_campaign_id is None:
        raise InvalidRequestError(
            message="campaign_id was not supplied and no default campaign is configured",
            details={"product_id": product_id},
        )
    result = await get_asset_readiness(db=db, product_id=product_id, campaign_id=resolved_campaign_id)
    return AssetReadinessResponse(**result)
