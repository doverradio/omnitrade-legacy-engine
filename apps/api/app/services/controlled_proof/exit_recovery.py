from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.services.position_lifecycle.source_adapter import load_position_snapshots

_RECOVERABLE_PROOF_STATES = {"EXPIRED", "FAILED"}
_ACTIVE_RECOVERY_STATES = {"AUTHORIZED", "IN_PROGRESS"}


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
    if proof.status not in _RECOVERABLE_PROOF_STATES:
        raise InvalidRequestError(message="Controlled Proof is not eligible for exit recovery", details={"status": proof.status})
    if proof.package_id is None:
        raise InvalidRequestError(message="Controlled Proof BUY package is missing", details={})
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

    from app.services.orchestration.continuous_pipeline_worker import _has_open_live_order, _has_unresolved_reconciliation
    if await _has_open_live_order(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
        raise InvalidRequestError(message="An open provider order blocks exit recovery", details={})
    if await _has_unresolved_reconciliation(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
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
    if position is None or proof.position_id is None or str(position.position_id) != str(proof.position_id):
        raise InvalidRequestError(message="Open position linkage does not match this Controlled Proof", details={})


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
        item.status = "EXPIRED"; item.updated_at = now
        db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.expired", entity_type="controlled_proof_exit_recovery", entity_id=item.recovery_id, before_state={"status": before}, after_state={"status": "EXPIRED"}))
    if expired:
        await db.flush()
    return await db.scalar(select(ControlledProofExitRecovery.recovery_id).where(
        ControlledProofExitRecovery.status.in_(_ACTIVE_RECOVERY_STATES), ControlledProofExitRecovery.expires_at > now,
    ).order_by(ControlledProofExitRecovery.authorized_at.asc()).limit(1))


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

    An execution claim is the boundary: once one exists, this helper refuses
    replacement and leaves recovery fail-closed. It never renews or mutates
    the expired authority evidence on the historical package.
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
    if existing_claim is not None:
        if existing_claim.claim_status != "FAILED_PRE_PROVIDER":
            raise InvalidRequestError(message="Stale SELL package has unresolved execution lineage", details={})
        if existing_claim.live_order_id is not None:
            failed_order = await db.scalar(select(LiveCryptoOrder).where(
                LiveCryptoOrder.live_crypto_order_id == existing_claim.live_order_id,
            ).with_for_update().limit(1))
            evidence = (
                failed_order.safe_provider_response
                if failed_order is not None and isinstance(failed_order.safe_provider_response, dict)
                else {}
            )
            if (
                failed_order is None
                or failed_order.status != "CANCELLED"
                or failed_order.provider_order_id is not None
                or failed_order.submitted_at is not None
                or evidence.get("provider_call_made") is not False
            ):
                raise InvalidRequestError(message="Stale SELL package provider boundary is unresolved", details={})
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
        },
    ))
    await db.flush()


async def get_exit_recovery_view(*, db: AsyncSession, proof_id: uuid.UUID) -> dict:
    await find_pending_exit_recovery_id(db=db)
    recovery = await db.scalar(select(ControlledProofExitRecovery).where(ControlledProofExitRecovery.proof_id == proof_id).order_by(ControlledProofExitRecovery.authorized_at.desc()).limit(1))
    if recovery is None:
        raise NotFoundError(message="Controlled Proof exit recovery not found", details={"proof_id": str(proof_id)})
    return {
        "recovery_id": recovery.recovery_id, "proof_id": recovery.proof_id, "status": recovery.status,
        "idempotency_key": recovery.idempotency_key, "authorized_by": recovery.authorized_by,
        "authorized_at": recovery.authorized_at, "expires_at": recovery.expires_at,
        "claimed_at": recovery.claimed_at, "completed_at": recovery.completed_at,
        "blocked_reason": recovery.blocked_reason, "failure_reason": recovery.failure_reason,
        "audit_correlation_id": recovery.audit_correlation_id,
    }


async def record_exit_recovery_waiting(*, db: AsyncSession, recovery: ControlledProofExitRecovery, reason: str) -> None:
    recovery.failure_reason = f"retryable:{reason}"; recovery.updated_at = _utcnow()
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.waiting", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": recovery.status}, after_state={"status": recovery.status, "failure_reason": recovery.failure_reason}))
    await db.flush()


async def block_exit_recovery(*, db: AsyncSession, recovery: ControlledProofExitRecovery, reason: str) -> None:
    before = recovery.status
    recovery.status = "BLOCKED"; recovery.blocked_reason = reason; recovery.updated_at = _utcnow()
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.blocked", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": before}, after_state={"status": "BLOCKED", "reason": reason}))
    await db.flush()


async def refresh_exit_recovery_completion(
    *, db: AsyncSession, recovery: ControlledProofExitRecovery, proof: ControlledProofRun,
) -> None:
    if proof.sell_live_crypto_order_id is None:
        from app.services.controlled_proof.service import get_controlled_proof_view
        await get_controlled_proof_view(db=db, proof_id=proof.proof_id)
        await db.refresh(proof)
    if proof.sell_live_crypto_order_id is None:
        return
    sell_order = await db.scalar(select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == proof.sell_live_crypto_order_id))
    if sell_order is None or sell_order.status != "FILLED":
        return
    from app.services.orchestration.continuous_pipeline_worker import _has_unresolved_reconciliation
    if await _has_unresolved_reconciliation(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
        return
    _runtime, profile_id = await _load_scope(db, proof)
    if await compute_signed_owned_quantity(db=db, live_trading_profile_id=profile_id, symbol=proof.product_id) != 0:
        return
    before = recovery.status
    recovery.status = "COMPLETED"; recovery.completed_at = _utcnow(); recovery.updated_at = recovery.completed_at
    db.add(AuditLog(actor="system:controlled_proof_worker", action="controlled_proof_exit_recovery.completed", entity_type="controlled_proof_exit_recovery", entity_id=recovery.recovery_id, before_state={"status": before}, after_state={"status": "COMPLETED", "sell_live_crypto_order_id": str(sell_order.live_crypto_order_id)}))
    await db.flush()


async def refresh_exit_recovery_outcomes(*, db: AsyncSession) -> None:
    """Supervise post-package outcomes even after submission authority expires."""
    recoveries = (await db.scalars(select(ControlledProofExitRecovery).where(
        ControlledProofExitRecovery.status.in_(("IN_PROGRESS", "EXPIRED")),
    ).order_by(ControlledProofExitRecovery.authorized_at.desc()))).all()
    seen_proofs: set[uuid.UUID] = set()
    for recovery in recoveries:
        if recovery.proof_id in seen_proofs:
            continue
        seen_proofs.add(recovery.proof_id)
        proof = await db.scalar(select(ControlledProofRun).where(
            ControlledProofRun.proof_id == recovery.proof_id,
            ControlledProofRun.sell_package_id.is_not(None),
        ).limit(1))
        if proof is not None:
            await refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)
