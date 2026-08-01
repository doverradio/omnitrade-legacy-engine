from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_proof_sell_attempt import AutonomousProofSellAttempt
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration.autonomous_position_exit_activation import activate_exit_package_and_claim
from app.services.orchestration.autonomous_position_exit_authority import issue_exit_authority
from app.services.orchestration.autonomous_position_exit_construction import construct_exit_paperwork
from app.services.orchestration.autonomous_position_exit_evaluation import persisted_exit_evaluation
from app.services.orchestration.autonomous_position_exit_order import construct_autonomous_exit_order
from app.services.orchestration.autonomous_position_exit_reconciliation import reconcile_autonomous_exit_order
from app.services.orchestration.autonomous_position_exit_submission import submit_autonomous_exit_order


@dataclass(frozen=True, slots=True)
class ProofSellWorkerResult:
    action: str
    attempt_id: uuid.UUID | None
    stage: str | None
    provider_call_made: bool = False


def _configured_scope() -> tuple[uuid.UUID, int, uuid.UUID] | None:
    settings = get_settings()
    values = (
        settings.autonomous_proof_sell_campaign_id,
        settings.autonomous_proof_sell_campaign_version,
        settings.autonomous_proof_sell_runtime_campaign_id,
    )
    if any(value is None for value in values) or int(values[1]) <= 0:
        return None
    return values[0], int(values[1]), values[2]  # type: ignore[return-value]


def _scope() -> tuple[uuid.UUID, int, uuid.UUID] | None:
    if not get_settings().autonomous_proof_sell_worker_enabled:
        return None
    return _configured_scope()


def _retry(attempt: AutonomousProofSellAttempt, *, now: datetime, blocker: str) -> None:
    attempt.retry_count += 1
    attempt.blocker = blocker
    delay = min(900, 30 * (2 ** min(attempt.retry_count - 1, 5)))
    attempt.next_attempt_at = now + timedelta(seconds=delay)
    attempt.updated_at = now


def _terminal(attempt: AutonomousProofSellAttempt, *, now: datetime, reason: str,
              verified: bool = False) -> None:
    attempt.stage = "TERMINAL"
    attempt.hard_stopped = True
    attempt.terminal_reason = reason
    attempt.proof_sell_verified = verified
    attempt.blocker = None
    attempt.next_attempt_at = None
    attempt.updated_at = now


async def _locked_attempt(db: AsyncSession, scope: tuple[uuid.UUID, int, uuid.UUID]) -> AutonomousProofSellAttempt | None:
    return await db.scalar(select(AutonomousProofSellAttempt).where(
        AutonomousProofSellAttempt.campaign_id == scope[0],
        AutonomousProofSellAttempt.campaign_version == scope[1],
        AutonomousProofSellAttempt.runtime_campaign_id == scope[2],
    ).with_for_update(skip_locked=True).limit(1))


async def _advance_locked_stage(
    *, db: AsyncSession, attempt: AutonomousProofSellAttempt,
    custody: AutonomousPositionCustody, observed_at: datetime,
) -> ProofSellWorkerResult:
    if attempt.stage == "SELECTED":
        evidence = persisted_exit_evaluation(custody)
        if evidence.get("disposition") != "EXIT_RECOMMENDED" or evidence.get("price_fresh") is not True:
            _retry(attempt, now=observed_at, blocker="exit_evaluation_not_ready")
            await db.flush()
            return ProofSellWorkerResult("waiting_for_evaluation", attempt.attempt_id, attempt.stage)
        attempt.stage = "EVALUATED"
    elif attempt.stage == "EVALUATED":
        authority, blockers, _ = await issue_exit_authority(db=db, custody_id=custody.custody_id, now=observed_at)
        if authority is None or blockers or authority.authority_state != "ARMED":
            _terminal(attempt, now=observed_at, reason="authority_blocked")
        else:
            attempt.authority_id = authority.authority_id; attempt.stage = "AUTHORIZED"
    elif attempt.stage == "AUTHORIZED":
        result = await construct_exit_paperwork(db=db, authority_id=attempt.authority_id, now=observed_at)
        attempt.package_id = result.package_id; attempt.stage = "PACKAGED"
    elif attempt.stage == "PACKAGED":
        result = await activate_exit_package_and_claim(db=db, authority_id=attempt.authority_id, now=observed_at)
        attempt.activation_id = result.activation_id; attempt.claim_id = result.claim_id; attempt.stage = "CLAIMED"
    elif attempt.stage == "CLAIMED":
        result = await construct_autonomous_exit_order(db=db, claim_id=attempt.claim_id, now=observed_at)
        attempt.order_id = result.order_id; attempt.stage = "ORDERED"
    elif attempt.stage == "ORDERED":
        if not get_settings().autonomous_position_exit_submission_enabled:
            _retry(attempt, now=observed_at, blocker="submission_gate_disabled")
            await db.flush()
            return ProofSellWorkerResult("submission_disabled", attempt.attempt_id, attempt.stage)
        result = await submit_autonomous_exit_order(db=db, order_id=attempt.order_id, now=observed_at)
        if result.status == "REJECTED":
            await reconcile_autonomous_exit_order(db=db, order_id=attempt.order_id, now=observed_at)
            _terminal(attempt, now=observed_at, reason="provider_rejected")
        elif result.status in {"SUBMISSION_PENDING", "RECONCILIATION_REQUIRED", "UNKNOWN"}:
            _retry(attempt, now=observed_at, blocker="provider_outcome_uncertain")
        else:
            attempt.stage = "RECONCILING"
        attempt.updated_at = observed_at
        await db.flush()
        return ProofSellWorkerResult("submitted", attempt.attempt_id, attempt.stage, result.provider_call_made)
    elif attempt.stage == "RECONCILING":
        result = await reconcile_autonomous_exit_order(db=db, order_id=attempt.order_id, now=observed_at)
        if result.terminal:
            reason = "proof_sell_verified" if result.proof_sell_verified else "sell_closed_without_profit"
            _terminal(attempt, now=observed_at, reason=reason, verified=result.proof_sell_verified)
            attempt.reconciliation_id = custody.exit_reconciliation_event_id
        elif result.status in {"REJECTED", "CANCELLED"} or custody.custody_state == "BLOCKED":
            _terminal(attempt, now=observed_at, reason=f"sell_{result.status.lower()}")
        else:
            _retry(attempt, now=observed_at, blocker="reconciliation_incomplete")
    if attempt.stage not in {"RECONCILING", "TERMINAL"}:
        attempt.blocker = None
        attempt.next_attempt_at = None
    attempt.updated_at = observed_at
    await db.flush()
    return ProofSellWorkerResult("advanced", attempt.attempt_id, attempt.stage)


async def advance_one_autonomous_proof_sell_stage(
    *, db: AsyncSession, now: datetime | None = None, cadence_seconds: int | None = None,
) -> ProofSellWorkerResult:
    """Advance at most one persisted stage for the explicitly pinned campaign.

    A missing/partial scope is deliberately indistinguishable from a disabled
    gate. The campaign unique constraint is the permanent one-attempt latch.
    """
    scope = _scope()
    if scope is None:
        return ProofSellWorkerResult("disabled_or_ambiguous", None, None)
    # Exit claims live for two minutes. A 30-second ceiling leaves multiple
    # ordinary cycles for order construction without widening that authority.
    if cadence_seconds is None or cadence_seconds <= 0 or cadence_seconds > 30:
        return ProofSellWorkerResult("unsafe_worker_cadence", None, None)
    observed_at = now or datetime.now(timezone.utc)
    attempt = await _locked_attempt(db, scope)
    if attempt is None:
        custody = await db.scalar(select(AutonomousPositionCustody).where(
            AutonomousPositionCustody.campaign_id == scope[0],
            AutonomousPositionCustody.campaign_version == scope[1],
            AutonomousPositionCustody.runtime_campaign_id == scope[2],
            AutonomousPositionCustody.autonomous_origin.is_(True),
            AutonomousPositionCustody.proof_eligible.is_(True),
            AutonomousPositionCustody.disqualification_reason.is_(None),
            AutonomousPositionCustody.custody_state.in_(("ACTIVE", "EXIT_PENDING")),
            AutonomousPositionCustody.terminal_at.is_(None),
            AutonomousPositionCustody.active_sell_claim_id.is_(None),
            AutonomousPositionCustody.active_sell_order_id.is_(None),
        ).order_by(AutonomousPositionCustody.created_at.asc(), AutonomousPositionCustody.custody_id.asc())
            .with_for_update(skip_locked=True).limit(1))
        if custody is None:
            return ProofSellWorkerResult("no_eligible_custody", None, None)
        attempt = AutonomousProofSellAttempt(
            custody_id=custody.custody_id, campaign_id=scope[0], campaign_version=scope[1],
            runtime_campaign_id=scope[2], stage="SELECTED", retry_count=0,
            hard_stopped=False, proof_sell_verified=False, created_at=observed_at, updated_at=observed_at,
        )
        try:
            async with db.begin_nested():
                db.add(attempt)
                await db.flush()
        except IntegrityError:
            # Another worker won the campaign latch while this worker held a
            # different eligible custody lock. Resolve as a safe duplicate;
            # never proceed to a replacement custody.
            winner = await db.scalar(select(AutonomousProofSellAttempt).where(
                AutonomousProofSellAttempt.campaign_id == scope[0],
                AutonomousProofSellAttempt.campaign_version == scope[1],
                AutonomousProofSellAttempt.runtime_campaign_id == scope[2],
            ).limit(1))
            return ProofSellWorkerResult(
                "duplicate_safe", None if winner is None else winner.attempt_id,
                None if winner is None else winner.stage,
            )
        return ProofSellWorkerResult("selected", attempt.attempt_id, attempt.stage)

    if attempt.hard_stopped or attempt.stage == "TERMINAL":
        return ProofSellWorkerResult("hard_stopped", attempt.attempt_id, "TERMINAL")
    if attempt.next_attempt_at is not None and attempt.next_attempt_at > observed_at:
        return ProofSellWorkerResult("backoff", attempt.attempt_id, attempt.stage)
    custody = await db.get(AutonomousPositionCustody, attempt.custody_id)
    if custody is None or custody.disqualification_reason is not None or not custody.proof_eligible:
        _terminal(attempt, now=observed_at, reason="custody_disqualified_or_missing")
        await db.flush()
        return ProofSellWorkerResult("terminal", attempt.attempt_id, attempt.stage)

    failed_stage = attempt.stage
    try:
        # Submission deliberately commits intent before provider contact in its
        # existing service. Every other stage is enclosed in this coordinator-
        # owned savepoint, so a flush followed by an exception cannot leak an
        # artifact while the durable attempt remains on its predecessor.
        if failed_stage == "ORDERED":
            return await _advance_locked_stage(
                db=db, attempt=attempt, custody=custody, observed_at=observed_at,
            )
        async with db.begin_nested():
            return await _advance_locked_stage(
                db=db, attempt=attempt, custody=custody, observed_at=observed_at,
            )
    except Exception as exc:
        if failed_stage == "ORDERED":
            # A database error may have invalidated the outer transaction. The
            # submission service's pre-contact intent commit remains durable;
            # rollback only clears the failed unit, then the same attempt is
            # reacquired for recovery evidence. It never selects a replacement.
            await db.rollback()
            attempt = await _locked_attempt(db, scope)
            if attempt is None:
                raise
        else:
            # begin_nested() has rolled back every service mutation and flush.
            # Refresh discards in-memory values from the failed savepoint.
            await db.refresh(attempt)
        if attempt.retry_count >= 4:
            _terminal(attempt, now=observed_at, reason=f"governed_stage_failure:{type(exc).__name__}")
        else:
            _retry(attempt, now=observed_at, blocker=f"{type(exc).__name__}:stage_failed")
        await db.flush()
        return ProofSellWorkerResult("stage_failed", attempt.attempt_id, attempt.stage)


async def inspect_autonomous_proof_sell_worker(*, db: AsyncSession) -> dict[str, Any]:
    settings = get_settings(); scope = _configured_scope()
    attempt = None if scope is None else await db.scalar(select(AutonomousProofSellAttempt).where(
        AutonomousProofSellAttempt.campaign_id == scope[0],
        AutonomousProofSellAttempt.campaign_version == scope[1],
        AutonomousProofSellAttempt.runtime_campaign_id == scope[2],
    ).limit(1))
    custody = None if attempt is None else await db.get(AutonomousPositionCustody, attempt.custody_id)
    order = None if attempt is None or attempt.order_id is None else await db.get(LiveCryptoOrder, attempt.order_id)
    stages = ("SELECTED", "EVALUATED", "AUTHORIZED", "PACKAGED", "CLAIMED", "ORDERED", "RECONCILING", "TERMINAL")
    next_stage = None if attempt is None or attempt.stage == "TERMINAL" else stages[stages.index(attempt.stage) + 1]
    return {
        "worker_gate_enabled": settings.autonomous_proof_sell_worker_enabled,
        "submission_gate_enabled": settings.autonomous_position_exit_submission_enabled,
        "configuration_complete": scope is not None,
        "campaign_id": None if scope is None else str(scope[0]), "campaign_version": None if scope is None else scope[1],
        "runtime_campaign_id": None if scope is None else str(scope[2]),
        "attempt_id": None if attempt is None else str(attempt.attempt_id),
        "custody_id": None if attempt is None else str(attempt.custody_id),
        "current_stage": None if attempt is None else attempt.stage, "next_stage": next_stage,
        "authority_id": None if attempt is None or attempt.authority_id is None else str(attempt.authority_id),
        "package_id": None if attempt is None or attempt.package_id is None else str(attempt.package_id),
        "activation_id": None if attempt is None or attempt.activation_id is None else str(attempt.activation_id),
        "claim_id": None if attempt is None or attempt.claim_id is None else str(attempt.claim_id),
        "order_id": None if attempt is None or attempt.order_id is None else str(attempt.order_id),
        "provider_order_id": None if order is None else order.provider_order_id,
        "requested_quantity": None if order is None else format(Decimal(str(order.requested_base_quantity)), "f"),
        "submitted_quantity": None if order is None else format(Decimal(str(order.normalized_base_quantity)), "f"),
        "filled_quantity": None if custody is None else format(Decimal(str(custody.realized_sold_quantity or 0)), "f"),
        "remaining_quantity": None if custody is None else format(Decimal(str(custody.observed_remaining_quantity)), "f"),
        "gross_proceeds": None if custody is None else format(Decimal(str(custody.realized_gross_sell_proceeds or 0)), "f"),
        "sell_fees": None if custody is None else format(Decimal(str(custody.realized_sell_fees or 0)), "f"),
        "cost_basis": None if custody is None else format(Decimal(str(custody.allocated_buy_cost_basis or 0)), "f"),
        "buy_fees": None if custody is None else format(Decimal(str(custody.allocated_buy_fees or 0)), "f"),
        "realized_net_profit": None if custody is None or custody.realized_net_profit is None else format(Decimal(str(custody.realized_net_profit)), "f"),
        "blocker": None if attempt is None else attempt.blocker,
        "retry_count": None if attempt is None else attempt.retry_count,
        "next_attempt_at": None if attempt is None or attempt.next_attempt_at is None else attempt.next_attempt_at.isoformat(),
        "hard_stopped": False if attempt is None else attempt.hard_stopped,
        "proof_eligible": None if custody is None else custody.proof_eligible,
        "disqualification_reason": None if custody is None else custody.disqualification_reason,
        "autonomous_proof_sell_verified": False if attempt is None else attempt.proof_sell_verified,
        "first_autonomous_profit_complete": False,
    }
