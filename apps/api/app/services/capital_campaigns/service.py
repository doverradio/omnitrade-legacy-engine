from __future__ import annotations

from decimal import Decimal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.capital_campaign import CapitalCampaign
from app.schemas.capital_campaigns import (
    CapitalCampaignCreateRequest,
    CapitalCampaignResponse,
    CapitalCampaignUpdateRequest,
)
from app.services.capital_campaigns.repository import CapitalCampaignRepository


_ALLOWED_STATUSES = {
    "DRAFT",
    "READY",
    "RUNNING",
    "PAUSED",
    "TARGET_REACHED",
    "COMPLETED",
    "ARCHIVED",
}

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"READY", "ARCHIVED"},
    "READY": {"RUNNING", "ARCHIVED"},
    "RUNNING": {"PAUSED", "TARGET_REACHED", "ARCHIVED"},
    "PAUSED": {"RUNNING", "ARCHIVED"},
    "TARGET_REACHED": {"COMPLETED", "ARCHIVED"},
    "COMPLETED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def _normalize_roi(*, starting_capital: Decimal, current_equity: Decimal) -> Decimal:
    if starting_capital <= 0:
        return Decimal("0")
    return ((current_equity - starting_capital) / starting_capital) * Decimal("100")


def _validate_status_transition(*, previous_status: str, next_status: str) -> None:
    if previous_status == next_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(previous_status, set())
    if next_status not in allowed:
        raise InvalidRequestError(
            message="Invalid status transition",
            details={"from": previous_status, "to": next_status},
        )


async def _validate_relationships(
    *,
    repository: CapitalCampaignRepository,
    paper_account_id: uuid.UUID | None,
    validation_run_id: uuid.UUID | None,
    strategy_id: uuid.UUID | None,
) -> None:
    if paper_account_id is not None and not await repository.paper_account_exists(paper_account_id):
        raise InvalidRequestError(
            message="paper_account_id was not found",
            details={"paper_account_id": str(paper_account_id)},
        )
    if validation_run_id is not None and not await repository.validation_run_exists(validation_run_id):
        raise InvalidRequestError(
            message="validation_run_id was not found",
            details={"validation_run_id": str(validation_run_id)},
        )
    if strategy_id is not None and not await repository.strategy_exists(strategy_id):
        raise InvalidRequestError(
            message="strategy_id was not found",
            details={"strategy_id": str(strategy_id)},
        )


def _to_response(campaign: CapitalCampaign) -> CapitalCampaignResponse:
    return CapitalCampaignResponse(
        id=campaign.id,
        uuid=campaign.uuid,
        owner=campaign.owner,
        name=campaign.name,
        description=campaign.description,
        status=campaign.status,
        campaign_type=campaign.campaign_type,
        exchange=campaign.exchange,
        paper_account_id=campaign.paper_account_id,
        validation_run_id=campaign.validation_run_id,
        strategy_id=campaign.strategy_id,
        starting_capital=campaign.starting_capital,
        current_equity=campaign.current_equity,
        realized_profit=campaign.realized_profit,
        unrealized_profit=campaign.unrealized_profit,
        fees=campaign.fees,
        roi=campaign.roi,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


async def list_capital_campaigns(
    *,
    db: AsyncSession,
    status: str | None = None,
    owner: str | None = None,
) -> list[CapitalCampaignResponse]:
    repository = CapitalCampaignRepository(db)
    rows = await repository.list(status=status, owner=owner)
    return [_to_response(item) for item in rows]


async def get_capital_campaign(*, db: AsyncSession, campaign_uuid: uuid.UUID) -> CapitalCampaignResponse:
    repository = CapitalCampaignRepository(db)
    campaign = await repository.get_by_uuid(campaign_uuid)
    if campaign is None:
        raise NotFoundError(message="Capital campaign not found", details={"campaign_uuid": str(campaign_uuid)})
    return _to_response(campaign)


async def create_capital_campaign(*, db: AsyncSession, request: CapitalCampaignCreateRequest) -> CapitalCampaignResponse:
    repository = CapitalCampaignRepository(db)

    name = request.name.strip()
    owner = request.owner.strip()
    campaign_type = request.campaign_type.strip()
    if not name:
        raise InvalidRequestError(message="name is required", details={})
    if not owner:
        raise InvalidRequestError(message="owner is required", details={})
    if not campaign_type:
        raise InvalidRequestError(message="campaign_type is required", details={})
    if request.status not in _ALLOWED_STATUSES:
        raise InvalidRequestError(message="Unsupported campaign status", details={"status": request.status})
    if request.starting_capital <= 0:
        raise InvalidRequestError(message="starting_capital must be > 0", details={})
    if request.current_equity is not None and request.current_equity < 0:
        raise InvalidRequestError(message="current_equity must be >= 0", details={})

    await _validate_relationships(
        repository=repository,
        paper_account_id=request.paper_account_id,
        validation_run_id=request.validation_run_id,
        strategy_id=request.strategy_id,
    )

    current_equity = request.current_equity if request.current_equity is not None else request.starting_capital
    roi = _normalize_roi(starting_capital=request.starting_capital, current_equity=current_equity)

    campaign = CapitalCampaign(
        owner=owner,
        name=name,
        description=(request.description.strip() if request.description else None),
        status=request.status,
        campaign_type=campaign_type,
        exchange=(request.exchange.strip() if request.exchange else None),
        paper_account_id=request.paper_account_id,
        validation_run_id=request.validation_run_id,
        strategy_id=request.strategy_id,
        starting_capital=request.starting_capital,
        current_equity=current_equity,
        realized_profit=request.realized_profit,
        unrealized_profit=request.unrealized_profit,
        fees=request.fees,
        roi=roi,
    )

    campaign = await repository.create(campaign)
    await db.commit()
    return _to_response(campaign)


async def update_capital_campaign(
    *,
    db: AsyncSession,
    campaign_uuid: uuid.UUID,
    request: CapitalCampaignUpdateRequest,
) -> CapitalCampaignResponse:
    repository = CapitalCampaignRepository(db)
    campaign = await repository.get_by_uuid(campaign_uuid)
    if campaign is None:
        raise NotFoundError(message="Capital campaign not found", details={"campaign_uuid": str(campaign_uuid)})

    changes = request.model_dump(exclude_unset=True)
    if not changes:
        return _to_response(campaign)

    if "status" in changes and changes["status"] not in _ALLOWED_STATUSES:
        raise InvalidRequestError(message="Unsupported campaign status", details={"status": changes["status"]})

    if "owner" in changes:
        raise InvalidRequestError(message="owner is immutable", details={})

    name = changes.get("name")
    if isinstance(name, str):
        cleaned = name.strip()
        if not cleaned:
            raise InvalidRequestError(message="name cannot be blank", details={})
        changes["name"] = cleaned

    campaign_type = changes.get("campaign_type")
    if isinstance(campaign_type, str):
        cleaned = campaign_type.strip()
        if not cleaned:
            raise InvalidRequestError(message="campaign_type cannot be blank", details={})
        changes["campaign_type"] = cleaned

    if "description" in changes and isinstance(changes["description"], str):
        changes["description"] = changes["description"].strip() or None

    if "exchange" in changes and isinstance(changes["exchange"], str):
        changes["exchange"] = changes["exchange"].strip() or None

    starting_capital = changes.get("starting_capital", campaign.starting_capital)
    if starting_capital <= 0:
        raise InvalidRequestError(message="starting_capital must be > 0", details={})
    current_equity = changes.get("current_equity", campaign.current_equity)
    if current_equity < 0:
        raise InvalidRequestError(message="current_equity must be >= 0", details={})

    next_status = changes.get("status", campaign.status)
    _validate_status_transition(previous_status=campaign.status, next_status=next_status)

    await _validate_relationships(
        repository=repository,
        paper_account_id=changes.get("paper_account_id", campaign.paper_account_id),
        validation_run_id=changes.get("validation_run_id", campaign.validation_run_id),
        strategy_id=changes.get("strategy_id", campaign.strategy_id),
    )

    changes["roi"] = _normalize_roi(starting_capital=starting_capital, current_equity=current_equity)

    campaign = await repository.update(campaign, changed_fields=changes)
    await db.commit()
    return _to_response(campaign)


async def delete_capital_campaign(*, db: AsyncSession, campaign_uuid: uuid.UUID) -> None:
    repository = CapitalCampaignRepository(db)
    campaign = await repository.get_by_uuid(campaign_uuid)
    if campaign is None:
        raise NotFoundError(message="Capital campaign not found", details={"campaign_uuid": str(campaign_uuid)})

    if campaign.status != "ARCHIVED":
        await repository.update(campaign, changed_fields={"status": "ARCHIVED"})
    await db.commit()
