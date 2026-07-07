from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.live.approval import evaluate_live_approval_gate
from app.services.live.broker_adapters import (
    BrokerAdapterContract,
    BrokerIdempotencyContract,
    NormalizedBrokerOrderRequest,
    RequiredOrchestrationIdentifiers,
)
from app.services.live.contracts import (
    LiveExecutionOrchestrationRequest,
    LiveExecutionOrchestrationResult,
    LiveRiskVerificationResult,
)


RiskVerifier = Callable[[uuid.UUID], Awaitable[LiveRiskVerificationResult]]


def build_live_execution_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    provider_name: str,
    adapter_request_id: str,
    risk_decision_id: uuid.UUID,
    approval_event_id: uuid.UUID,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "provider_name": provider_name,
            "adapter_request_id": adapter_request_id,
            "risk_decision_id": str(risk_decision_id),
            "approval_event_id": str(approval_event_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_execution_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    provider_name: str,
    idempotency_key: str,
    recorded_at: datetime,
    event_payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "sequence_number": sequence_number,
            "event_type": event_type,
            "provider_name": provider_name,
            "idempotency_key": idempotency_key,
            "recorded_at": recorded_at.isoformat(),
            "event_payload": event_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def select_live_broker_adapter(
    *,
    provider_name: str,
    adapters: dict[str, BrokerAdapterContract],
) -> BrokerAdapterContract | None:
    return adapters.get(provider_name)


async def orchestrate_live_execution(
    *,
    db: AsyncSession,
    request: LiveExecutionOrchestrationRequest,
    adapters: dict[str, BrokerAdapterContract],
    verify_risk_decision: RiskVerifier,
) -> LiveExecutionOrchestrationResult:
    idempotency_key = request.idempotency_key or build_live_execution_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        provider_name=request.provider_name,
        adapter_request_id=request.adapter_request_id,
        risk_decision_id=request.risk_decision_id,
        approval_event_id=request.approval_event_id,
    )
    existing = await db.scalar(
        select(LiveExecutionEvent)
        .where(LiveExecutionEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return LiveExecutionOrchestrationResult(
            accepted=existing.event_type == "execution_intent_created",
            status="replayed",
            reason=None,
            provider_name=existing.provider_name,
            live_trading_profile_id=existing.live_trading_profile_id,
            execution_event_id=existing.id,
            approval_event_id=existing.approval_event_id,
            risk_decision_id=existing.risk_decision_id,
            audit_correlation_id=existing.audit_correlation_id,
            adapter_request_id=request.adapter_request_id,
            idempotency_key=idempotency_key,
        )

    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.id == request.live_trading_profile_id)
        .limit(1)
    )
    if profile is None:
        return LiveExecutionOrchestrationResult(
            accepted=False,
            status="blocked",
            reason="live_profile_not_found",
            provider_name=request.provider_name,
            live_trading_profile_id=request.live_trading_profile_id,
            execution_event_id=None,
            approval_event_id=request.approval_event_id,
            risk_decision_id=request.risk_decision_id,
            audit_correlation_id=request.audit_correlation_id,
            adapter_request_id=request.adapter_request_id,
            idempotency_key=idempotency_key,
        )

    if profile.operating_mode != "live" or profile.lifecycle_state != "enabled":
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason="approved_live_operating_mode_required",
        )

    if not profile.human_approval_recorded:
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason="human_approval_required",
        )

    if profile.risk_authority_model != "risk_engine_final":
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason="risk_engine_final_authority_required",
        )

    approval_gate = await evaluate_live_approval_gate(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        checkpoint_type="first_live_enablement",
    )
    if not approval_gate.allowed:
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason=approval_gate.reason or "approval_gate_failed",
        )

    if approval_gate.matched_approval_event_id != request.approval_event_id:
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason="approval_event_id_mismatch",
        )

    risk_verification = await verify_risk_decision(request.risk_decision_id)
    if not risk_verification.approved:
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason=risk_verification.reason or "risk_decision_not_approved",
        )

    adapter = select_live_broker_adapter(provider_name=request.provider_name, adapters=adapters)
    if adapter is None:
        return await _record_blocked_execution(
            db=db,
            profile=profile,
            request=request,
            idempotency_key=idempotency_key,
            reason="adapter_not_registered",
        )

    normalized_request = NormalizedBrokerOrderRequest(
        orchestration_ids=RequiredOrchestrationIdentifiers(
            risk_decision_id=request.risk_decision_id,
            approval_event_id=request.approval_event_id,
            audit_correlation_id=request.audit_correlation_id,
        ),
        idempotency=BrokerIdempotencyContract(
            idempotency_key=idempotency_key,
            idempotency_group="live_execution_intent",
        ),
        adapter_request_id=request.adapter_request_id,
        broker_account_ref=request.broker_account_ref,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        quantity=Decimal(request.quantity),
        limit_price=Decimal(request.limit_price) if request.limit_price is not None else None,
        stop_price=Decimal(request.stop_price) if request.stop_price is not None else None,
        time_in_force=request.time_in_force,
        requested_at=datetime.now(timezone.utc),
        metadata={
            "audit_correlation_id": request.audit_correlation_id,
            **request.provenance_metadata,
        },
    )
    provider_envelope = adapter.build_provider_order_request(request=normalized_request)

    return await _record_execution_intent(
        db=db,
        profile=profile,
        request=request,
        idempotency_key=idempotency_key,
        provider_payload=provider_envelope.payload,
    )


async def _record_execution_intent(
    *,
    db: AsyncSession,
    profile: LiveTradingProfile,
    request: LiveExecutionOrchestrationRequest,
    idempotency_key: str,
    provider_payload: dict[str, object],
) -> LiveExecutionOrchestrationResult:
    recorded_at = datetime.now(timezone.utc)

    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveExecutionEvent.sequence_number)).where(
                LiveExecutionEvent.live_trading_profile_id == profile.id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        event_payload = {
            "adapter_request_id": request.adapter_request_id,
            "symbol": request.symbol,
            "side": request.side,
            "order_type": request.order_type,
            "quantity": request.quantity,
            "time_in_force": request.time_in_force,
            "provider_payload": provider_payload,
        }
        event = LiveExecutionEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_execution_event_hash(
                live_trading_profile_id=profile.id,
                sequence_number=sequence_number,
                event_type="execution_intent_created",
                provider_name=request.provider_name,
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                event_payload=event_payload,
            ),
            live_trading_profile_id=profile.id,
            sequence_number=sequence_number,
            event_type="execution_intent_created",
            provider_name=request.provider_name,
            risk_decision_id=request.risk_decision_id,
            approval_event_id=request.approval_event_id,
            audit_correlation_id=request.audit_correlation_id,
            operating_mode=profile.operating_mode,
            paper_default_mode=profile.paper_default_mode,
            risk_authority_model=profile.risk_authority_model,
            event_payload=event_payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

    return LiveExecutionOrchestrationResult(
        accepted=True,
        status="prepared",
        reason=None,
        provider_name=request.provider_name,
        live_trading_profile_id=profile.id,
        execution_event_id=event.id,
        approval_event_id=request.approval_event_id,
        risk_decision_id=request.risk_decision_id,
        audit_correlation_id=request.audit_correlation_id,
        adapter_request_id=request.adapter_request_id,
        idempotency_key=idempotency_key,
    )


async def _record_blocked_execution(
    *,
    db: AsyncSession,
    profile: LiveTradingProfile,
    request: LiveExecutionOrchestrationRequest,
    idempotency_key: str,
    reason: str,
) -> LiveExecutionOrchestrationResult:
    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveExecutionEvent.sequence_number)).where(
                LiveExecutionEvent.live_trading_profile_id == profile.id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        event_payload = {
            "adapter_request_id": request.adapter_request_id,
            "reason": reason,
        }
        event = LiveExecutionEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_execution_event_hash(
                live_trading_profile_id=profile.id,
                sequence_number=sequence_number,
                event_type="execution_blocked",
                provider_name=request.provider_name,
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                event_payload=event_payload,
            ),
            live_trading_profile_id=profile.id,
            sequence_number=sequence_number,
            event_type="execution_blocked",
            provider_name=request.provider_name,
            risk_decision_id=request.risk_decision_id,
            approval_event_id=request.approval_event_id,
            audit_correlation_id=request.audit_correlation_id,
            operating_mode=profile.operating_mode,
            paper_default_mode=profile.paper_default_mode,
            risk_authority_model=profile.risk_authority_model,
            event_payload=event_payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

    return LiveExecutionOrchestrationResult(
        accepted=False,
        status="blocked",
        reason=reason,
        provider_name=request.provider_name,
        live_trading_profile_id=profile.id,
        execution_event_id=event.id,
        approval_event_id=request.approval_event_id,
        risk_decision_id=request.risk_decision_id,
        audit_correlation_id=request.audit_correlation_id,
        adapter_request_id=request.adapter_request_id,
        idempotency_key=idempotency_key,
    )