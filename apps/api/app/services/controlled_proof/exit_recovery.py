from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, NoInspectionAvailable
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.orchestration.reconciliation_guard import has_unresolved_reconciliation
from app.services.position_lifecycle.source_adapter import _position_id, load_position_snapshots

logger = logging.getLogger(__name__)

_RECOVERABLE_PROOF_STATES = {"EXPIRED", "FAILED"}
_EXPOSURE_CONDITIONAL_RECOVERABLE_PROOF_STATES = {"BLOCKED"}
_ACTIVE_RECOVERY_STATES = {"AUTHORIZED", "IN_PROGRESS"}
_RECOVERED_OUTCOME_ACTION = "controlled_proof_exit_recovery.recovered_outcome_published"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _load_scope(db: AsyncSession, proof: ControlledProofRun):
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == proof.campaign_id).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        raise InvalidRequestError(message="Controlled Proof ownership scope is unavailable", details={"proof_id": str(proof.proof_id)})
    from app.services.controlled_proof.service import _resolve_live_trading_profile_id
    profile_id = await _resolve_live_trading_profile_id(db=db, paper_account_id=runtime.paper_account_id)
    if profile_id is None:
        raise InvalidRequestError(message="Controlled Proof ownership scope is unavailable", details={"proof_id": str(proof.proof_id)})
    return runtime, profile_id


async def _validate_exit_recovery(
    db: AsyncSession, proof: ControlledProofRun, *, allow_existing_sell_package: bool = False,
) -> None:
    blocked_with_exposure = proof.status in _EXPOSURE_CONDITIONAL_RECOVERABLE_PROOF_STATES
    if proof.status not in _RECOVERABLE_PROOF_STATES and not blocked_with_exposure:
        raise InvalidRequestError(message="Controlled Proof is not eligible for exit recovery", details={"status": proof.status})
    if proof.package_id is None:
        raise InvalidRequestError(message="Controlled Proof BUY package is missing", details={})

    # buy_live_crypto_order_id/sell_live_crypto_order_id are only an
    # opportunistic read-side projection (see repair_controlled_proof_
    # cached_order_ids's own docstring), written solely as a side effect of
    # get_controlled_proof_view being called. A proof reaching exit
    # recovery may never have had that view queried since its lineage was
    # established -- refresh from canonical lineage before trusting either
    # column below, exactly the repair get_controlled_proof_view already
    # performs on every read. Idempotent no-op when already current.
    from app.services.controlled_proof.service import repair_controlled_proof_cached_order_ids
    await repair_controlled_proof_cached_order_ids(db=db, proof=proof)

    if proof.sell_live_crypto_order_id is not None:
        raise InvalidRequestError(message="Controlled Proof already has SELL lineage", details={})
    if proof.sell_package_id is not None:
        if not allow_existing_sell_package:
            raise InvalidRequestError(message="Controlled Proof already has SELL lineage", details={})
        sell_package = await db.scalar(select(CanonicalPreviewPackage).where(
            CanonicalPreviewPackage.package_id == proof.sell_package_id,
        ))
        if sell_package is None or sell_package.side != "SELL":
            raise InvalidRequestError(message="Existing SELL package lineage is invalid", details={})
    if proof.buy_live_crypto_order_id is None:
        raise InvalidRequestError(message="Controlled Proof BUY order linkage is missing", details={})
    buy_order = await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == proof.buy_live_crypto_order_id)
    )
    if buy_order is None or buy_order.side.upper() != "BUY" or buy_order.status != "FILLED":
        raise InvalidRequestError(message="Controlled Proof BUY is not authoritatively filled", details={})
    latest_reconciliation = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.live_crypto_order_id == buy_order.live_crypto_order_id)
        .order_by(LiveReconciliationEvent.sequence_number.desc()).limit(1)
    )
    if latest_reconciliation is None or latest_reconciliation.reconciliation_status != "filled":
        raise InvalidRequestError(message="Controlled Proof BUY reconciliation is incomplete", details={})

    from app.services.orchestration.continuous_pipeline_worker import _has_open_live_order
    if await _has_open_live_order(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
        raise InvalidRequestError(message="An open provider order blocks exit recovery", details={})
    if await has_unresolved_reconciliation(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
        raise InvalidRequestError(message="Unresolved reconciliation blocks exit recovery", details={})

    runtime, profile_id = await _load_scope(db, proof)
    owned_quantity = await compute_signed_owned_quantity(db=db, live_trading_profile_id=profile_id, symbol=proof.product_id)
    if owned_quantity <= 0:
        raise InvalidRequestError(message="No positive authoritative owned quantity exists", details={})
    buy_accounting = await db.scalar(
        select(LiveAccountingRecord).where(
            LiveAccountingRecord.live_crypto_order_id == buy_order.live_crypto_order_id,
            LiveAccountingRecord.live_trading_profile_id == profile_id,
            LiveAccountingRecord.capital_campaign_id == runtime.id,
            LiveAccountingRecord.side == "buy",
        ).limit(1)
    )
    if buy_accounting is None:
        raise InvalidRequestError(message="Position cannot be attributed to this Controlled Proof BUY", details={})
    snapshots = await load_position_snapshots(db=db, account_id=runtime.paper_account_id, campaign_id=runtime.id)
    base = proof.product_id.split("-")[0].upper()
    position = next((item for item in snapshots if item.symbol.split("-")[0].upper() == base and item.position_size > 0), None)
    if position is None:
        raise InvalidRequestError(message="Open position linkage does not match this Controlled Proof", details={})
    # proof.position_id is only ever a display-side cache (see
    # get_controlled_proof_view's position_payload) -- nothing in this
    # codebase ever writes it, so treating it as required authority here
    # would fail closed on every single recovery, unconditionally, which is
    # exactly the confirmed production defect this fixes. position_id is
    # instead fully deterministic -- a pure function of
    # (live_trading_profile_id, capital_campaign_id, symbol), see
    # position_lifecycle.source_adapter._position_id -- so recompute the
    # expected value directly from this exact, already-scoped ownership
    # tuple (profile_id/runtime.id/proof.product_id all independently
    # verified above) and compare against what load_position_snapshots
    # actually returned, rather than trusting a column that may never have
    # been populated. Still fails closed on a genuine mismatch.
    expected_position_id = _position_id(
        live_trading_profile_id=profile_id, capital_campaign_id=runtime.id, symbol=proof.product_id,
    )
    if str(position.position_id) != expected_position_id:
        raise InvalidRequestError(message="Open position linkage does not match this Controlled Proof", details={})
    if blocked_with_exposure:
        # BLOCKED remains terminal and never regains ordinary BUY authority.
        # It receives narrowly scoped exit authority only when the quantity
        # attributable to this proof's canonical BUY/SELL lineage agrees
        # exactly with both the profile-wide custody ledger and this
        # campaign's position projection. Any ambiguity fails closed before
        # a recovery row or SELL package can exist.
        from app.services.controlled_proof.service import resolve_controlled_proof_owned_quantity
        proof_owned_quantity = await resolve_controlled_proof_owned_quantity(db=db, proof=proof)
        position_quantity = Decimal(str(position.position_size))
        if proof_owned_quantity <= 0:
            raise InvalidRequestError(message="No positive authoritative proof-owned quantity exists", details={})
        if proof_owned_quantity != owned_quantity or proof_owned_quantity != position_quantity:
            raise InvalidRequestError(
                message="Controlled Proof owned quantity disagrees with authoritative custody",
                details={},
            )
    if proof.position_id != expected_position_id:
        before_position_id = proof.position_id
        proof.position_id = expected_position_id
        proof.updated_at = _utcnow()
        db.add(AuditLog(
            actor="system:controlled_proof_exit_recovery",
            action="controlled_proof_run.position_lineage_repaired",
            entity_type="controlled_proof_run", entity_id=proof.proof_id,
            before_state={"position_id": before_position_id},
            after_state={"position_id": expected_position_id},
        ))
        await db.flush()


async def authorize_controlled_proof_exit_recovery(
    *, db: AsyncSession, proof_id: uuid.UUID, idempotency_key: str,
    expires_in_minutes: int, actor: str,
) -> ControlledProofExitRecovery:
    key = idempotency_key.strip()
    if not key:
        raise InvalidRequestError(message="idempotency_key is required", details={})
    replay = await db.scalar(select(ControlledProofExitRecovery).where(ControlledProofExitRecovery.idempotency_key == key))
    if replay is not None:
        if replay.proof_id != proof_id:
            raise InvalidRequestError(message="Exit recovery idempotency key belongs to another proof", details={})
        return replay
    proof = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.proof_id == proof_id).with_for_update())
    if proof is None:
        raise NotFoundError(message="Controlled Proof not found", details={"proof_id": str(proof_id)})
    existing_active = await db.scalar(select(ControlledProofExitRecovery).where(
        ControlledProofExitRecovery.proof_id == proof_id,
        ControlledProofExitRecovery.status.in_(_ACTIVE_RECOVERY_STATES),
    ).limit(1))
    if existing_active is not None:
        raise InvalidRequestError(message="Controlled Proof already has an active exit recovery authority", details={"recovery_id": str(existing_active.recovery_id)})
    await _validate_exit_recovery(db, proof, allow_existing_sell_package=proof.sell_package_id is not None)
    now = _utcnow()
    recovery = ControlledProofExitRecovery(
        proof_id=proof_id, status="AUTHORIZED", idempotency_key=key, authorized_by=actor,
        authorized_at=now, expires_at=now + timedelta(minutes=expires_in_minutes), updated_at=now,
    )
    db.add(recovery)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        replay = await db.scalar(select(ControlledProofExitRecovery).where(ControlledProofExitRecovery.idempotency_key == key))
        if replay is not None and replay.proof_id == proof_id:
            return replay
        raise InvalidRequestError(message="Another exit recovery is already active", details={}) from exc
    db.add(AuditLog(
        actor=actor, action="controlled_proof_exit_recovery.authorized",
        entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id,
        before_state={"proof_status": proof.status, "terminal_verdict": proof.terminal_verdict},
        after_state={"status": "AUTHORIZED", "proof_id": str(proof_id), "exit_only": True, "expires_at": recovery.expires_at.isoformat()},
    ))
    await db.commit()
    return recovery


async def find_pending_exit_recovery_id(*, db: AsyncSession) -> uuid.UUID | None:
    now = _utcnow()
    expired = (await db.scalars(select(ControlledProofExitRecovery).where(
        ControlledProofExitRecovery.status.in_(_ACTIVE_RECOVERY_STATES), ControlledProofExitRecovery.expires_at <= now,
    ))).all()
    for item in expired:
        before = item.status
        item.status = "EXPIRED"; item.completed_at = now; item.updated_at = now
        db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.expired", entity_type="controlled_proof_exit_recovery", entity_id=item.recovery_id, before_state={"status": before}, after_state={"status": "EXPIRED"}))
    if expired:
        await db.flush()
    return await db.scalar(select(ControlledProofExitRecovery.recovery_id).where(
        ControlledProofExitRecovery.status.in_(_ACTIVE_RECOVERY_STATES), ControlledProofExitRecovery.expires_at > now,
    ).order_by(ControlledProofExitRecovery.authorized_at.asc()).limit(1))


async def has_active_exit_recovery(*, db: AsyncSession, proof_id: uuid.UUID) -> bool:
    """True when a Controlled Proof exit recovery for this proof is
    currently AUTHORIZED or IN_PROGRESS -- the exact condition other
    modules (e.g. Controlled Proof's stale-proof recovery safety check) need
    to know before treating a proof as safe to automatically terminalize,
    without depending on this module's private _ACTIVE_RECOVERY_STATES set."""
    recovery_id = await db.scalar(select(ControlledProofExitRecovery.recovery_id).where(
        ControlledProofExitRecovery.proof_id == proof_id,
        ControlledProofExitRecovery.status.in_(_ACTIVE_RECOVERY_STATES),
    ).limit(1))
    return recovery_id is not None


async def claim_exit_recovery_by_id(*, db: AsyncSession, recovery_id: uuid.UUID):
    now = _utcnow()
    recovery = await db.scalar(select(ControlledProofExitRecovery).where(ControlledProofExitRecovery.recovery_id == recovery_id).with_for_update())
    if recovery is None or recovery.status not in _ACTIVE_RECOVERY_STATES or recovery.expires_at <= now:
        return None
    proof = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.proof_id == recovery.proof_id).with_for_update())
    if proof is None:
        return None
    if proof.sell_package_id is None:
        try:
            await _validate_exit_recovery(db, proof)
        except InvalidRequestError as exc:
            before = recovery.status
            recovery.status = "BLOCKED"
            recovery.blocked_reason = f"authority_revalidation_failed:{exc.message}"
            recovery.failure_reason = None
            recovery.completed_at = now
            recovery.updated_at = now
            db.add(AuditLog(
                actor="system:controlled_proof_worker",
                action="controlled_proof_exit_recovery.blocked",
                entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id,
                before_state={"status": before},
                after_state={"status": "BLOCKED", "reason": recovery.blocked_reason},
            ))
            # This branch returns no claimed work to the caller, so there is no
            # later worker commit on which the terminal transition can rely.
            await db.commit()
            return None
    if recovery.status == "AUTHORIZED":
        recovery.status = "IN_PROGRESS"; recovery.claimed_at = now; recovery.updated_at = now
        db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.claimed", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": "AUTHORIZED"}, after_state={"status": "IN_PROGRESS"}))
        await db.flush()
    return recovery, proof


async def supersede_stale_exit_recovery_sell_package(
    *, db: AsyncSession, recovery: ControlledProofExitRecovery,
    proof: ControlledProofRun, package: CanonicalPreviewPackage,
) -> None:
    """Retire an unused stale SELL package so fresh governance can run.

    An execution claim is the boundary. Replacement is permitted only for a
    proven pre-provider failure or an authoritative explicit provider
    rejection; ambiguous or potentially filled lineage remains fail-closed.
    It never renews or mutates the expired authority evidence on the
    historical package.
    """
    now = _utcnow()
    if (
        recovery.proof_id != proof.proof_id
        or recovery.status != "IN_PROGRESS"
        or recovery.expires_at <= now
        or proof.sell_package_id != package.package_id
        or package.side != "SELL"
        or package.package_state != "ACTIVATED"
        or package.authorization_source != "MANDATE"
        or package.authorization_expires_at is None
        or package.authorization_expires_at > now
    ):
        raise InvalidRequestError(message="Stale SELL package is not eligible for governed replacement", details={})
    existing_claim = await db.scalar(select(AutonomousExecutionClaim).where(
        AutonomousExecutionClaim.package_id == package.package_id,
    ).with_for_update().limit(1))
    terminal_rejection_audit: dict[str, object] | None = None
    if existing_claim is not None:
        if existing_claim.claim_status not in {"FAILED_PRE_PROVIDER", "CANCELLED"}:
            raise InvalidRequestError(message="Stale SELL package has unresolved execution lineage", details={})
        if existing_claim.claim_status == "CANCELLED" and existing_claim.live_order_id is None:
            raise InvalidRequestError(message="Stale SELL package provider boundary is unresolved", details={})
        if existing_claim.live_order_id is not None:
            failed_order = await db.scalar(select(LiveCryptoOrder).where(
                LiveCryptoOrder.live_crypto_order_id == existing_claim.live_order_id,
            ).with_for_update().limit(1))
            evidence = (
                failed_order.safe_provider_response
                if failed_order is not None and isinstance(failed_order.safe_provider_response, dict)
                else {}
            )
            rejected_error = evidence.get("create_order_error") if isinstance(evidence.get("create_order_error"), dict) else {}
            is_proven_pre_provider = bool(
                existing_claim.claim_status == "FAILED_PRE_PROVIDER"
                and failed_order is not None
                and failed_order.status == "CANCELLED"
                and failed_order.provider_order_id is None
                and failed_order.submitted_at is None
                and evidence.get("provider_call_made") is False
            )
            is_authoritative_rejection = bool(
                existing_claim.claim_status == "CANCELLED"
                and failed_order is not None
                and failed_order.status == "REJECTED"
                and failed_order.provider_order_id is None
                and failed_order.submitted_at is not None
                and evidence.get("create_order_responded") is True
                and rejected_error.get("rejection_reason") == "provider_explicit_rejection"
                and str(rejected_error.get("code") or "").strip()
                and str(rejected_error.get("message") or "").strip()
            )
            if not (is_proven_pre_provider or is_authoritative_rejection):
                raise InvalidRequestError(message="Stale SELL package provider boundary is unresolved", details={})
            if is_authoritative_rejection:
                terminal_rejection_audit = {
                    "execution_claim_id": str(existing_claim.claim_id),
                    "live_crypto_order_id": str(failed_order.live_crypto_order_id),
                    "provider_error_code": str(rejected_error["code"]),
                    "rejection_message": str(rejected_error["message"]),
                    "provider_order_id": None,
                    "provider_outcome": "REJECTED",
                }
    activation = await db.scalar(select(CanonicalProvingActivation).where(
        CanonicalProvingActivation.package_id == package.package_id,
    ).with_for_update().limit(1))
    if activation is None or activation.expires_at > now:
        raise InvalidRequestError(message="Stale SELL package activation is not authoritatively expired", details={})

    old_package_id = package.package_id
    package.package_state = "SUPERSEDED"
    package.superseded_at = now
    package.invalidated_reason = "controlled_proof_exit_recovery_fresh_authority_required"
    before_activation_state = activation.activation_state
    if activation.activation_state == "ACTIVE":
        activation.activation_state = "EXPIRED"
        activation.updated_at = now
    proof.sell_package_id = None
    proof.updated_at = now
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="canonical_preview_package.superseded_for_exit_recovery",
        entity_type="canonical_preview_package", entity_id=old_package_id,
        before_state={"package_state": "ACTIVATED"},
        after_state={
            "package_state": "SUPERSEDED",
            "reason": package.invalidated_reason,
            "controlled_proof_id": str(proof.proof_id),
            "exit_recovery_id": str(recovery.recovery_id),
            "terminal_rejection_lineage": terminal_rejection_audit,
        },
    ))
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="canonical_proving_activation.expired_for_exit_recovery_reissue",
        entity_type="canonical_proving_activation", entity_id=activation.activation_id,
        before_state={"activation_state": before_activation_state},
        after_state={
            "activation_state": activation.activation_state,
            "package_id": str(old_package_id),
            "exit_recovery_id": str(recovery.recovery_id),
        },
    ))
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="controlled_proof_exit_recovery.stale_sell_package_superseded",
        entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id,
        before_state={
            "sell_package_id": str(old_package_id),
            "package_state": "ACTIVATED",
            "authorization_expires_at": package.authorization_expires_at.isoformat(),
        },
        after_state={
            "sell_package_id": None,
            "package_state": "SUPERSEDED",
            "activation_state": activation.activation_state,
            "replacement_requires_fresh_risk_mandate_preview_package": True,
            "controlled_proof_id": str(proof.proof_id),
            "exit_recovery_id": str(recovery.recovery_id),
            "superseded_package_id": str(old_package_id),
            "terminal_rejection_lineage": terminal_rejection_audit,
        },
    ))
    await db.flush()


async def supersede_expired_preview_exit_recovery_sell_package(
    *, db: AsyncSession, recovery: ControlledProofExitRecovery,
    proof: ControlledProofRun, package: CanonicalPreviewPackage,
) -> None:
    """Retire a SELL package whose canonical preview window expired before
    it ever reached ACTIVATED, so fresh governance can run.

    Disjoint from supersede_stale_exit_recovery_sell_package, which handles
    an already-ACTIVATED package whose post-activation authorization window
    later elapsed. A package that never reached ACTIVATED can never have an
    execution claim (claim_activated_package requires package_state ==
    "ACTIVATED") or a CanonicalProvingActivation row (only created by
    activate_canonical_proving_campaign) -- so neither is checked here.
    """
    now = _utcnow()
    if (
        recovery.proof_id != proof.proof_id
        or recovery.status != "IN_PROGRESS"
        or recovery.expires_at <= now
        or proof.sell_package_id != package.package_id
        or package.side != "SELL"
        or package.package_state not in {"READY", "AUTHORIZED", "DRY_RUN_PASSED"}
        or package.preview_expires_at > now
    ):
        raise InvalidRequestError(message="Stale SELL package preview is not eligible for governed replacement", details={})

    old_package_id = package.package_id
    before_package_state = package.package_state
    package.package_state = "SUPERSEDED"
    package.superseded_at = now
    package.invalidated_reason = "controlled_proof_exit_recovery_fresh_authority_required"
    proof.sell_package_id = None
    proof.updated_at = now
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="canonical_preview_package.superseded_for_exit_recovery",
        entity_type="canonical_preview_package", entity_id=old_package_id,
        before_state={"package_state": before_package_state},
        after_state={
            "package_state": "SUPERSEDED",
            "reason": package.invalidated_reason,
            "controlled_proof_id": str(proof.proof_id),
            "exit_recovery_id": str(recovery.recovery_id),
        },
    ))
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="controlled_proof_exit_recovery.stale_sell_package_superseded",
        entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id,
        before_state={
            "sell_package_id": str(old_package_id),
            "package_state": before_package_state,
            "preview_expires_at": package.preview_expires_at.isoformat(),
        },
        after_state={
            "sell_package_id": None,
            "package_state": "SUPERSEDED",
            "replacement_requires_fresh_risk_mandate_preview_package": True,
            "controlled_proof_id": str(proof.proof_id),
            "exit_recovery_id": str(recovery.recovery_id),
            "superseded_package_id": str(old_package_id),
        },
    ))
    await db.flush()


async def get_exit_recovery_view(*, db: AsyncSession, proof_id: uuid.UUID) -> dict:
    await find_pending_exit_recovery_id(db=db)
    recovery = await db.scalar(select(ControlledProofExitRecovery).where(ControlledProofExitRecovery.proof_id == proof_id).order_by(ControlledProofExitRecovery.authorized_at.desc()).limit(1))
    if recovery is None:
        raise NotFoundError(message="Controlled Proof exit recovery not found", details={"proof_id": str(proof_id)})
    outcome_audit = await db.scalar(select(AuditLog).where(
        AuditLog.entity_type == "controlled_proof_exit_recovery",
        AuditLog.entity_id == recovery.recovery_id,
        AuditLog.action == _RECOVERED_OUTCOME_ACTION,
    ).order_by(AuditLog.id.desc()).limit(1))
    return {
        "recovery_id": recovery.recovery_id, "proof_id": recovery.proof_id, "status": recovery.status,
        "idempotency_key": recovery.idempotency_key, "authorized_by": recovery.authorized_by,
        "authorized_at": recovery.authorized_at, "expires_at": recovery.expires_at,
        "claimed_at": recovery.claimed_at, "completed_at": recovery.completed_at,
        "blocked_reason": recovery.blocked_reason, "failure_reason": recovery.failure_reason,
        "audit_correlation_id": recovery.audit_correlation_id,
        "recovered_outcome": None if outcome_audit is None else outcome_audit.after_state,
    }


async def record_exit_recovery_waiting(*, db: AsyncSession, recovery: ControlledProofExitRecovery, reason: str) -> None:
    recovery.failure_reason = f"retryable:{reason}"; recovery.updated_at = _utcnow()
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.waiting", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": recovery.status}, after_state={"status": recovery.status, "failure_reason": recovery.failure_reason}))
    await db.flush()


async def block_exit_recovery(*, db: AsyncSession, recovery: ControlledProofExitRecovery, reason: str) -> None:
    before = recovery.status
    completed_at = _utcnow()
    recovery.status = "BLOCKED"; recovery.blocked_reason = reason
    recovery.failure_reason = None; recovery.completed_at = completed_at; recovery.updated_at = completed_at
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.blocked", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": before}, after_state={"status": "BLOCKED", "reason": reason}))
    await db.flush()


async def refresh_exit_recovery_completion(
    *, db: AsyncSession, recovery: ControlledProofExitRecovery, proof: ControlledProofRun,
) -> None:
    if recovery.status == "COMPLETED":
        return
    proof_view = None
    if proof.sell_live_crypto_order_id is None:
        from app.services.controlled_proof.service import get_controlled_proof_view
        proof_view = await get_controlled_proof_view(db=db, proof_id=proof.proof_id)
        await db.refresh(proof)
    if proof.sell_live_crypto_order_id is None:
        return
    sell_order = await db.scalar(select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == proof.sell_live_crypto_order_id))
    if sell_order is None or sell_order.status != "FILLED":
        return
    execution_claim = await db.scalar(select(AutonomousExecutionClaim).where(
        AutonomousExecutionClaim.live_order_id == sell_order.live_crypto_order_id,
    ).limit(1))
    if execution_claim is None or execution_claim.claim_status != "COMPLETED":
        return
    if await has_unresolved_reconciliation(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
        return
    _runtime, profile_id = await _load_scope(db, proof)
    if await compute_signed_owned_quantity(db=db, live_trading_profile_id=profile_id, symbol=proof.product_id) != 0:
        return
    if proof_view is None:
        from app.services.controlled_proof.service import get_controlled_proof_view
        proof_view = await get_controlled_proof_view(db=db, proof_id=proof.proof_id)
        await db.refresh(proof)
    net_pnl_raw = proof_view.get("net_pnl_usd") if isinstance(proof_view, dict) else None
    if net_pnl_raw is None:
        return
    net_pnl = Decimal(str(net_pnl_raw))
    before_proof = {
        "status": proof.status,
        "terminal_verdict": proof.terminal_verdict,
        "net_pnl_usd": None if proof.net_pnl_usd is None else str(proof.net_pnl_usd),
    }
    recovered_verdict = (
        "LIFECYCLE_PROVEN_PROFIT" if net_pnl > 0
        else "LIFECYCLE_PROVEN_LOSS" if net_pnl < 0
        else "LIFECYCLE_PROVEN_FLAT"
    )
    db.add(AuditLog(
        actor="system:controlled_proof_worker",
        action="controlled_proof_run.exit_recovery_accounting_completed",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state=before_proof,
        after_state={
            "status": proof.status,
            "terminal_verdict": proof.terminal_verdict,
            "recovered_terminal_verdict": recovered_verdict,
            "recovered_net_pnl_usd": str(net_pnl),
            "sell_live_crypto_order_id": str(sell_order.live_crypto_order_id),
            "execution_claim_id": str(execution_claim.claim_id),
            "exit_recovery_id": str(recovery.recovery_id),
        },
    ))
    before = recovery.status
    recovery.status = "COMPLETED"; recovery.completed_at = _utcnow(); recovery.updated_at = recovery.completed_at
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.completed", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": before}, after_state={"status": "COMPLETED", "sell_live_crypto_order_id": str(sell_order.live_crypto_order_id), "execution_claim_id": str(execution_claim.claim_id), "controlled_proof_status": proof.status, "controlled_proof_terminal_verdict": proof.terminal_verdict, "recovered_terminal_verdict": recovered_verdict, "recovered_net_pnl_usd": str(net_pnl)}))
    await db.flush()


def _recovered_verdict(net_pnl: Decimal) -> str:
    if net_pnl > 0:
        return "LIFECYCLE_PROVEN_PROFIT"
    if net_pnl < 0:
        return "LIFECYCLE_PROVEN_LOSS"
    return "LIFECYCLE_PROVEN_FLAT"


def _pnl_matches(left: Decimal, right: Decimal, *, sqlite: bool = False) -> bool:
    return abs(left - right) < Decimal("0.0000000001") if sqlite else left == right


async def _flush_and_verify_proof_projection(
    *, db: AsyncSession, proof: ControlledProofRun, net_pnl: Decimal, verdict: str,
) -> bool:
    """Flush the proof UPDATE and verify its transaction-local row state."""
    if not isinstance(db, AsyncSession):
        await db.flush()
        return True
    await db.flush([proof])
    persisted = (await db.execute(select(
        ControlledProofRun.net_pnl_usd,
        ControlledProofRun.terminal_verdict,
    ).where(ControlledProofRun.proof_id == proof.proof_id))).one_or_none()
    if persisted is None or persisted.terminal_verdict != verdict or persisted.net_pnl_usd is None:
        return False
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        return _pnl_matches(persisted.net_pnl_usd, net_pnl, sqlite=True)
    return _pnl_matches(persisted.net_pnl_usd, net_pnl)


async def project_blocked_exit_recovery_outcome(
    *, db: AsyncSession, recovery: ControlledProofExitRecovery, proof: ControlledProofRun,
    diagnostics: dict[str, object] | None = None,
) -> bool:
    """Publish an accounting outcome without reopening a terminal recovery.

    The canonical package's immutable recovery marker is the root of the
    lineage.  Nothing is inferred from timestamps or merely sharing a proof.
    Locking the recovery serializes projector replays; the append-only audit
    row is the projection and the historical BLOCKED row is never mutated.
    """
    def record(reason: str, *, matched: bool = False, mutated: bool = False, verified: bool = False) -> None:
        if diagnostics is not None:
            diagnostics.update(
                reason=reason, proof_fields_matched_recovered_outcome=matched,
                orm_mutation_occurred=mutated, flush_readback_verified=verified,
            )

    if recovery.status != "BLOCKED":
        record("recovery_not_blocked")
        return False
    try:
        proof_state = inspect(proof)
    except NoInspectionAvailable:
        proof_state = None
    if proof_state is not None and (
        not proof_state.persistent
        or proof_state.detached
        or proof_state.session is not db.sync_session
    ):
        record("proof_not_persistent_in_worker_session")
        return False
    locked = await db.scalar(select(ControlledProofExitRecovery).where(
        ControlledProofExitRecovery.recovery_id == recovery.recovery_id,
    ).with_for_update())
    if locked is None or locked.status != "BLOCKED" or locked.proof_id != proof.proof_id:
        record("locked_recovery_scope_mismatch")
        return False
    existing = await db.scalar(select(AuditLog).where(
        AuditLog.entity_type == "controlled_proof_exit_recovery",
        AuditLog.entity_id == locked.recovery_id,
        AuditLog.action == _RECOVERED_OUTCOME_ACTION,
    ).limit(1))
    if existing is not None:
        # The audit row is never mutated or duplicated here -- only the
        # proof's own net_pnl_usd/terminal_verdict may be backfilled from
        # it, and only when every field proves this exact, already-
        # published outcome belongs to this exact proof/recovery/package/
        # order lineage and is a genuinely proven, reconciled result.
        # Fails closed (no mutation) on any missing/malformed/mismatched
        # field, or when the proof already carries a real verdict.
        #
        # proof.sell_live_crypto_order_id is only an opportunistic,
        # denormalized read-side cache (see repair_controlled_proof_
        # cached_order_ids's own docstring) -- for a proof reached purely
        # through this historical sweep (never re-queried via
        # get_controlled_proof_view since the BLOCKED transition), it can
        # still be None/stale even though canonical lineage is complete.
        # Confirmed root cause of a real production non-projection: the
        # match below silently and permanently skipped backfill on every
        # sweep pass, with no error, because this exact column was never
        # refreshed. Repair it from canonical lineage before trusting it.
        from app.services.controlled_proof.service import repair_controlled_proof_cached_order_ids
        await repair_controlled_proof_cached_order_ids(db=db, proof=proof)
        payload = existing.after_state
        valid_verdicts = {
            "LIFECYCLE_PROVEN_PROFIT", "LIFECYCLE_PROVEN_LOSS", "LIFECYCLE_PROVEN_FLAT",
        }
        checks = (
            (isinstance(payload, dict), "recovered_outcome_payload_not_object"),
            (isinstance(payload, dict) and payload.get("status") == "COMPLETED_RECONCILED", "recovered_outcome_status_mismatch"),
            (isinstance(payload, dict) and payload.get("proof_id") == str(proof.proof_id), "recovered_outcome_proof_id_mismatch"),
            (isinstance(payload, dict) and payload.get("original_recovery_id") == str(locked.recovery_id), "recovered_outcome_recovery_id_mismatch"),
            (proof.sell_package_id is not None, "proof_sell_package_missing"),
            (isinstance(payload, dict) and proof.sell_package_id is not None and payload.get("sell_package_id") == str(proof.sell_package_id), "recovered_outcome_sell_package_mismatch"),
            (proof.sell_live_crypto_order_id is not None, "proof_sell_order_missing_after_lineage_repair"),
            (isinstance(payload, dict) and proof.sell_live_crypto_order_id is not None and payload.get("sell_live_crypto_order_id") == str(proof.sell_live_crypto_order_id), "recovered_outcome_sell_order_mismatch"),
            (isinstance(payload, dict) and payload.get("recovered_terminal_verdict") in valid_verdicts, "recovered_outcome_verdict_invalid"),
        )
        failed_check = next((reason for passed, reason in checks if not passed), None)
        if failed_check is not None:
            record(failed_check)
            return False
        if proof.terminal_verdict in valid_verdicts:
            try:
                already_matched = (
                    proof.net_pnl_usd is not None
                    and _pnl_matches(
                        proof.net_pnl_usd, Decimal(str(payload.get("recovered_net_pnl_usd"))),
                        sqlite=isinstance(db, AsyncSession) and db.bind is not None and db.bind.dialect.name == "sqlite",
                    )
                    and proof.terminal_verdict == payload["recovered_terminal_verdict"]
                )
            except (TypeError, ArithmeticError, ValueError):
                already_matched = False
            record("proof_already_terminal", matched=already_matched)
            return False
        if isinstance(payload, dict):
            try:
                recovered_net_pnl = Decimal(str(payload.get("recovered_net_pnl_usd")))
            except (TypeError, ArithmeticError, ValueError):
                record("recovered_outcome_pnl_invalid")
                return False
            proof.net_pnl_usd = recovered_net_pnl
            proof.terminal_verdict = payload["recovered_terminal_verdict"]
            proof.updated_at = _utcnow()
            verified = await _flush_and_verify_proof_projection(
                db=db, proof=proof, net_pnl=recovered_net_pnl,
                verdict=payload["recovered_terminal_verdict"],
            )
            record(
                "projection_verified" if verified else "projection_readback_mismatch",
                matched=verified, mutated=True, verified=verified,
            )
            return verified
        record("recovered_outcome_payload_not_object")
        return False

    packages = (await db.scalars(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.side == "SELL",
        CanonicalPreviewPackage.campaign_id == proof.campaign_id,
        CanonicalPreviewPackage.campaign_version == proof.campaign_version,
        CanonicalPreviewPackage.provider == proof.provider,
        CanonicalPreviewPackage.environment == proof.environment,
        CanonicalPreviewPackage.product == proof.product_id,
    ))).all()
    matches = [
        package for package in packages
        if str((package.market_evidence_identity or {}).get("controlled_proof_id")) == str(proof.proof_id)
    ]
    # Package history is immutable and may contain several abandoned or
    # superseded recovery attempts.  Authority begins only once a package's
    # SELL claim reached a provider-identified order.  Package-only,
    # unclaimed, and unsubmitted attempts are history, not competing exits.
    # Conversely, two provider-submitted SELL lineages are genuinely
    # ambiguous even if only one later reports FILLED, and must fail closed.
    executed_lineages: list[tuple[CanonicalPreviewPackage, AutonomousExecutionClaim, LiveCryptoOrder]] = []
    for candidate in matches:
        candidate_claim = await db.scalar(select(AutonomousExecutionClaim).where(
            AutonomousExecutionClaim.package_id == candidate.package_id,
            AutonomousExecutionClaim.side == "SELL",
        ).limit(1))
        if candidate_claim is None or candidate_claim.live_order_id is None:
            continue
        candidate_order = await db.scalar(select(LiveCryptoOrder).where(
            LiveCryptoOrder.live_crypto_order_id == candidate_claim.live_order_id,
        ).limit(1))
        if (
            candidate_order is None or not candidate_order.provider_order_id
            or str(candidate_order.side or "").upper() != "SELL"
        ):
            continue
        executed_lineages.append((candidate, candidate_claim, candidate_order))
    if not executed_lineages:
        record("canonical_authoritative_sell_lineage_missing")
        return False
    if len(executed_lineages) != 1:
        record("canonical_authoritative_sell_lineage_ambiguous")
        return False
    package, claim, order = executed_lineages[0]
    if (
        claim is None or claim.claim_status != "COMPLETED" or claim.live_order_id is None
        or claim.campaign_id != package.campaign_id or claim.campaign_version != package.campaign_version
        or claim.profile_id != package.live_trading_profile_id
        or claim.provider != package.provider or claim.environment != package.environment
        or claim.product != package.product
    ):
        record("canonical_sell_claim_invalid")
        return False
    if (
        order is None or order.status != "FILLED" or not order.provider_order_id
        or order.side.upper() != "SELL" or order.provider != proof.provider
        or order.environment != proof.environment or order.product_id != proof.product_id
    ):
        record("canonical_sell_order_invalid")
        return False
    # These are denormalized canonical caches. Once exactly one provider-
    # executed lineage survives the ambiguity check, repair stale pointers
    # rather than letting an abandoned historical package conceal it.
    proof.sell_package_id = package.package_id
    proof.sell_live_crypto_order_id = order.live_crypto_order_id
    reconciliation = await db.scalar(select(LiveReconciliationEvent).where(
        LiveReconciliationEvent.live_crypto_order_id == order.live_crypto_order_id,
    ).order_by(LiveReconciliationEvent.sequence_number.desc()).limit(1))
    if reconciliation is None or reconciliation.reconciliation_status != "filled":
        record("latest_sell_reconciliation_not_filled")
        return False
    if await has_unresolved_reconciliation(
        db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id,
    ):
        record("unresolved_reconciliation")
        return False
    runtime, profile_id = await _load_scope(db, proof)
    if profile_id != package.live_trading_profile_id:
        record("runtime_profile_package_mismatch")
        return False
    if (
        reconciliation.live_crypto_order_id != order.live_crypto_order_id
        or reconciliation.live_trading_profile_id != profile_id
        or reconciliation.capital_campaign_id != runtime.id
        or reconciliation.provider_name != order.provider
        or reconciliation.provider_order_id != order.provider_order_id
    ):
        record("sell_reconciliation_scope_mismatch")
        return False
    if await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=profile_id, symbol=proof.product_id,
    ) != 0:
        record("position_not_closed")
        return False
    if proof.buy_live_crypto_order_id is None:
        record("proof_buy_order_missing")
        return False
    accounting = (await db.scalars(select(LiveAccountingRecord).where(
        LiveAccountingRecord.live_trading_profile_id == profile_id,
        LiveAccountingRecord.capital_campaign_id == runtime.id,
        LiveAccountingRecord.live_crypto_order_id.in_((
            proof.buy_live_crypto_order_id, order.live_crypto_order_id,
        )),
    ))).all()
    base = proof.product_id.split("-")[0].upper()
    accounting = [row for row in accounting if row.symbol.split("-")[0].upper() == base]
    if not any(row.live_crypto_order_id == proof.buy_live_crypto_order_id and row.side == "buy" for row in accounting):
        record("buy_accounting_missing")
        return False
    sell_accounting = [
        row for row in accounting
        if row.live_crypto_order_id == order.live_crypto_order_id and row.side == "sell"
    ]
    final_sell_accounting = [row for row in sell_accounting if row.record_type == "fill_accounting"]
    if not sell_accounting or not final_sell_accounting:
        record("sell_accounting_incomplete")
        return False
    accounting_reconciliation = await db.scalar(select(LiveReconciliationEvent).where(
        LiveReconciliationEvent.id.in_(tuple(
            row.reconciliation_event_id for row in final_sell_accounting
        )),
        LiveReconciliationEvent.live_crypto_order_id == order.live_crypto_order_id,
    ).order_by(LiveReconciliationEvent.sequence_number.desc()).limit(1))
    if (
        accounting_reconciliation is None
        or accounting_reconciliation.event_type != "fill_reconciled"
        or accounting_reconciliation.reconciliation_status != "filled"
        or accounting_reconciliation.live_crypto_order_id != order.live_crypto_order_id
        or accounting_reconciliation.live_trading_profile_id != profile_id
        or accounting_reconciliation.capital_campaign_id != runtime.id
        or accounting_reconciliation.provider_name != order.provider
        or accounting_reconciliation.provider_order_id != order.provider_order_id
    ):
        record("accounting_reconciliation_scope_mismatch")
        return False
    net_pnl = sum((row.net_cash_impact for row in accounting), Decimal("0"))
    completed_at = _utcnow()
    # The proof itself must finalize truthfully here. The recovery's earlier
    # BLOCKED classification is retained in immutable audit history below,
    # while its current row transitions to COMPLETED. get_controlled_proof_view's terminal_verdict is frozen the
    # first time it is computed (by design -- a real PROFIT/LOSS/FLAT
    # verdict must never later flip) and, for a proof that reached this
    # exact governed-replacement-SELL-filled state only after already
    # expiring, that freeze can land on a stale "FAILED"/"BLOCKED" label
    # written before this proven, reconciled outcome ever existed. Every
    # invariant above (matching package/claim/order/reconciliation/
    # accounting evidence, resolved reconciliation, zero owned quantity)
    # is already exhaustively verified by this point, and this whole
    # branch runs at most once (guarded by the existing-audit-row check
    # above) -- so overwrite the proof's own net_pnl_usd/terminal_verdict
    # with the real recovered outcome now. proof.status is left untouched.
    proof.net_pnl_usd = net_pnl
    proof.terminal_verdict = _recovered_verdict(net_pnl)
    proof.updated_at = completed_at
    recovery_before_state = {
        "status": locked.status,
        "blocked_reason": locked.blocked_reason,
        "failure_reason": getattr(locked, "failure_reason", None),
        "completed_at": None if locked.completed_at is None else locked.completed_at.isoformat(),
    }
    # BLOCKED described the earlier package-replacement attempt, not the
    # now-proven financial outcome. Preserve that history in the immutable
    # audit below, while making the recovery row's current terminal truth
    # unambiguously successful.
    locked.status = "COMPLETED"
    locked.completed_at = completed_at
    locked.blocked_reason = None
    locked.failure_reason = None
    locked.updated_at = completed_at
    payload = {
        "status": "COMPLETED_RECONCILED",
        "original_recovery_id": str(locked.recovery_id),
        "proof_id": str(proof.proof_id),
        "sell_package_id": str(package.package_id),
        "sell_live_crypto_order_id": str(order.live_crypto_order_id),
        "provider_order_id": order.provider_order_id,
        "execution_claim_id": str(claim.claim_id),
        "reconciliation_event_id": str(accounting_reconciliation.id),
        "recovered_terminal_verdict": _recovered_verdict(net_pnl),
        "recovered_net_pnl_usd": str(net_pnl),
        "completed_at": completed_at.isoformat(),
        "audit_correlation_id": str(locked.audit_correlation_id),
    }
    db.add(AuditLog(
        actor="system:controlled_proof_reconciliation_projector",
        action=_RECOVERED_OUTCOME_ACTION,
        entity_type="controlled_proof_exit_recovery", entity_id=locked.recovery_id,
        before_state=recovery_before_state,
        after_state=payload,
    ))
    await db.flush()
    verified = await _flush_and_verify_proof_projection(
        db=db, proof=proof, net_pnl=net_pnl, verdict=_recovered_verdict(net_pnl),
    )
    record(
        "projection_verified" if verified else "projection_readback_mismatch",
        matched=verified, mutated=True, verified=verified,
    )
    return verified


@dataclass(frozen=True, slots=True)
class ExitRecoveryOutcomeSweepResult:
    """Counts describe what this call *flushed* in-session, not what is
    durably persisted -- this function never commits (see module-level
    commit-ownership note on refresh_exit_recovery_outcomes). The caller
    must only report/log these counts as authoritative after its own
    surrounding db.commit() succeeds; a caller that logs them before or
    without a successful commit misrepresents flushed-but-not-yet-durable
    work as done."""

    candidates: int
    projected: int
    skipped: int
    failed: int
    expected_projections: tuple[tuple[uuid.UUID, Decimal, str], ...] = ()


async def refresh_exit_recovery_outcomes(*, db: AsyncSession) -> ExitRecoveryOutcomeSweepResult:
    """Supervise post-package outcomes even after submission authority
    expires. Flushes only -- never commits; commit ownership belongs to
    the caller (run_orchestration_cycle), matching every adjacent sweep in
    that function (sweep_stale_autonomous_execution_claims, poll_
    unresolved_live_orders). Returns counts for the caller to log only
    once its own commit has actually succeeded."""
    recoveries = (await db.scalars(select(ControlledProofExitRecovery).where(
        ControlledProofExitRecovery.status.in_(("IN_PROGRESS", "EXPIRED", "BLOCKED")),
    ).order_by(ControlledProofExitRecovery.authorized_at.desc()))).all()
    candidates = len(recoveries)
    projected = 0
    skipped = 0
    failed = 0
    expected_projections: list[tuple[uuid.UUID, Decimal, str]] = []
    projected_proof_ids: set[uuid.UUID] = set()
    for recovery in recoveries:
        try:
            if recovery.proof_id in projected_proof_ids:
                skipped += 1
                logger.info(
                    "exit_recovery_outcome_candidate proof_id=%s outcome=skipped "
                    "reason=proof_already_projected_in_sweep proof_fields_matched_recovered_outcome=true "
                    "orm_mutation_occurred=false flush_readback_verified=false",
                    recovery.proof_id,
                )
                continue
            proof = await db.scalar(select(ControlledProofRun).where(
                ControlledProofRun.proof_id == recovery.proof_id,
                ControlledProofRun.sell_package_id.is_not(None),
            ).limit(1))
            if proof is None:
                skipped += 1
                logger.info(
                    "exit_recovery_outcome_candidate proof_id=%s outcome=skipped reason=proof_not_selected "
                    "proof_fields_matched_recovered_outcome=false orm_mutation_occurred=false "
                    "flush_readback_verified=false",
                    recovery.proof_id,
                )
                continue
            if recovery.status == "BLOCKED":
                diagnostics: dict[str, object] = {}
                persisted = await project_blocked_exit_recovery_outcome(
                    db=db, recovery=recovery, proof=proof, diagnostics=diagnostics,
                )
                if persisted:
                    projected += 1
                    outcome = "projected"
                    projected_proof_ids.add(proof.proof_id)
                    expected_projections.append((
                        proof.proof_id, proof.net_pnl_usd, proof.terminal_verdict,
                    ))
                else:
                    skipped += 1
                    outcome = "skipped"
                logger.info(
                    "exit_recovery_outcome_candidate proof_id=%s outcome=%s reason=%s "
                    "proof_fields_matched_recovered_outcome=%s orm_mutation_occurred=%s "
                    "flush_readback_verified=%s",
                    proof.proof_id, outcome, diagnostics.get("reason", "projector_rejected"),
                    str(bool(diagnostics.get("proof_fields_matched_recovered_outcome"))).lower(),
                    str(bool(diagnostics.get("orm_mutation_occurred"))).lower(),
                    str(bool(diagnostics.get("flush_readback_verified"))).lower(),
                )
            else:
                before_status = recovery.status
                await refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)
                if recovery.status != before_status:
                    projected += 1
                    outcome = "projected"
                    reason = "recovery_status_transitioned"
                else:
                    skipped += 1
                    outcome = "skipped"
                    reason = "recovery_status_unchanged"
                logger.info(
                    "exit_recovery_outcome_candidate proof_id=%s outcome=%s reason=%s "
                    "proof_fields_matched_recovered_outcome=false orm_mutation_occurred=false "
                    "flush_readback_verified=false",
                    proof.proof_id, outcome, reason,
                )
        except Exception:
            failed += 1
            logger.exception(
                "exit_recovery_outcome_candidate proof_id=%s outcome=failed reason=exception "
                "proof_fields_matched_recovered_outcome=false orm_mutation_occurred=false "
                "flush_readback_verified=false recovery_id=%s",
                recovery.proof_id, recovery.recovery_id,
            )
    return ExitRecoveryOutcomeSweepResult(
        candidates=candidates, projected=projected, skipped=skipped, failed=failed,
        expected_projections=tuple(expected_projections),
    )
