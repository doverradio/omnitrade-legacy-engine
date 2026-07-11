from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.schemas.live import (
    LiveApprovalCheckpointCreateRequest,
    LiveApprovalEventResponse,
    LiveApprovalStateChangeCreateRequest,
    LiveApprovalStatusReadModelResponse,
    LiveComplianceEvidenceItemResponse,
    LiveComplianceEvidenceReadModelResponse,
    LiveComplianceExportBundleResponse,
    LiveExecutionQualityReadModelItemResponse,
    LiveExecutionQualityReadModelResponse,
    LiveOperatorWarningResponse,
    LiveReconciliationSummaryResponse,
    LiveRegistrationStatusResponse,
)
from app.services.live import (
    LiveApprovalCheckpointRequest,
    LiveApprovalStateChangeRequest,
    LiveComplianceExportRequest,
    export_live_compliance_bundle,
    read_live_compliance_evidence,
    read_live_execution_quality,
    record_live_approval_checkpoint,
    revoke_live_approval,
    suspend_live_approval,
)

router = APIRouter(prefix="/live", tags=["live"])


def _with_baseline_warning(warnings: list[LiveOperatorWarningResponse]) -> list[LiveOperatorWarningResponse]:
    baseline = LiveOperatorWarningResponse(
        code="operator_controlled_live_mode",
        message=(
            "Live trading is operator-controlled. Paper is default, Risk Engine remains final authority, "
            "and direct UI order submission is prohibited."
        ),
    )
    return [baseline, *warnings]


def _profile_warnings(profile: LiveTradingProfile | None) -> list[LiveOperatorWarningResponse]:
    warnings: list[LiveOperatorWarningResponse] = []

    if profile is None:
        warnings.append(
            LiveOperatorWarningResponse(
                code="registration_state_unknown",
                message="Live registration profile not found; treat status as unknown and fail closed.",
            )
        )
        return _with_baseline_warning(warnings)

    if profile.operating_mode != "paper" and profile.approval_state != "approved":
        warnings.append(
            LiveOperatorWarningResponse(
                code="approval_boundary_violation",
                message="Operating mode is not paper while approval is not approved. Investigate immediately.",
            )
        )
    if profile.paper_default_mode is not True:
        warnings.append(
            LiveOperatorWarningResponse(
                code="paper_default_boundary_violation",
                message="paper_default_mode boundary violated; live operations must fail closed.",
            )
        )
    if profile.risk_authority_model != "risk_engine_final":
        warnings.append(
            LiveOperatorWarningResponse(
                code="risk_authority_boundary_violation",
                message="Risk Engine final-authority boundary is violated; stop live operations.",
            )
        )
    if profile.operating_mode == "paper":
        warnings.append(
            LiveOperatorWarningResponse(
                code="paper_mode_active",
                message="Paper mode remains active; live order submission is blocked unless explicit approvals exist.",
            )
        )

    return _with_baseline_warning(warnings)


@router.get("/registration/status", response_model=LiveRegistrationStatusResponse)
async def read_live_registration_status(
    live_trading_profile_id: uuid.UUID | None = None,
    paper_account_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveRegistrationStatusResponse:
    if live_trading_profile_id is None and paper_account_id is None:
        raise InvalidRequestError("live_trading_profile_id or paper_account_id is required")

    profile: LiveTradingProfile | None
    if live_trading_profile_id is not None:
        profile = await db.scalar(
            select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
        )
    else:
        profile = await db.scalar(
            select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == paper_account_id).limit(1)
        )

    if profile is None:
        return LiveRegistrationStatusResponse(
            live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id,
            readiness_state="unknown",
            operating_mode="paper",
            approval_state="not_requested",
            live_opt_in=None,
            human_approval_recorded=None,
            governance_approved=None,
            risk_authority_model=None,
            paper_default_mode=None,
            status_state="unknown",
            warnings=_profile_warnings(None),
        )

    return LiveRegistrationStatusResponse(
        live_trading_profile_id=profile.id,
        paper_account_id=profile.paper_account_id,
        readiness_state=profile.lifecycle_state,
        operating_mode=profile.operating_mode,
        approval_state=profile.approval_state,
        live_opt_in=profile.live_opt_in,
        human_approval_recorded=profile.human_approval_recorded,
        governance_approved=profile.governance_approved,
        risk_authority_model=profile.risk_authority_model,
        paper_default_mode=profile.paper_default_mode,
        status_state="available",
        warnings=_profile_warnings(profile),
    )


@router.get("/approvals/status", response_model=LiveApprovalStatusReadModelResponse)
async def read_live_approvals_status(
    live_trading_profile_id: uuid.UUID,
    checkpoint_type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveApprovalStatusReadModelResponse:
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    warnings = _profile_warnings(profile)

    events = list(
        await db.scalars(
            select(LiveApprovalEvent)
            .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
            .order_by(LiveApprovalEvent.sequence_number.desc())
        )
    )
    if checkpoint_type is not None:
        events = [item for item in events if item.checkpoint_type == checkpoint_type]

    status_state = "available" if events else "unavailable"
    items = [
        LiveApprovalEventResponse(
            approval_event_id=item.id,
            live_trading_profile_id=item.live_trading_profile_id,
            checkpoint_type=item.checkpoint_type,
            approval_state=item.approval_state,
            lifecycle_state=profile.lifecycle_state if profile is not None else "unknown",
            operating_mode=profile.operating_mode if profile is not None else "paper",
            expires_at=item.expires_at,
            renewal_condition=item.renewal_condition,
            idempotency_key=item.idempotency_key,
        )
        for item in events
    ]

    return LiveApprovalStatusReadModelResponse(
        live_trading_profile_id=live_trading_profile_id,
        status_state=status_state,
        total_events=len(items),
        items=items,
        warnings=warnings,
    )


@router.post("/approvals/checkpoints", response_model=LiveApprovalEventResponse)
async def create_live_approval_checkpoint(
    payload: LiveApprovalCheckpointCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveApprovalEventResponse:
    result = await record_live_approval_checkpoint(
        db=db,
        request=LiveApprovalCheckpointRequest(
            live_trading_profile_id=payload.live_trading_profile_id,
            checkpoint_type=payload.checkpoint_type,
            approver_id=current_user["id"],
            approver_role=payload.approver_role,
            rationale=payload.rationale,
            approval_scope=payload.approval_scope,
            expires_at=payload.expires_at,
            renewal_condition=payload.renewal_condition,
            requested_by=current_user["id"],
            provenance_metadata=payload.provenance_metadata,
            idempotency_key=payload.idempotency_key,
        ),
    )
    return LiveApprovalEventResponse(
        approval_event_id=result.approval_event_id,
        live_trading_profile_id=result.live_trading_profile_id,
        checkpoint_type=result.checkpoint_type,
        approval_state=result.approval_state,
        lifecycle_state=result.lifecycle_state,
        operating_mode=result.operating_mode,
        expires_at=result.expires_at,
        renewal_condition=result.renewal_condition,
        idempotency_key=result.idempotency_key,
    )


@router.post("/approvals/revoke", response_model=LiveApprovalEventResponse)
async def revoke_live_approval_checkpoint(
    payload: LiveApprovalStateChangeCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveApprovalEventResponse:
    result = await revoke_live_approval(
        db=db,
        request=LiveApprovalStateChangeRequest(
            live_trading_profile_id=payload.live_trading_profile_id,
            checkpoint_type=payload.checkpoint_type,
            approver_id=current_user["id"],
            approver_role=payload.approver_role,
            rationale=payload.rationale,
            approval_scope=payload.approval_scope,
            requested_by=current_user["id"],
            provenance_metadata=payload.provenance_metadata,
            idempotency_key=payload.idempotency_key,
        ),
    )
    return LiveApprovalEventResponse(
        approval_event_id=result.approval_event_id,
        live_trading_profile_id=result.live_trading_profile_id,
        checkpoint_type=result.checkpoint_type,
        approval_state=result.approval_state,
        lifecycle_state=result.lifecycle_state,
        operating_mode=result.operating_mode,
        expires_at=result.expires_at,
        renewal_condition=result.renewal_condition,
        idempotency_key=result.idempotency_key,
    )


@router.post("/approvals/suspend", response_model=LiveApprovalEventResponse)
async def suspend_live_approval_checkpoint(
    payload: LiveApprovalStateChangeCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> LiveApprovalEventResponse:
    result = await suspend_live_approval(
        db=db,
        request=LiveApprovalStateChangeRequest(
            live_trading_profile_id=payload.live_trading_profile_id,
            checkpoint_type=payload.checkpoint_type,
            approver_id=current_user["id"],
            approver_role=payload.approver_role,
            rationale=payload.rationale,
            approval_scope=payload.approval_scope,
            requested_by=current_user["id"],
            provenance_metadata=payload.provenance_metadata,
            idempotency_key=payload.idempotency_key,
        ),
    )
    return LiveApprovalEventResponse(
        approval_event_id=result.approval_event_id,
        live_trading_profile_id=result.live_trading_profile_id,
        checkpoint_type=result.checkpoint_type,
        approval_state=result.approval_state,
        lifecycle_state=result.lifecycle_state,
        operating_mode=result.operating_mode,
        expires_at=result.expires_at,
        renewal_condition=result.renewal_condition,
        idempotency_key=result.idempotency_key,
    )


@router.get("/reconciliation/status", response_model=LiveReconciliationSummaryResponse)
async def read_live_reconciliation_status(
    live_trading_profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LiveReconciliationSummaryResponse:
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    warnings = _profile_warnings(profile)

    events = list(
        await db.scalars(
            select(LiveReconciliationEvent)
            .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
            .order_by(LiveReconciliationEvent.sequence_number.desc())
        )
    )
    latest = events[0] if events else None

    open_count = sum(1 for item in events if item.reconciliation_status == "open")
    partially_filled_count = sum(1 for item in events if item.reconciliation_status == "partially_filled")
    filled_count = sum(1 for item in events if item.reconciliation_status == "filled")
    canceled_count = sum(1 for item in events if item.reconciliation_status == "canceled")
    rejected_count = sum(1 for item in events if item.reconciliation_status == "rejected")

    return LiveReconciliationSummaryResponse(
        live_trading_profile_id=live_trading_profile_id,
        status_state="available" if events else "unavailable",
        total_events=len(events),
        open_count=open_count,
        partially_filled_count=partially_filled_count,
        filled_count=filled_count,
        canceled_count=canceled_count,
        rejected_count=rejected_count,
        unresolved_count=open_count + partially_filled_count,
        latest_event_type=latest.event_type if latest is not None else None,
        latest_reconciliation_status=latest.reconciliation_status if latest is not None else None,
        latest_provider_name=latest.provider_name if latest is not None else None,
        latest_recorded_at=latest.recorded_at if latest is not None else None,
        warnings=warnings,
    )


@router.get("/execution-quality", response_model=LiveExecutionQualityReadModelResponse)
async def read_live_execution_quality_surface(
    live_trading_profile_id: uuid.UUID,
    symbol: str | None = None,
    provider_name: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveExecutionQualityReadModelResponse:
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    model = await read_live_execution_quality(
        db=db,
        live_trading_profile_id=live_trading_profile_id,
        symbol=symbol,
        provider_name=provider_name,
    )
    return LiveExecutionQualityReadModelResponse(
        live_trading_profile_id=model.live_trading_profile_id,
        status_state="available" if model.total_records > 0 else "unavailable",
        total_records=model.total_records,
        available_slippage_records=model.available_slippage_records,
        unknown_or_unavailable_records=model.unknown_or_unavailable_records,
        average_slippage_bps=model.average_slippage_bps,
        items=[
            LiveExecutionQualityReadModelItemResponse(
                quality_metric_id=item.quality_metric_id,
                provider_name=item.provider_name,
                symbol=item.symbol,
                side=item.side,
                expected_price=item.expected_price,
                expected_price_state=item.expected_price_state,
                actual_fill_price=item.actual_fill_price,
                actual_price_state=item.actual_price_state,
                slippage_abs=item.slippage_abs,
                slippage_bps=item.slippage_bps,
                slippage_state=item.slippage_state,
                market_context=item.market_context,
                telemetry_context=item.telemetry_context,
                recorded_at=item.recorded_at,
            )
            for item in model.items
        ],
        warnings=_profile_warnings(profile),
    )


@router.get("/compliance/evidence", response_model=LiveComplianceEvidenceReadModelResponse)
async def read_live_compliance_evidence_surface(
    live_trading_profile_id: uuid.UUID,
    event_type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveComplianceEvidenceReadModelResponse:
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    model = await read_live_compliance_evidence(
        db=db,
        live_trading_profile_id=live_trading_profile_id,
        event_type=event_type,
    )
    return LiveComplianceEvidenceReadModelResponse(
        live_trading_profile_id=model.live_trading_profile_id,
        status_state="available" if model.total_records > 0 else "unavailable",
        total_records=model.total_records,
        items=[
            LiveComplianceEvidenceItemResponse(
                evidence_record_id=item.evidence_record_id,
                event_type=item.event_type,
                attributable_actor_id=item.attributable_actor_id,
                attributable_actor_role=item.attributable_actor_role,
                action_name=item.action_name,
                action_source=item.action_source,
                action_summary=item.action_summary,
                provenance_hash=item.provenance_hash,
                linked_records=item.linked_records,
                evidence_payload=item.evidence_payload,
                provenance=item.provenance,
                recorded_at=item.recorded_at,
            )
            for item in model.items
        ],
        warnings=_profile_warnings(profile),
    )


@router.get("/compliance/export", response_model=LiveComplianceExportBundleResponse)
async def export_live_compliance_surface(
    live_trading_profile_id: uuid.UUID,
    exported_by: str,
    event_type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LiveComplianceExportBundleResponse:
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    bundle = await export_live_compliance_bundle(
        db=db,
        request=LiveComplianceExportRequest(
            live_trading_profile_id=live_trading_profile_id,
            exported_by=exported_by,
            event_type=event_type,
        ),
    )
    return LiveComplianceExportBundleResponse(
        live_trading_profile_id=bundle.live_trading_profile_id,
        exported_by=bundle.exported_by,
        exported_at=bundle.exported_at,
        status_state="available" if bundle.total_records > 0 else "unavailable",
        total_records=bundle.total_records,
        records=[
            LiveComplianceEvidenceItemResponse(
                evidence_record_id=item.evidence_record_id,
                event_type=item.event_type,
                attributable_actor_id=item.attributable_actor_id,
                attributable_actor_role=item.attributable_actor_role,
                action_name=item.action_name,
                action_source=item.action_source,
                action_summary=item.action_summary,
                provenance_hash=item.provenance_hash,
                linked_records=item.linked_records,
                evidence_payload=item.evidence_payload,
                provenance=item.provenance,
                recorded_at=item.recorded_at,
            )
            for item in bundle.records
        ],
        warnings=_profile_warnings(profile)
        + [
            LiveOperatorWarningResponse(
                code="read_only_export",
                message="Compliance export is read-only and does not mutate audit evidence records.",
            )
        ],
    )
