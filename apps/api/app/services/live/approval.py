from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.live.contracts import (
    LiveApprovalCheckpointRequest,
    LiveApprovalCheckpointResult,
    LiveApprovalGateResult,
    LiveApprovalStateChangeRequest,
)


async def _commit_if_supported(*, db: AsyncSession) -> None:
    if hasattr(db, "commit"):
        await db.commit()


def build_live_approval_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    checkpoint_type: str,
    event_type: str,
    approver_id: str,
    rationale: str,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "checkpoint_type": checkpoint_type,
            "event_type": event_type,
            "approver_id": approver_id,
            "rationale": rationale,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_approval_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    checkpoint_type: str,
    approver_id: str,
    approval_state: str,
    recorded_at: datetime,
    payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "sequence_number": sequence_number,
            "event_type": event_type,
            "checkpoint_type": checkpoint_type,
            "approver_id": approver_id,
            "approval_state": approval_state,
            "recorded_at": recorded_at.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_expired(*, expires_at: datetime | None, observed_at: datetime) -> bool:
    if expires_at is None:
        return False
    return expires_at <= observed_at


async def evaluate_live_approval_gate(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    checkpoint_type: str,
    observed_at: datetime | None = None,
) -> LiveApprovalGateResult:
    now = observed_at or datetime.now(timezone.utc)
    profile = await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    if profile is None:
        return LiveApprovalGateResult(allowed=False, reason="live_profile_not_found", matched_approval_event_id=None)
    if not profile.paper_default_mode:
        return LiveApprovalGateResult(
            allowed=False,
            reason="paper_default_boundary_violated",
            matched_approval_event_id=None,
        )
    if profile.risk_authority_model != "risk_engine_final":
        return LiveApprovalGateResult(
            allowed=False,
            reason="risk_engine_final_authority_required",
            matched_approval_event_id=None,
        )

    latest = await db.scalar(
        select(LiveApprovalEvent)
        .where(
            LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id,
            LiveApprovalEvent.checkpoint_type == checkpoint_type,
        )
        .order_by(LiveApprovalEvent.sequence_number.desc())
        .limit(1)
    )
    if latest is None:
        return LiveApprovalGateResult(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)
    if latest.approval_state != "approved":
        return LiveApprovalGateResult(
            allowed=False,
            reason="approval_not_active",
            matched_approval_event_id=latest.id,
        )
    if _is_expired(expires_at=latest.expires_at, observed_at=now):
        return LiveApprovalGateResult(
            allowed=False,
            reason="approval_expired",
            matched_approval_event_id=latest.id,
        )
    return LiveApprovalGateResult(allowed=True, reason=None, matched_approval_event_id=latest.id)


async def record_live_approval_checkpoint(
    *,
    db: AsyncSession,
    request: LiveApprovalCheckpointRequest,
) -> LiveApprovalCheckpointResult:
    idempotency_key = request.idempotency_key or build_live_approval_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        checkpoint_type=request.checkpoint_type,
        event_type="approval_granted",
        approver_id=request.approver_id,
        rationale=request.rationale,
    )

    existing = await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        profile = await db.scalar(
            select(LiveTradingProfile)
            .where(LiveTradingProfile.id == existing.live_trading_profile_id)
            .limit(1)
        )
        if profile is None:
            raise RuntimeError("live profile missing for existing approval idempotency key")
        return LiveApprovalCheckpointResult(
            approval_event_id=existing.id,
            live_trading_profile_id=existing.live_trading_profile_id,
            checkpoint_type=existing.checkpoint_type,
            approval_state=existing.approval_state,
            lifecycle_state=profile.lifecycle_state,
            operating_mode=profile.operating_mode,
            expires_at=existing.expires_at,
            renewal_condition=existing.renewal_condition,
            idempotency_key=idempotency_key,
        )

    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.id == request.live_trading_profile_id)
        .limit(1)
    )
    if profile is None:
        raise ValueError("live trading profile not found")

    recorded_at = datetime.now(timezone.utc)
    next_state = "approved"
    payload = {
        "checkpoint_type": request.checkpoint_type,
        "approval_scope": request.approval_scope,
        "rationale": request.rationale,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "renewal_condition": request.renewal_condition,
    }

    existing_sequence = await db.scalar(
        select(func.max(LiveApprovalEvent.sequence_number))
        .where(LiveApprovalEvent.live_trading_profile_id == request.live_trading_profile_id)
    )
    sequence_number = int(existing_sequence or 0) + 1

    approval_event = LiveApprovalEvent(
        idempotency_key=idempotency_key,
        event_hash=build_live_approval_event_hash(
            live_trading_profile_id=request.live_trading_profile_id,
            sequence_number=sequence_number,
            event_type="approval_granted",
            checkpoint_type=request.checkpoint_type,
            approver_id=request.approver_id,
            approval_state="approved",
            recorded_at=recorded_at,
            payload=payload,
        ),
        live_trading_profile_id=request.live_trading_profile_id,
        sequence_number=sequence_number,
        event_type="approval_granted",
        checkpoint_type=request.checkpoint_type,
        approval_state="approved",
        approver_id=request.approver_id,
        approver_role=request.approver_role,
        rationale=request.rationale,
        approval_scope=request.approval_scope,
        expires_at=request.expires_at,
        renewal_condition=request.renewal_condition,
        event_payload=payload,
        provenance={
            "requested_by": request.requested_by,
            "recorded_at": recorded_at.isoformat(),
            **request.provenance_metadata,
        },
        immutable_contract_version="v1",
        recorded_at=recorded_at,
    )
    db.add(approval_event)

    profile.approval_state = "approved"
    profile.human_approval_recorded = True
    if not (profile.operating_mode == "live" and profile.lifecycle_state in {"enabled", "suspended"}):
        # Do not regress an already-live profile's lifecycle_state to "approved" merely because a
        # subsequent, differently-typed checkpoint (e.g. bounded_proving_entry) is recorded --
        # ck_live_trading_profiles_live_mode_lifecycle_boundary requires lifecycle_state in
        # ('enabled','suspended') whenever operating_mode != 'paper'.
        profile.lifecycle_state = next_state

    if request.checkpoint_type == "first_live_enablement":
        # Approval for first live enablement permits transitioning to enabled under explicit checkpoint.
        profile.operating_mode = "live"
        profile.lifecycle_state = "enabled"

    await db.flush()
    await _commit_if_supported(db=db)

    return LiveApprovalCheckpointResult(
        approval_event_id=approval_event.id,
        live_trading_profile_id=approval_event.live_trading_profile_id,
        checkpoint_type=approval_event.checkpoint_type,
        approval_state=approval_event.approval_state,
        lifecycle_state=profile.lifecycle_state,
        operating_mode=profile.operating_mode,
        expires_at=approval_event.expires_at,
        renewal_condition=approval_event.renewal_condition,
        idempotency_key=idempotency_key,
    )


async def revoke_live_approval(
    *,
    db: AsyncSession,
    request: LiveApprovalStateChangeRequest,
) -> LiveApprovalCheckpointResult:
    return await _record_approval_state_change(
        db=db,
        request=request,
        event_type="approval_revoked",
        approval_state="revoked",
    )


async def suspend_live_approval(
    *,
    db: AsyncSession,
    request: LiveApprovalStateChangeRequest,
) -> LiveApprovalCheckpointResult:
    return await _record_approval_state_change(
        db=db,
        request=request,
        event_type="approval_suspended",
        approval_state="suspended",
    )


async def _record_approval_state_change(
    *,
    db: AsyncSession,
    request: LiveApprovalStateChangeRequest,
    event_type: str,
    approval_state: str,
) -> LiveApprovalCheckpointResult:
    idempotency_key = request.idempotency_key or build_live_approval_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        checkpoint_type=request.checkpoint_type,
        event_type=event_type,
        approver_id=request.approver_id,
        rationale=request.rationale,
    )

    existing = await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        profile = await db.scalar(
            select(LiveTradingProfile)
            .where(LiveTradingProfile.id == existing.live_trading_profile_id)
            .limit(1)
        )
        if profile is None:
            raise RuntimeError("live profile missing for existing approval idempotency key")
        return LiveApprovalCheckpointResult(
            approval_event_id=existing.id,
            live_trading_profile_id=existing.live_trading_profile_id,
            checkpoint_type=existing.checkpoint_type,
            approval_state=existing.approval_state,
            lifecycle_state=profile.lifecycle_state,
            operating_mode=profile.operating_mode,
            expires_at=existing.expires_at,
            renewal_condition=existing.renewal_condition,
            idempotency_key=idempotency_key,
        )

    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.id == request.live_trading_profile_id)
        .limit(1)
    )
    if profile is None:
        raise ValueError("live trading profile not found")

    recorded_at = datetime.now(timezone.utc)
    payload = {
        "checkpoint_type": request.checkpoint_type,
        "approval_scope": request.approval_scope,
        "rationale": request.rationale,
    }

    existing_sequence = await db.scalar(
        select(func.max(LiveApprovalEvent.sequence_number))
        .where(LiveApprovalEvent.live_trading_profile_id == request.live_trading_profile_id)
    )
    sequence_number = int(existing_sequence or 0) + 1

    approval_event = LiveApprovalEvent(
        idempotency_key=idempotency_key,
        event_hash=build_live_approval_event_hash(
            live_trading_profile_id=request.live_trading_profile_id,
            sequence_number=sequence_number,
            event_type=event_type,
            checkpoint_type=request.checkpoint_type,
            approver_id=request.approver_id,
            approval_state=approval_state,
            recorded_at=recorded_at,
            payload=payload,
        ),
        live_trading_profile_id=request.live_trading_profile_id,
        sequence_number=sequence_number,
        event_type=event_type,
        checkpoint_type=request.checkpoint_type,
        approval_state=approval_state,
        approver_id=request.approver_id,
        approver_role=request.approver_role,
        rationale=request.rationale,
        approval_scope=request.approval_scope,
        expires_at=None,
        renewal_condition=None,
        event_payload=payload,
        provenance={
            "requested_by": request.requested_by,
            "recorded_at": recorded_at.isoformat(),
            **request.provenance_metadata,
        },
        immutable_contract_version="v1",
        recorded_at=recorded_at,
    )
    db.add(approval_event)

    profile.approval_state = "revoked" if approval_state == "revoked" else "pending"
    profile.human_approval_recorded = False
    profile.lifecycle_state = "suspended"
    profile.operating_mode = "paper"

    await db.flush()
    await _commit_if_supported(db=db)

    return LiveApprovalCheckpointResult(
        approval_event_id=approval_event.id,
        live_trading_profile_id=approval_event.live_trading_profile_id,
        checkpoint_type=approval_event.checkpoint_type,
        approval_state=approval_event.approval_state,
        lifecycle_state=profile.lifecycle_state,
        operating_mode=profile.operating_mode,
        expires_at=approval_event.expires_at,
        renewal_condition=approval_event.renewal_condition,
        idempotency_key=idempotency_key,
    )
