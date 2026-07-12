from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.schemas.autonomous_capital_mandates import (
    AutonomousCapitalMandateAuthorizationCreateRequest,
    AutonomousCapitalMandateAuthorizationListResponse,
    AutonomousCapitalMandateAuthorizationResponse,
    AutonomousCapitalMandateCreateRequest,
    AutonomousCapitalMandateEvaluationCreateRequest,
    AutonomousCapitalMandateEvaluationListResponse,
    AutonomousCapitalMandateEvaluationResponse,
    AutonomousCapitalMandateHistoryEventResponse,
    AutonomousCapitalMandateHistoryResponse,
    AutonomousCapitalMandateLifecycleActionRequest,
    AutonomousCapitalMandateListResponse,
    AutonomousCapitalMandateResponse,
    AutonomousCapitalMandateVersionCreateRequest,
    AutonomousCapitalMandateVersionListResponse,
    AutonomousCapitalMandateVersionResponse,
)
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.mandates.evidence import (
    MandateEvaluationWriteRequest,
    evaluate_and_record_mandate,
    list_mandate_evaluations,
)
from app.services.mandates.lifecycle import (
    apply_mandate_lifecycle_action,
    authorize_mandate_version,
    create_mandate,
    create_mandate_version,
    get_mandate,
    list_mandate_authorizations,
    list_mandate_versions,
    list_mandates,
    read_mandate_history,
)

router = APIRouter(prefix="/autonomous-capital/mandates", tags=["autonomous-capital-mandates"])


@router.get("", response_model=AutonomousCapitalMandateListResponse)
async def get_autonomous_capital_mandates(
    owner_actor_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateListResponse:
    rows = await list_mandates(db=db, owner_actor_id=owner_actor_id, status=status)
    return AutonomousCapitalMandateListResponse(items=[_to_mandate_response(item) for item in rows])


@router.post("", response_model=AutonomousCapitalMandateResponse, status_code=201)
async def post_autonomous_capital_mandate(
    payload: AutonomousCapitalMandateCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateResponse:
    mandate = await create_mandate(
        db=db,
        owner_actor_id=payload.owner_actor_id,
        autonomy_level=payload.autonomy_level,
        provider=payload.provider,
        exchange_environment=payload.exchange_environment,
        exchange_connection_id=payload.exchange_connection_id,
        live_trading_profile_id=payload.live_trading_profile_id,
        paper_account_id=payload.paper_account_id,
        capital_campaign_id=payload.capital_campaign_id,
        expires_at=payload.expires_at,
        actor=current_user["id"],
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
    )
    return _to_mandate_response(mandate)


@router.get("/{mandate_id}", response_model=AutonomousCapitalMandateResponse)
async def get_autonomous_capital_mandate_detail(
    mandate_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateResponse:
    parsed = _parse_mandate_id(mandate_id)
    mandate = await get_mandate(db=db, mandate_id=parsed)
    return _to_mandate_response(mandate)


@router.post("/{mandate_id}/lifecycle-actions", response_model=AutonomousCapitalMandateResponse)
async def post_autonomous_capital_mandate_lifecycle_action(
    mandate_id: str,
    payload: AutonomousCapitalMandateLifecycleActionRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateResponse:
    parsed = _parse_mandate_id(mandate_id)
    mandate = await apply_mandate_lifecycle_action(
        db=db,
        request=MandateLifecycleActionRequest(
            mandate_id=parsed,
            actor=current_user["id"],
            action=payload.action,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            audit_correlation_id=payload.audit_correlation_id,
            software_build_version=payload.software_build_version,
        ),
    )
    return _to_mandate_response(mandate)


@router.get("/{mandate_id}/versions", response_model=AutonomousCapitalMandateVersionListResponse)
async def get_autonomous_capital_mandate_versions(
    mandate_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateVersionListResponse:
    parsed = _parse_mandate_id(mandate_id)
    versions = await list_mandate_versions(db=db, mandate_id=parsed)
    return AutonomousCapitalMandateVersionListResponse(items=[_to_version_response(item) for item in versions])


@router.post("/{mandate_id}/versions", response_model=AutonomousCapitalMandateVersionResponse, status_code=201)
async def post_autonomous_capital_mandate_version(
    mandate_id: str,
    payload: AutonomousCapitalMandateVersionCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateVersionResponse:
    parsed = _parse_mandate_id(mandate_id)
    version = await create_mandate_version(
        db=db,
        request=MandateVersionCreateRequest(
            mandate_id=parsed,
            actor=current_user["id"],
            base_currency=payload.base_currency,
            authorized_capital_usd=payload.authorized_capital_usd,
            max_order_notional_usd=payload.max_order_notional_usd,
            max_open_exposure_usd=payload.max_open_exposure_usd,
            max_daily_deployed_usd=payload.max_daily_deployed_usd,
            max_daily_realized_loss_usd=payload.max_daily_realized_loss_usd,
            max_campaign_drawdown_usd=payload.max_campaign_drawdown_usd,
            max_consecutive_losses=payload.max_consecutive_losses,
            position_limit=payload.position_limit,
            price_evidence_max_age_seconds=payload.price_evidence_max_age_seconds,
            max_slippage_bps=payload.max_slippage_bps,
            max_fee_bps=payload.max_fee_bps,
            allowed_products=tuple(payload.allowed_products),
            allowed_order_sides=tuple(payload.allowed_order_sides),
            allowed_strategy_versions=tuple(payload.allowed_strategy_versions),
            entry_policy=payload.entry_policy,
            exit_policy=payload.exit_policy,
            cooldown_policy=payload.cooldown_policy,
            operating_schedule=payload.operating_schedule,
            approval_policy=payload.approval_policy,
            reconciliation_policy=payload.reconciliation_policy,
            kill_switch_policy=payload.kill_switch_policy,
            owner_acknowledgements=payload.owner_acknowledgements,
            authorization_evidence_summary=payload.authorization_evidence_summary,
            idempotency_key=payload.idempotency_key,
            audit_correlation_id=payload.audit_correlation_id,
        ),
    )
    return _to_version_response(version)


@router.get("/{mandate_id}/authorizations", response_model=AutonomousCapitalMandateAuthorizationListResponse)
async def get_autonomous_capital_mandate_authorizations(
    mandate_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateAuthorizationListResponse:
    parsed = _parse_mandate_id(mandate_id)
    rows = await list_mandate_authorizations(db=db, mandate_id=parsed)
    return AutonomousCapitalMandateAuthorizationListResponse(
        items=[
            AutonomousCapitalMandateAuthorizationResponse(
                mandate_authorization_id=item.mandate_authorization_id,
                mandate_id=item.mandate_id,
                mandate_version_id=item.mandate_version_id,
                mandate_version_number=item.mandate_version_number,
                autonomy_level=item.autonomy_level,
                authorization_state=item.authorization_state,
                approval_result=item.approval_result,
                authorized_by_actor_id=item.authorized_by_actor_id,
                audit_correlation_id=item.audit_correlation_id,
                recorded_at=item.recorded_at,
                expires_at=item.expires_at,
                revoked_at=item.revoked_at,
            )
            for item in rows
        ]
    )


@router.post("/{mandate_id}/authorizations", response_model=AutonomousCapitalMandateAuthorizationResponse, status_code=201)
async def post_autonomous_capital_mandate_authorization(
    mandate_id: str,
    payload: AutonomousCapitalMandateAuthorizationCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateAuthorizationResponse:
    parsed = _parse_mandate_id(mandate_id)
    item = await authorize_mandate_version(
        db=db,
        request=MandateAuthorizationRequest(
            mandate_id=parsed,
            mandate_version_id=payload.mandate_version_id,
            actor=current_user["id"],
            authorization_method=payload.authorization_method,
            owner_acknowledgements=payload.owner_acknowledgements,
            authorization_evidence=payload.authorization_evidence,
            deterministic_explanation=payload.deterministic_explanation,
            expires_at=payload.expires_at,
            idempotency_key=payload.idempotency_key,
            audit_correlation_id=payload.audit_correlation_id,
        ),
    )
    return AutonomousCapitalMandateAuthorizationResponse(
        mandate_authorization_id=item.mandate_authorization_id,
        mandate_id=item.mandate_id,
        mandate_version_id=item.mandate_version_id,
        mandate_version_number=item.mandate_version_number,
        autonomy_level=item.autonomy_level,
        authorization_state=item.authorization_state,
        approval_result=item.approval_result,
        authorized_by_actor_id=item.authorized_by_actor_id,
        audit_correlation_id=item.audit_correlation_id,
        recorded_at=item.recorded_at,
        expires_at=item.expires_at,
        revoked_at=item.revoked_at,
    )


@router.get("/{mandate_id}/history", response_model=AutonomousCapitalMandateHistoryResponse)
async def get_autonomous_capital_mandate_history(
    mandate_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateHistoryResponse:
    parsed = _parse_mandate_id(mandate_id)
    events = await read_mandate_history(db=db, mandate_id=parsed)
    return AutonomousCapitalMandateHistoryResponse(
        items=[
            AutonomousCapitalMandateHistoryEventResponse(
                audit_id=item.audit_id,
                actor=item.actor,
                action=item.action,
                created_at=item.created_at,
                before_state=item.before_state,
                after_state=item.after_state,
            )
            for item in events
        ]
    )


@router.post("/{mandate_id}/evaluations", response_model=AutonomousCapitalMandateEvaluationResponse, status_code=201)
async def post_autonomous_capital_mandate_evaluation(
    mandate_id: str,
    payload: AutonomousCapitalMandateEvaluationCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateEvaluationResponse:
    parsed = _parse_mandate_id(mandate_id)
    result = await evaluate_and_record_mandate(
        db=db,
        request=MandateEvaluationWriteRequest(
            mandate_id=parsed,
            actor=current_user["id"],
            strategy_version=payload.strategy_version,
            product=payload.product,
            side=payload.side,
            proposed_notional_usd=payload.proposed_notional_usd,
            current_open_exposure_usd=payload.current_open_exposure_usd,
            daily_deployed_usd=payload.daily_deployed_usd,
            daily_realized_loss_usd=payload.daily_realized_loss_usd,
            campaign_drawdown_usd=payload.campaign_drawdown_usd,
            consecutive_losses=payload.consecutive_losses,
            current_position_count=payload.current_position_count,
            risk_verdict=payload.risk_verdict,
            evidence_age_seconds=payload.evidence_age_seconds,
            kill_switch_engaged=payload.kill_switch_engaged,
            observed_at=payload.observed_at,
            decision_id=payload.decision_id,
            request_context=payload.request_context,
            idempotency_key=payload.idempotency_key,
            audit_correlation_id=payload.audit_correlation_id,
            software_build_version=payload.software_build_version,
        ),
    )
    return AutonomousCapitalMandateEvaluationResponse(
        evaluation_id=result.evaluation_id,
        mandate_id=result.mandate_id,
        mandate_version_id=result.mandate_version_id,
        mandate_version_number=result.mandate_version_number,
        autonomy_level=result.autonomy_level,
        proposed_action=result.proposed_action,
        authorization_result=result.authorization_result,
        approval_result=result.approval_result,
        risk_verdict=result.risk_verdict,
        risk_evaluated=result.risk_evaluated,
        checks_passed=list(result.checks_passed),
        checks_failed=list(result.checks_failed),
        deterministic_explanation=list(result.deterministic_explanation),
        reason_code=result.reason_code,
        human_approval_required=result.human_approval_required,
        active_mandate_exemption_eligible=result.active_mandate_exemption_eligible,
        decision_id=result.decision_id,
        actor=result.actor,
        audit_correlation_id=result.audit_correlation_id,
        software_build_version=result.software_build_version,
        idempotency_key=result.idempotency_key,
        created_at=result.created_at,
    )


@router.get("/{mandate_id}/evaluations", response_model=AutonomousCapitalMandateEvaluationListResponse)
async def get_autonomous_capital_mandate_evaluations(
    mandate_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutonomousCapitalMandateEvaluationListResponse:
    parsed = _parse_mandate_id(mandate_id)
    rows = await list_mandate_evaluations(db=db, mandate_id=parsed)
    return AutonomousCapitalMandateEvaluationListResponse(
        items=[
            AutonomousCapitalMandateEvaluationResponse(
                evaluation_id=item.evaluation_id,
                mandate_id=item.mandate_id,
                mandate_version_id=item.mandate_version_id,
                mandate_version_number=item.mandate_version_number,
                autonomy_level=item.autonomy_level,
                proposed_action=item.proposed_action,
                authorization_result=item.authorization_result,
                approval_result=item.approval_result,
                risk_verdict=item.risk_verdict,
                risk_evaluated=item.risk_evaluated,
                checks_passed=list(item.checks_passed),
                checks_failed=list(item.checks_failed),
                deterministic_explanation=list(item.deterministic_explanation),
                reason_code=item.reason_code,
                human_approval_required=item.human_approval_required,
                active_mandate_exemption_eligible=item.active_mandate_exemption_eligible,
                decision_id=item.decision_id,
                actor=item.actor,
                audit_correlation_id=item.audit_correlation_id,
                software_build_version=item.software_build_version,
                idempotency_key=item.idempotency_key,
                created_at=item.created_at,
            )
            for item in rows
        ]
    )


def _parse_mandate_id(raw_value: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw_value)
    except ValueError as exc:
        raise InvalidRequestError(message="Invalid mandate_id", details={"mandate_id": raw_value}) from exc


def _to_mandate_response(mandate: AutonomousCapitalMandate) -> AutonomousCapitalMandateResponse:
    return AutonomousCapitalMandateResponse(
        mandate_id=mandate.mandate_id,
        owner_actor_id=mandate.owner_actor_id,
        status=mandate.status,
        autonomy_level=mandate.autonomy_level,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        authorized_at=mandate.authorized_at,
        activated_at=mandate.activated_at,
        paused_at=mandate.paused_at,
        expires_at=mandate.expires_at,
        revoked_at=mandate.revoked_at,
        created_at=mandate.created_at,
        updated_at=mandate.updated_at,
    )


def _to_version_response(version: AutonomousCapitalMandateVersion) -> AutonomousCapitalMandateVersionResponse:
    return AutonomousCapitalMandateVersionResponse(
        mandate_version_id=version.mandate_version_id,
        mandate_id=version.mandate_id,
        version_number=version.version_number,
        version_hash=version.version_hash,
        base_currency=version.base_currency,
        authorized_capital_usd=version.authorized_capital_usd,
        max_order_notional_usd=version.max_order_notional_usd,
        max_open_exposure_usd=version.max_open_exposure_usd,
        max_daily_deployed_usd=version.max_daily_deployed_usd,
        max_daily_realized_loss_usd=version.max_daily_realized_loss_usd,
        max_campaign_drawdown_usd=version.max_campaign_drawdown_usd,
        max_consecutive_losses=version.max_consecutive_losses,
        position_limit=version.position_limit,
        price_evidence_max_age_seconds=version.price_evidence_max_age_seconds,
        max_slippage_bps=version.max_slippage_bps,
        max_fee_bps=version.max_fee_bps,
        allowed_products=version.allowed_products,
        allowed_order_sides=version.allowed_order_sides,
        allowed_strategy_versions=version.allowed_strategy_versions,
        approval_policy=version.approval_policy,
        is_authorized=version.is_authorized,
        is_active=version.is_active,
        created_at=version.created_at,
        authorized_at=version.authorized_at,
    )
