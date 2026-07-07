from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_resilience_event import LiveResilienceEvent
from app.services.live.approval import evaluate_live_approval_gate
from app.services.live.contracts import (
    LiveKillSwitchRequest,
    LiveOutageDetectionRequest,
    LiveRecoveryRequest,
    LiveResilienceEventResult,
    LiveSubmissionGuardResult,
)


def build_live_resilience_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    event_type: str,
    reason_code: str,
    requested_by: str,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "event_type": event_type,
            "reason_code": reason_code,
            "requested_by": requested_by,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_resilience_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    idempotency_key: str,
    recorded_at: datetime,
    event_payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "sequence_number": sequence_number,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "recorded_at": recorded_at.isoformat(),
            "event_payload": event_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def evaluate_live_submission_guard(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
) -> LiveSubmissionGuardResult:
    latest = await db.scalar(
        select(LiveResilienceEvent)
        .where(LiveResilienceEvent.live_trading_profile_id == live_trading_profile_id)
        .order_by(LiveResilienceEvent.sequence_number.desc())
        .limit(1)
    )
    if latest is None:
        return LiveSubmissionGuardResult(
            allowed=True,
            reason=None,
            submission_blocked=False,
            kill_switch_engaged=False,
            outage_detected=False,
            ambiguity_detected=False,
            reapproval_required=False,
        )

    if latest.submission_blocked:
        return LiveSubmissionGuardResult(
            allowed=False,
            reason=latest.reason_code,
            submission_blocked=True,
            kill_switch_engaged=latest.kill_switch_engaged,
            outage_detected=latest.outage_detected,
            ambiguity_detected=latest.ambiguity_detected,
            reapproval_required=latest.reapproval_required,
        )

    if latest.reapproval_required:
        return LiveSubmissionGuardResult(
            allowed=False,
            reason="reapproval_required",
            submission_blocked=False,
            kill_switch_engaged=latest.kill_switch_engaged,
            outage_detected=latest.outage_detected,
            ambiguity_detected=latest.ambiguity_detected,
            reapproval_required=True,
        )

    return LiveSubmissionGuardResult(
        allowed=True,
        reason=None,
        submission_blocked=False,
        kill_switch_engaged=False,
        outage_detected=False,
        ambiguity_detected=False,
        reapproval_required=False,
    )


async def engage_live_kill_switch(
    *,
    db: AsyncSession,
    request: LiveKillSwitchRequest,
) -> LiveResilienceEventResult:
    idempotency_key = request.idempotency_key or build_live_resilience_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="kill_switch_engaged",
        reason_code=request.reason_code,
        requested_by=request.requested_by,
    )
    return await _record_resilience_event(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="kill_switch_engaged",
        provider_name=None,
        reason_code=request.reason_code,
        submission_blocked=True,
        kill_switch_engaged=True,
        outage_detected=False,
        ambiguity_detected=False,
        reapproval_required=True,
        approval_event_id=None,
        requested_by=request.requested_by,
        provenance_metadata=request.provenance_metadata,
        idempotency_key=idempotency_key,
        event_payload={"reason_code": request.reason_code, "action": "engage_kill_switch"},
    )


async def engage_live_emergency_stop(
    *,
    db: AsyncSession,
    request: LiveKillSwitchRequest,
) -> LiveResilienceEventResult:
    idempotency_key = request.idempotency_key or build_live_resilience_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="emergency_stop_engaged",
        reason_code=request.reason_code,
        requested_by=request.requested_by,
    )
    return await _record_resilience_event(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="emergency_stop_engaged",
        provider_name=None,
        reason_code=request.reason_code,
        submission_blocked=True,
        kill_switch_engaged=True,
        outage_detected=False,
        ambiguity_detected=False,
        reapproval_required=True,
        approval_event_id=None,
        requested_by=request.requested_by,
        provenance_metadata=request.provenance_metadata,
        idempotency_key=idempotency_key,
        event_payload={"reason_code": request.reason_code, "action": "emergency_stop"},
    )


async def record_live_broker_outage(
    *,
    db: AsyncSession,
    request: LiveOutageDetectionRequest,
) -> LiveResilienceEventResult:
    reason_code = "outage_ambiguous_state" if request.ambiguity_detected else request.reason_code
    idempotency_key = request.idempotency_key or build_live_resilience_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="outage_detected",
        reason_code=reason_code,
        requested_by=request.requested_by,
    )
    return await _record_resilience_event(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="outage_detected",
        provider_name=request.provider_name,
        reason_code=reason_code,
        submission_blocked=True,
        kill_switch_engaged=False,
        outage_detected=True,
        ambiguity_detected=request.ambiguity_detected,
        reapproval_required=True,
        approval_event_id=None,
        requested_by=request.requested_by,
        provenance_metadata=request.provenance_metadata,
        idempotency_key=idempotency_key,
        event_payload={
            "provider_name": request.provider_name,
            "reason_code": reason_code,
            "ambiguity_detected": request.ambiguity_detected,
        },
    )


async def request_live_recovery(
    *,
    db: AsyncSession,
    request: LiveRecoveryRequest,
) -> LiveResilienceEventResult:
    idempotency_key = request.idempotency_key or build_live_resilience_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="recovery_requested",
        reason_code="recovery_requested",
        requested_by=request.requested_by,
    )
    return await _record_resilience_event(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="recovery_requested",
        provider_name=None,
        reason_code="recovery_requested",
        submission_blocked=True,
        kill_switch_engaged=False,
        outage_detected=True,
        ambiguity_detected=False,
        reapproval_required=True,
        approval_event_id=request.approval_event_id,
        requested_by=request.requested_by,
        provenance_metadata=request.provenance_metadata,
        idempotency_key=idempotency_key,
        event_payload={"rationale": request.rationale},
    )


async def approve_live_recovery(
    *,
    db: AsyncSession,
    request: LiveRecoveryRequest,
) -> LiveResilienceEventResult:
    if request.approval_event_id is None:
        return await _record_resilience_event(
            db=db,
            live_trading_profile_id=request.live_trading_profile_id,
            event_type="recovery_rejected",
            provider_name=None,
            reason_code="reapproval_event_required",
            submission_blocked=True,
            kill_switch_engaged=False,
            outage_detected=True,
            ambiguity_detected=False,
            reapproval_required=True,
            approval_event_id=None,
            requested_by=request.requested_by,
            provenance_metadata=request.provenance_metadata,
            idempotency_key=request.idempotency_key or build_live_resilience_idempotency_key(
                live_trading_profile_id=request.live_trading_profile_id,
                event_type="recovery_rejected",
                reason_code="reapproval_event_required",
                requested_by=request.requested_by,
            ),
            event_payload={"rationale": request.rationale},
        )

    approval_gate = await evaluate_live_approval_gate(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        checkpoint_type="first_live_enablement",
    )
    if not approval_gate.allowed or approval_gate.matched_approval_event_id != request.approval_event_id:
        return await _record_resilience_event(
            db=db,
            live_trading_profile_id=request.live_trading_profile_id,
            event_type="recovery_rejected",
            provider_name=None,
            reason_code="reapproval_validation_failed",
            submission_blocked=True,
            kill_switch_engaged=False,
            outage_detected=True,
            ambiguity_detected=False,
            reapproval_required=True,
            approval_event_id=request.approval_event_id,
            requested_by=request.requested_by,
            provenance_metadata=request.provenance_metadata,
            idempotency_key=request.idempotency_key or build_live_resilience_idempotency_key(
                live_trading_profile_id=request.live_trading_profile_id,
                event_type="recovery_rejected",
                reason_code="reapproval_validation_failed",
                requested_by=request.requested_by,
            ),
            event_payload={"rationale": request.rationale},
        )

    idempotency_key = request.idempotency_key or build_live_resilience_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="recovery_approved",
        reason_code="recovery_approved",
        requested_by=request.requested_by,
    )
    return await _record_resilience_event(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type="recovery_approved",
        provider_name=None,
        reason_code="recovery_approved",
        submission_blocked=False,
        kill_switch_engaged=False,
        outage_detected=False,
        ambiguity_detected=False,
        reapproval_required=False,
        approval_event_id=request.approval_event_id,
        requested_by=request.requested_by,
        provenance_metadata=request.provenance_metadata,
        idempotency_key=idempotency_key,
        event_payload={"rationale": request.rationale},
    )


async def _record_resilience_event(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    event_type: str,
    provider_name: str | None,
    reason_code: str,
    submission_blocked: bool,
    kill_switch_engaged: bool,
    outage_detected: bool,
    ambiguity_detected: bool,
    reapproval_required: bool,
    approval_event_id: uuid.UUID | None,
    requested_by: str,
    provenance_metadata: dict[str, object],
    idempotency_key: str,
    event_payload: dict[str, object],
) -> LiveResilienceEventResult:
    existing = await db.scalar(
        select(LiveResilienceEvent).where(LiveResilienceEvent.idempotency_key == idempotency_key).limit(1)
    )
    if existing is not None:
        return LiveResilienceEventResult(
            event_id=existing.id,
            live_trading_profile_id=existing.live_trading_profile_id,
            event_type=existing.event_type,
            submission_blocked=existing.submission_blocked,
            reapproval_required=existing.reapproval_required,
            reason_code=existing.reason_code,
            idempotency_key=idempotency_key,
        )

    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveResilienceEvent.sequence_number)).where(
                LiveResilienceEvent.live_trading_profile_id == live_trading_profile_id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        event_record = LiveResilienceEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_resilience_event_hash(
                live_trading_profile_id=live_trading_profile_id,
                sequence_number=sequence_number,
                event_type=event_type,
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                event_payload=event_payload,
            ),
            live_trading_profile_id=live_trading_profile_id,
            sequence_number=sequence_number,
            event_type=event_type,
            provider_name=provider_name,
            reason_code=reason_code,
            submission_blocked=submission_blocked,
            kill_switch_engaged=kill_switch_engaged,
            outage_detected=outage_detected,
            ambiguity_detected=ambiguity_detected,
            reapproval_required=reapproval_required,
            approval_event_id=approval_event_id,
            event_payload=event_payload,
            provenance={
                "requested_by": requested_by,
                "recorded_at": recorded_at.isoformat(),
                **provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(event_record)
        await db.flush()

    return LiveResilienceEventResult(
        event_id=event_record.id,
        live_trading_profile_id=event_record.live_trading_profile_id,
        event_type=event_record.event_type,
        submission_blocked=event_record.submission_blocked,
        reapproval_required=event_record.reapproval_required,
        reason_code=event_record.reason_code,
        idempotency_key=idempotency_key,
    )
