from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.schemas.capital_campaign_domain import (
    CampaignAccountingState,
    CampaignCompoundingPolicy,
    CampaignProfitDistributionPolicy,
    CapitalCampaignDefinitionListResponse,
    CapitalCampaignDefinitionResponse,
    CapitalCampaignDraftCreateRequest,
    CapitalCampaignPreviewRequest,
    CapitalCampaignPreviewResponse,
)
from app.services.capital_campaign_domain.preview_engine import build_campaign_preview
from app.services.capital_campaign_domain.repository import CapitalCampaignDomainRepository


_ALLOWED_STATUSES_FOR_DRAFT_CREATE = {"DRAFT", "READY"}
_SUPPORTED_PREVIEW_INSTRUMENTS = {"BTC-USD", "ETH-USD", "SOL-USD"}
_RUNTIME_EDITABLE_STATUSES = {"DRAFT", "READY", "PAUSED"}

_DEFINITION_TO_RUNTIME_STATUS = {
    "DRAFT": "DRAFT",
    "READY": "READY",
    "ACTIVE": "RUNNING",
    "PAUSED": "PAUSED",
    "CAPITAL_EXHAUSTED": "TARGET_REACHED",
    "COMPLETED": "COMPLETED",
    "CANCELED": "ARCHIVED",
    "MANUAL_REVIEW_REQUIRED": "PAUSED",
}

_RUNTIME_TO_DEFINITION_STATUS = {
    "DRAFT": "DRAFT",
    "READY": "READY",
    "RUNNING": "ACTIVE",
    "PAUSED": "PAUSED",
    "TARGET_REACHED": "CAPITAL_EXHAUSTED",
    "COMPLETED": "COMPLETED",
    "ARCHIVED": "CANCELED",
}


def _normalize_symbol(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _normalize_list(values: list[str]) -> list[str]:
    deduped = sorted({item.strip() for item in values if item.strip()})
    return deduped


def _validate_decimal_non_negative(*, field_name: str, value: Decimal) -> None:
    if value < Decimal("0"):
        raise InvalidRequestError(message=f"{field_name} must be >= 0", details={"field": field_name})


def _validate_create_request(request: CapitalCampaignDraftCreateRequest) -> None:
    if not request.non_live_only:
        raise InvalidRequestError(message="PFP-2.1 only supports non-live draft campaign creation", details={"non_live_only": request.non_live_only})

    if request.status not in _ALLOWED_STATUSES_FOR_DRAFT_CREATE:
        raise InvalidRequestError(message="Draft creation only supports DRAFT or READY status", details={"status": request.status})

    if request.capital_budget <= Decimal("0"):
        raise InvalidRequestError(message="capital_budget must be > 0", details={})

    remaining = request.remaining_unallocated_capital if request.remaining_unallocated_capital is not None else request.capital_budget
    if remaining < Decimal("0") or remaining > request.capital_budget:
        raise InvalidRequestError(message="remaining_unallocated_capital must be within 0..capital_budget", details={})

    if request.maximum_open_positions < 0:
        raise InvalidRequestError(message="maximum_open_positions must be >= 0", details={})

    if request.maximum_position_size < request.minimum_position_size:
        raise InvalidRequestError(message="maximum_position_size must be >= minimum_position_size", details={})

    for field_name, value in [
        ("minimum_position_size", request.minimum_position_size),
        ("maximum_position_size", request.maximum_position_size),
        ("maximum_total_exposure", request.maximum_total_exposure),
    ]:
        _validate_decimal_non_negative(field_name=field_name, value=value)

    allowed_instruments = {_normalize_symbol(item) for item in request.allowed_instruments}
    unsupported = sorted(allowed_instruments - _SUPPORTED_PREVIEW_INSTRUMENTS)
    if unsupported:
        raise InvalidRequestError(
            message="Unsupported instruments in PFP-2.1",
            details={"unsupported_instruments": unsupported},
        )


def _resolve_accounting_state(request: CapitalCampaignDraftCreateRequest) -> CampaignAccountingState:
    if request.accounting_state is None:
        return CampaignAccountingState(
            initial_capital=request.capital_budget,
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("0"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("0"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=request.capital_budget,
            maximum_drawdown=Decimal("0"),
            available_capital=request.capital_budget,
        )

    state = request.accounting_state
    for field_name, value in state.model_dump().items():
        _validate_decimal_non_negative(field_name=field_name, value=Decimal(value))
    return state


def _normalize_roi(*, starting_capital: Decimal, current_equity: Decimal) -> Decimal:
    if starting_capital <= Decimal("0"):
        return Decimal("0")
    return ((current_equity - starting_capital) / starting_capital) * Decimal("100")


async def _get_runtime_campaign(*, db: AsyncSession, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


def _runtime_status_from_definition(status: str) -> str:
    return _DEFINITION_TO_RUNTIME_STATUS.get(status, "DRAFT")


def _definition_status_from_runtime(status: str) -> str:
    return _RUNTIME_TO_DEFINITION_STATUS.get(status, "MANUAL_REVIEW_REQUIRED")


async def _ensure_runtime_campaign_pin(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
    request: CapitalCampaignDraftCreateRequest,
    accounting_state: CampaignAccountingState,
) -> CapitalCampaign:
    runtime = await _get_runtime_campaign(db=db, campaign_id=campaign_id)
    desired_status = _runtime_status_from_definition(request.status)

    if runtime is None:
        runtime = CapitalCampaign(
            uuid=campaign_id,
            owner=request.owner_identity.strip(),
            name=request.name.strip(),
            description=request.description.strip() if request.description else None,
            status=desired_status,
            campaign_type="definition_pinned_runtime",
            exchange=None,
            paper_account_id=None,
            validation_run_id=None,
            strategy_id=None,
            definition_campaign_id=campaign_id,
            definition_version=version,
            starting_capital=request.capital_budget,
            current_equity=accounting_state.current_campaign_equity,
            realized_profit=accounting_state.realized_net_pnl,
            unrealized_profit=accounting_state.unrealized_pnl,
            fees=accounting_state.fees,
            roi=_normalize_roi(
                starting_capital=request.capital_budget,
                current_equity=accounting_state.current_campaign_equity,
            ),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(runtime)
        await db.flush()
        return runtime

    if runtime.status not in _RUNTIME_EDITABLE_STATUSES and runtime.definition_version is not None:
        raise InvalidRequestError(
            message="Cannot repin runtime campaign while runtime status is immutable",
            details={
                "campaign_id": str(campaign_id),
                "runtime_status": runtime.status,
                "runtime_definition_version": runtime.definition_version,
            },
        )

    runtime.name = request.name.strip()
    runtime.description = request.description.strip() if request.description else None
    runtime.definition_campaign_id = campaign_id
    runtime.definition_version = version
    if runtime.status in {"DRAFT", "READY", "PAUSED"}:
        runtime.status = desired_status
    runtime.starting_capital = request.capital_budget
    runtime.current_equity = accounting_state.current_campaign_equity
    runtime.realized_profit = accounting_state.realized_net_pnl
    runtime.unrealized_profit = accounting_state.unrealized_pnl
    runtime.fees = accounting_state.fees
    runtime.roi = _normalize_roi(starting_capital=runtime.starting_capital, current_equity=runtime.current_equity)
    runtime.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return runtime


def _to_response(definition: CapitalCampaignDefinition, runtime: CapitalCampaign) -> CapitalCampaignDefinitionResponse:
    accounting_state = CampaignAccountingState(
        initial_capital=runtime.starting_capital,
        allocated_capital=Decimal("0"),
        reserved_capital=Decimal("0"),
        deployed_capital=Decimal("0"),
        realized_gross_pnl=runtime.realized_profit + runtime.fees,
        fees=runtime.fees,
        realized_net_pnl=runtime.realized_profit,
        unrealized_pnl=runtime.unrealized_profit,
        distributable_profit=Decimal("0"),
        compounded_profit=Decimal("0"),
        withdrawn_profit=Decimal("0"),
        current_campaign_equity=runtime.current_equity,
        maximum_drawdown=Decimal("0"),
        available_capital=runtime.current_equity,
    )
    return CapitalCampaignDefinitionResponse(
        campaign_id=definition.campaign_id,
        version=definition.version,
        runtime_campaign_uuid=runtime.uuid,
        runtime_definition_version=runtime.definition_version or definition.version,
        name=definition.name,
        description=definition.description,
        owner_identity=definition.owner_identity,
        status=_definition_status_from_runtime(runtime.status),
        capital_budget=definition.capital_budget,
        remaining_unallocated_capital=runtime.current_equity,
        base_currency=definition.base_currency,
        allowed_asset_classes=list(definition.allowed_asset_classes or []),
        allowed_venues=list(definition.allowed_venues or []),
        allowed_instruments=list(definition.allowed_instruments or []),
        campaign_modes=list(definition.campaign_modes or []),
        maximum_open_positions=definition.maximum_open_positions,
        maximum_position_size=definition.maximum_position_size,
        minimum_position_size=definition.minimum_position_size,
        maximum_total_exposure=definition.maximum_total_exposure,
        profitability_policy_id=definition.profitability_policy_id,
        profitability_policy_version=definition.profitability_policy_version,
        risk_policy_id=definition.risk_policy_id,
        risk_policy_version=definition.risk_policy_version,
        compounding_policy=CampaignCompoundingPolicy.model_validate(definition.compounding_policy),
        profit_distribution_policy=CampaignProfitDistributionPolicy.model_validate(definition.profit_distribution_policy),
        aggression_mode=definition.aggression_mode,
        accounting_state=accounting_state,
        created_at=definition.created_at,
        activated_at=definition.activated_at,
        paused_at=definition.paused_at,
        completed_at=definition.completed_at,
        metadata_evidence=dict(definition.metadata_evidence or {}),
    )


async def create_campaign_draft(*, db: AsyncSession, request: CapitalCampaignDraftCreateRequest) -> CapitalCampaignDefinitionResponse:
    _validate_create_request(request)

    repository = CapitalCampaignDomainRepository(db)

    now = datetime.now(timezone.utc)
    campaign_id = request.campaign_id or uuid4()
    version = await repository.next_version(campaign_id=campaign_id)

    accounting_state = _resolve_accounting_state(request)

    definition = CapitalCampaignDefinition(
        campaign_id=campaign_id,
        name=request.name.strip(),
        description=request.description.strip() if request.description else None,
        owner_identity=request.owner_identity.strip(),
        status="DRAFT",
        capital_budget=request.capital_budget,
        remaining_unallocated_capital=request.remaining_unallocated_capital if request.remaining_unallocated_capital is not None else request.capital_budget,
        base_currency=request.base_currency.strip().upper(),
        allowed_asset_classes=[item.lower() for item in _normalize_list(request.allowed_asset_classes)],
        allowed_venues=[item.lower() for item in _normalize_list(request.allowed_venues)],
        allowed_instruments=[_normalize_symbol(item) for item in _normalize_list(request.allowed_instruments)],
        campaign_modes=[item for item in sorted(set(request.campaign_modes))],
        maximum_open_positions=request.maximum_open_positions,
        maximum_position_size=request.maximum_position_size,
        minimum_position_size=request.minimum_position_size,
        maximum_total_exposure=request.maximum_total_exposure,
        profitability_policy_id=request.profitability_policy_id.strip(),
        profitability_policy_version=request.profitability_policy_version.strip(),
        risk_policy_id=request.risk_policy_id.strip(),
        risk_policy_version=request.risk_policy_version.strip(),
        compounding_policy=request.compounding_policy.model_dump(mode="json"),
        profit_distribution_policy=request.profit_distribution_policy.model_dump(mode="json"),
        aggression_mode=request.aggression_mode,
        initial_capital=accounting_state.initial_capital,
        allocated_capital=accounting_state.allocated_capital,
        reserved_capital=accounting_state.reserved_capital,
        deployed_capital=accounting_state.deployed_capital,
        realized_gross_pnl=accounting_state.realized_gross_pnl,
        fees=accounting_state.fees,
        realized_net_pnl=accounting_state.realized_net_pnl,
        unrealized_pnl=accounting_state.unrealized_pnl,
        distributable_profit=accounting_state.distributable_profit,
        compounded_profit=accounting_state.compounded_profit,
        withdrawn_profit=accounting_state.withdrawn_profit,
        current_campaign_equity=accounting_state.current_campaign_equity,
        maximum_drawdown=accounting_state.maximum_drawdown,
        available_capital=accounting_state.available_capital,
        activated_at=request.activated_at,
        paused_at=request.paused_at,
        completed_at=request.completed_at,
        version=version,
        metadata_evidence=request.metadata_evidence,
        created_at=now,
        updated_at=now,
    )

    created = await repository.create(definition)
    runtime = await _ensure_runtime_campaign_pin(
        db=db,
        campaign_id=campaign_id,
        version=version,
        request=request,
        accounting_state=accounting_state,
    )
    await db.commit()
    return _to_response(created, runtime)


async def get_campaign_definition(*, db: AsyncSession, campaign_id: UUID, version: int | None = None) -> CapitalCampaignDefinitionResponse:
    repository = CapitalCampaignDomainRepository(db)
    definition = await repository.get(campaign_id=campaign_id, version=version)
    if definition is None:
        raise NotFoundError(message="Capital campaign definition not found", details={"campaign_id": str(campaign_id), "version": version})
    runtime = await _get_runtime_campaign(db=db, campaign_id=campaign_id)
    if runtime is None:
        raise NotFoundError(message="Runtime capital campaign not found", details={"campaign_id": str(campaign_id)})
    if runtime.definition_version != definition.version:
        raise InvalidRequestError(
            message="Requested definition version is not pinned by runtime campaign",
            details={
                "campaign_id": str(campaign_id),
                "requested_version": definition.version,
                "runtime_pinned_version": runtime.definition_version,
            },
        )
    return _to_response(definition, runtime)


async def list_campaign_definitions(
    *,
    db: AsyncSession,
    campaign_id: UUID | None,
    status: str | None,
    latest_only: bool,
) -> CapitalCampaignDefinitionListResponse:
    repository = CapitalCampaignDomainRepository(db)
    rows = await repository.list(campaign_id=campaign_id, status=status, latest_only=latest_only)
    items: list[CapitalCampaignDefinitionResponse] = []
    for item in rows:
        runtime = await _get_runtime_campaign(db=db, campaign_id=item.campaign_id)
        if runtime is None:
            continue
        if runtime.definition_version != item.version:
            continue
        items.append(_to_response(item, runtime))
    return CapitalCampaignDefinitionListResponse(items=items)


async def preview_campaign_definition(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int | None,
    request: CapitalCampaignPreviewRequest,
) -> CapitalCampaignPreviewResponse:
    campaign = await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)
    now = datetime.now(timezone.utc)
    return build_campaign_preview(campaign=campaign, request=request, now=now)
