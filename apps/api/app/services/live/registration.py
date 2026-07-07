from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_trading_event import LiveTradingEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.live.contracts import (
    LiveAccountRegistrationRequest,
    LiveAccountRegistrationResult,
    LiveReadinessEligibilityResult,
)


def build_live_registration_idempotency_key(
    *,
    paper_account_id: uuid.UUID,
    registration_source: str,
    live_opt_in: bool,
) -> str:
    payload = json.dumps(
        {
            "paper_account_id": str(paper_account_id),
            "registration_source": registration_source,
            "live_opt_in": live_opt_in,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_registration_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    idempotency_key: str,
    readiness_state: str,
    operating_mode: str,
    recorded_at: datetime,
    payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "idempotency_key": idempotency_key,
            "readiness_state": readiness_state,
            "operating_mode": operating_mode,
            "recorded_at": recorded_at.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_live_registration_eligibility(
    *,
    paper_account_exists: bool,
    paper_account_active: bool,
    registration_source: str,
    live_opt_in: bool,
    autonomous_capital_allocation: bool,
    autonomous_strategy_evolution: bool,
    automatic_promotion_enabled: bool,
    risk_authority_model: str,
) -> LiveReadinessEligibilityResult:
    if not paper_account_exists:
        return LiveReadinessEligibilityResult(eligible=False, rejection_reason="paper_account_not_found")
    if not paper_account_active:
        return LiveReadinessEligibilityResult(eligible=False, rejection_reason="paper_account_inactive")
    if not registration_source.startswith("human_"):
        return LiveReadinessEligibilityResult(
            eligible=False,
            rejection_reason="human_registration_source_required",
        )
    if not live_opt_in:
        return LiveReadinessEligibilityResult(eligible=False, rejection_reason="live_opt_in_required")
    if autonomous_capital_allocation:
        return LiveReadinessEligibilityResult(
            eligible=False,
            rejection_reason="autonomous_capital_allocation_not_allowed",
        )
    if autonomous_strategy_evolution:
        return LiveReadinessEligibilityResult(
            eligible=False,
            rejection_reason="autonomous_strategy_evolution_not_allowed",
        )
    if automatic_promotion_enabled:
        return LiveReadinessEligibilityResult(
            eligible=False,
            rejection_reason="automatic_promotion_not_allowed",
        )
    if risk_authority_model != "risk_engine_final":
        return LiveReadinessEligibilityResult(
            eligible=False,
            rejection_reason="risk_engine_final_authority_required",
        )
    return LiveReadinessEligibilityResult(eligible=True, rejection_reason=None)


async def register_live_account(
    *,
    db: AsyncSession,
    request: LiveAccountRegistrationRequest,
) -> LiveAccountRegistrationResult:
    idempotency_key = request.idempotency_key or build_live_registration_idempotency_key(
        paper_account_id=request.paper_account_id,
        registration_source=request.registration_source,
        live_opt_in=request.live_opt_in,
    )

    existing_event = await db.scalar(
        select(LiveTradingEvent)
        .where(
            LiveTradingEvent.idempotency_key == idempotency_key,
            LiveTradingEvent.event_type == "registration_created",
        )
        .limit(1)
    )
    if existing_event is not None:
        profile = await db.scalar(
            select(LiveTradingProfile)
            .where(LiveTradingProfile.id == existing_event.live_trading_profile_id)
            .limit(1)
        )
        if profile is None:
            raise RuntimeError("live trading profile missing for existing registration idempotency key")

        accepted = profile.lifecycle_state in {"pending_approval", "approved", "enabled", "suspended"}
        return LiveAccountRegistrationResult(
            live_trading_profile_id=profile.id,
            readiness_state=profile.lifecycle_state,
            operating_mode=profile.operating_mode,
            accepted=accepted,
            rejection_reason=None,
            created_event_id=existing_event.id,
            idempotency_key=idempotency_key,
        )

    paper_account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.id == request.paper_account_id)
        .limit(1)
    )

    eligibility = validate_live_registration_eligibility(
        paper_account_exists=paper_account is not None,
        paper_account_active=bool(paper_account.is_active) if paper_account is not None else False,
        registration_source=request.registration_source,
        live_opt_in=request.live_opt_in,
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        risk_authority_model="risk_engine_final",
    )

    readiness_state = "pending_approval" if eligibility.eligible else "draft"
    approval_state = "pending" if eligibility.eligible else "not_requested"
    recorded_at = datetime.now(timezone.utc)

    profile = LiveTradingProfile(
        paper_account_id=request.paper_account_id,
        operating_mode="paper",
        lifecycle_state=readiness_state,
        approval_state=approval_state,
        live_opt_in=request.live_opt_in,
        human_approval_recorded=request.human_approval_recorded,
        paper_default_mode=True,
        governance_approved=request.governance_approved,
        risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={
            "registration_source": request.registration_source,
            "requested_by": request.requested_by,
            "requested_at": recorded_at.isoformat(),
            "eligibility": {
                "eligible": eligibility.eligible,
                "rejection_reason": eligibility.rejection_reason,
            },
            **request.provenance_metadata,
        },
    )

    async with db.begin():
        db.add(profile)
        await db.flush()

        existing_sequence = await db.scalar(
            select(func.max(LiveTradingEvent.sequence_number))
            .where(LiveTradingEvent.live_trading_profile_id == profile.id)
        )
        sequence_number = int(existing_sequence or 0) + 1

        event_payload = {
            "registration_source": request.registration_source,
            "requested_by": request.requested_by,
            "readiness_state": readiness_state,
            "approval_state": approval_state,
            "operating_mode": "paper",
            "paper_default_mode": True,
            "human_approval_recorded": request.human_approval_recorded,
            "governance_approved": request.governance_approved,
            "eligibility": {
                "eligible": eligibility.eligible,
                "rejection_reason": eligibility.rejection_reason,
            },
        }

        registration_event = LiveTradingEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_registration_event_hash(
                live_trading_profile_id=profile.id,
                idempotency_key=idempotency_key,
                readiness_state=readiness_state,
                operating_mode="paper",
                recorded_at=recorded_at,
                payload=event_payload,
            ),
            live_trading_profile_id=profile.id,
            sequence_number=sequence_number,
            event_type="registration_created",
            from_state=None,
            to_state=readiness_state,
            operating_mode="paper",
            paper_default_mode=True,
            live_opt_in=request.live_opt_in,
            governance_approved=request.governance_approved,
            risk_authority_model="risk_engine_final",
            event_payload=event_payload,
            provenance={
                "actor": request.requested_by,
                "source": request.registration_source,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(registration_event)
        await db.flush()

    return LiveAccountRegistrationResult(
        live_trading_profile_id=profile.id,
        readiness_state=profile.lifecycle_state,
        operating_mode=profile.operating_mode,
        accepted=eligibility.eligible,
        rejection_reason=eligibility.rejection_reason,
        created_event_id=registration_event.id,
        idempotency_key=idempotency_key,
    )
