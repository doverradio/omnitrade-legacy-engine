from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.db.session import get_db
from app.schemas.capital_campaigns import (
    CapitalCampaignCreateRequest,
    CapitalCampaignDeleteResponse,
    CapitalCampaignListResponse,
    CapitalCampaignResponse,
    CapitalCampaignStatus,
    CapitalCampaignUpdateRequest,
)
from app.services.capital_campaigns.service import (
    create_capital_campaign,
    delete_capital_campaign,
    get_capital_campaign,
    list_capital_campaigns,
    update_capital_campaign,
)

router = APIRouter(prefix="/capital-campaigns", tags=["capital-campaigns"])


@router.get("", response_model=CapitalCampaignListResponse)
async def get_capital_campaigns(
    status: CapitalCampaignStatus | None = Query(default=None),
    owner: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignListResponse:
    items = await list_capital_campaigns(db=db, status=status, owner=owner)
    return CapitalCampaignListResponse(items=items)


@router.post("", response_model=CapitalCampaignResponse, status_code=201)
async def post_capital_campaign(
    request: CapitalCampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignResponse:
    return await create_capital_campaign(db=db, request=request)


@router.get("/{campaign_uuid}", response_model=CapitalCampaignResponse)
async def get_capital_campaign_detail(
    campaign_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await get_capital_campaign(db=db, campaign_uuid=parsed)


@router.patch("/{campaign_uuid}", response_model=CapitalCampaignResponse)
async def patch_capital_campaign(
    campaign_uuid: str,
    request: CapitalCampaignUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await update_capital_campaign(db=db, campaign_uuid=parsed, request=request)


@router.delete("/{campaign_uuid}", response_model=CapitalCampaignDeleteResponse)
async def remove_capital_campaign(
    campaign_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignDeleteResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})

    await delete_capital_campaign(db=db, campaign_uuid=parsed)
    return CapitalCampaignDeleteResponse(campaign_uuid=parsed, deleted=True)
