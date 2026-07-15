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
from app.schemas.capital_campaign_profit import (
    CapitalCampaignProfitCycleDecisionRequest,
    CapitalCampaignProfitCycleEvaluateRequest,
    CapitalCampaignProfitCycleListResponse,
    CapitalCampaignProfitCycleResponse,
    CapitalCampaignProfitPolicyResponse,
    CapitalCampaignProfitPolicyUpsertRequest,
)
from app.schemas.capital_campaign_domain import (
    CapitalCampaignDefinitionListResponse,
    CapitalCampaignDefinitionResponse,
    CapitalCampaignDraftCreateRequest,
    CapitalCampaignPreviewRequest,
    CapitalCampaignPreviewResponse,
)
from app.services.capital_campaign_domain import (
    create_campaign_draft,
    get_campaign_definition,
    list_campaign_definitions,
    preview_campaign_definition,
)
from app.services.capital_campaign_profit.service import (
    approve_profit_cycle,
    evaluate_profit_cycle,
    get_active_profit_policy,
    get_profit_cycle,
    list_profit_cycles,
    reject_profit_cycle,
    upsert_profit_policy,
)
from app.services.capital_campaigns.service import (
    create_capital_campaign,
    delete_capital_campaign,
    get_capital_campaign,
    list_capital_campaigns,
    update_capital_campaign,
)

router = APIRouter(prefix="/capital-campaigns", tags=["capital-campaigns"])


@router.post("/domain/drafts", response_model=CapitalCampaignDefinitionResponse, status_code=201)
async def post_capital_campaign_domain_draft(
    request: CapitalCampaignDraftCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignDefinitionResponse:
    return await create_campaign_draft(db=db, request=request)


@router.get("/domain", response_model=CapitalCampaignDefinitionListResponse)
async def get_capital_campaign_domain_definitions(
    campaign_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    latest_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignDefinitionListResponse:
    parsed_campaign_id = None
    if campaign_id is not None:
        try:
            parsed_campaign_id = uuid.UUID(campaign_id)
        except ValueError:
            raise InvalidRequestError(message="Invalid campaign_id", details={"campaign_id": campaign_id})
    return await list_campaign_definitions(
        db=db,
        campaign_id=parsed_campaign_id,
        status=status,
        latest_only=latest_only,
    )


@router.get("/domain/{campaign_id}", response_model=CapitalCampaignDefinitionResponse)
async def get_capital_campaign_domain_definition(
    campaign_id: str,
    version: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignDefinitionResponse:
    try:
        parsed_campaign_id = uuid.UUID(campaign_id)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_id", details={"campaign_id": campaign_id})
    return await get_campaign_definition(db=db, campaign_id=parsed_campaign_id, version=version)


@router.post("/domain/{campaign_id}/preview", response_model=CapitalCampaignPreviewResponse)
async def post_capital_campaign_domain_preview(
    campaign_id: str,
    request: CapitalCampaignPreviewRequest,
    version: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignPreviewResponse:
    try:
        parsed_campaign_id = uuid.UUID(campaign_id)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_id", details={"campaign_id": campaign_id})
    return await preview_campaign_definition(
        db=db,
        campaign_id=parsed_campaign_id,
        version=version,
        request=request,
    )


@router.post("/domain/{campaign_id}/preview/explain", response_model=CapitalCampaignPreviewResponse)
async def post_capital_campaign_domain_preview_explain(
    campaign_id: str,
    request: CapitalCampaignPreviewRequest,
    version: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignPreviewResponse:
    try:
        parsed_campaign_id = uuid.UUID(campaign_id)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_id", details={"campaign_id": campaign_id})
    return await preview_campaign_definition(
        db=db,
        campaign_id=parsed_campaign_id,
        version=version,
        request=request,
    )


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


@router.post("/{campaign_uuid}/profit-policy", response_model=CapitalCampaignProfitPolicyResponse)
async def post_capital_campaign_profit_policy(
    campaign_uuid: str,
    request: CapitalCampaignProfitPolicyUpsertRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitPolicyResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await upsert_profit_policy(db=db, campaign_uuid=parsed, request=request)


@router.get("/{campaign_uuid}/profit-policy", response_model=CapitalCampaignProfitPolicyResponse)
async def get_capital_campaign_profit_policy(
    campaign_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitPolicyResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await get_active_profit_policy(db=db, campaign_uuid=parsed)


@router.patch("/{campaign_uuid}/profit-policy", response_model=CapitalCampaignProfitPolicyResponse)
async def patch_capital_campaign_profit_policy(
    campaign_uuid: str,
    request: CapitalCampaignProfitPolicyUpsertRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitPolicyResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await upsert_profit_policy(db=db, campaign_uuid=parsed, request=request)


@router.post("/{campaign_uuid}/profit-cycles/evaluate", response_model=CapitalCampaignProfitCycleResponse)
async def post_capital_campaign_profit_cycle_evaluate(
    campaign_uuid: str,
    request: CapitalCampaignProfitCycleEvaluateRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitCycleResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    return await evaluate_profit_cycle(
        db=db,
        campaign_uuid=parsed,
        actor=request.actor,
        force_new_cycle=request.force_new_cycle,
    )


@router.get("/{campaign_uuid}/profit-cycles", response_model=CapitalCampaignProfitCycleListResponse)
async def get_capital_campaign_profit_cycles(
    campaign_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitCycleListResponse:
    try:
        parsed = uuid.UUID(campaign_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid campaign_uuid", details={"campaign_uuid": campaign_uuid})
    items = await list_profit_cycles(db=db, campaign_uuid=parsed)
    return CapitalCampaignProfitCycleListResponse(items=items)


@router.get("/{campaign_uuid}/profit-cycles/{cycle_uuid}", response_model=CapitalCampaignProfitCycleResponse)
async def get_capital_campaign_profit_cycle_detail(
    campaign_uuid: str,
    cycle_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitCycleResponse:
    try:
        parsed_campaign = uuid.UUID(campaign_uuid)
        parsed_cycle = uuid.UUID(cycle_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid UUID format", details={"campaign_uuid": campaign_uuid, "cycle_uuid": cycle_uuid})
    return await get_profit_cycle(db=db, campaign_uuid=parsed_campaign, cycle_uuid=parsed_cycle)


@router.post("/{campaign_uuid}/profit-cycles/{cycle_uuid}/approve", response_model=CapitalCampaignProfitCycleResponse)
async def post_capital_campaign_profit_cycle_approve(
    campaign_uuid: str,
    cycle_uuid: str,
    request: CapitalCampaignProfitCycleDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitCycleResponse:
    try:
        parsed_campaign = uuid.UUID(campaign_uuid)
        parsed_cycle = uuid.UUID(cycle_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid UUID format", details={"campaign_uuid": campaign_uuid, "cycle_uuid": cycle_uuid})
    return await approve_profit_cycle(
        db=db,
        campaign_uuid=parsed_campaign,
        cycle_uuid=parsed_cycle,
        actor=request.actor,
    )


@router.post("/{campaign_uuid}/profit-cycles/{cycle_uuid}/reject", response_model=CapitalCampaignProfitCycleResponse)
async def post_capital_campaign_profit_cycle_reject(
    campaign_uuid: str,
    cycle_uuid: str,
    request: CapitalCampaignProfitCycleDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> CapitalCampaignProfitCycleResponse:
    try:
        parsed_campaign = uuid.UUID(campaign_uuid)
        parsed_cycle = uuid.UUID(cycle_uuid)
    except ValueError:
        raise InvalidRequestError(message="Invalid UUID format", details={"campaign_uuid": campaign_uuid, "cycle_uuid": cycle_uuid})
    return await reject_profit_cycle(
        db=db,
        campaign_uuid=parsed_campaign,
        cycle_uuid=parsed_cycle,
        actor=request.actor,
        reason=request.reason,
    )
