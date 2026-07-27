from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.live_crypto_order import LiveCryptoOrder
from app.schemas.live_crypto_orders import LiveCryptoOrderReconcileRequest
from app.services.live_crypto_orders import LiveCryptoOrderService

logger = logging.getLogger(__name__)

RECONCILIATION_SCHEDULER_ACTOR = "system:reconciliation_scheduler"

# Every LiveCryptoOrder status that represents a genuinely final,
# already-resolved outcome (mirrors _ORDER_STATUS_TO_RELEASED_CLAIM_STATUS
# in autonomous_execution_claims.py). Deliberately exclude-based, not an
# allow-list of "still in progress" statuses: a status this scheduler
# doesn't yet recognize must default to "needs reconciliation", never be
# silently skipped -- the same class of enumeration-drift defect already
# found and fixed once this session for AutonomousExecutionClaim's own
# status set (the old, permanently-blocking campaign-version constraint).
_TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}


@dataclass(frozen=True, slots=True)
class ReconciliationPollOutcome:
    candidates_discovered: int
    reconciled: int
    still_pending: int
    failed: int


async def _rollback_if_supported(*, db: AsyncSession) -> None:
    if not hasattr(db, "rollback"):
        return
    await db.rollback()


async def discover_reconciliation_candidates(
    *, db: AsyncSession, limit: int,
) -> list[UUID]:
    """Every submitted live order whose outcome is not yet authoritatively
    resolved, oldest first. `submitted_at IS NOT NULL` excludes orders that
    were never actually sent to the provider (nothing to reconcile against
    yet) -- PENDING_CONFIRMATION/VALIDATING packages are prepared, not
    submitted. Row-locked with SKIP LOCKED so a concurrent poller (a second
    orchestration worker instance briefly alive during a deploy, or an
    overlapping manual reconciliation on the same order) is skipped rather
    than raced -- correctness itself is independently guaranteed by
    LiveAccountingRecord's own idempotency_key and
    (provider_order_id, provider_fill_id, record_type) unique constraints
    regardless, but this avoids wasted duplicate provider calls."""
    statement = (
        select(LiveCryptoOrder.live_crypto_order_id)
        .where(
            LiveCryptoOrder.submitted_at.is_not(None),
            LiveCryptoOrder.status.not_in(_TERMINAL_ORDER_STATUSES),
        )
        .order_by(LiveCryptoOrder.submitted_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = (await db.scalars(statement)).all()
    return list(rows)


async def poll_unresolved_live_orders(
    *, db: AsyncSession, actor: str = RECONCILIATION_SCHEDULER_ACTOR, limit: int | None = None,
) -> ReconciliationPollOutcome:
    """Automatic counterpart to the operator CLI/`/reconcile` route --
    reuses the identical `LiveCryptoOrderService.reconcile()` authoritative
    service (no provider lookup, fill accounting, fee calculation, or
    ledger logic is duplicated here), so both Controlled Proof and ordinary
    autonomous live execution progress through the exact same reconciliation
    path an operator would use manually. Called once per orchestration
    cycle (see continuous_pipeline_worker.run_orchestration_cycle) --
    bounded by `limit`/`automatic_live_order_reconciliation_batch_limit` so
    one cycle can never issue an unbounded number of provider requests.
    Every candidate is attempted independently: one candidate's failure
    (provider outage, missing credentials, ambiguous response, accounting
    mismatch -- reconcile_live_order_and_fills already fails closed on all
    of these, recording `reconciliation_required`/`conflict`/
    `balance_mismatch` rather than fabricating an outcome) never aborts the
    rest of the batch, and never blindly retries or resubmits anything --
    reconcile() only ever queries the provider for existing order state."""
    settings = get_settings()
    if not settings.automatic_live_order_reconciliation_enabled:
        logger.info("live_order_reconciliation_poll_skipped reason=automatic_reconciliation_disabled")
        return ReconciliationPollOutcome(0, 0, 0, 0)

    batch_limit = limit if limit is not None else settings.automatic_live_order_reconciliation_batch_limit
    logger.info("live_order_reconciliation_poll_started batch_limit=%s", batch_limit)

    candidate_ids = await discover_reconciliation_candidates(db=db, limit=batch_limit)
    logger.info("live_order_reconciliation_candidates_discovered count=%s", len(candidate_ids))

    reconciled = 0
    still_pending = 0
    failed = 0
    service = LiveCryptoOrderService()
    for live_crypto_order_id in candidate_ids:
        logger.info("live_order_reconciliation_attempt_started live_crypto_order_id=%s", live_crypto_order_id)
        try:
            response = await service.reconcile(
                db=db,
                live_crypto_order_id=live_crypto_order_id,
                request=LiveCryptoOrderReconcileRequest(operator_identity=actor),
            )
        except Exception:
            failed += 1
            logger.exception(
                "live_order_reconciliation_attempt_failed live_crypto_order_id=%s",
                live_crypto_order_id,
            )
            await _rollback_if_supported(db=db)
            continue

        status = response.live_crypto_order.status
        if status in _TERMINAL_ORDER_STATUSES:
            reconciled += 1
            logger.info(
                "live_order_reconciliation_attempt_resolved live_crypto_order_id=%s status=%s "
                "provider_order_id=%s provider_fill_observed=%s",
                live_crypto_order_id, status, response.provider_order_id, response.provider_fill_observed,
            )
        else:
            still_pending += 1
            logger.info(
                "live_order_reconciliation_attempt_still_pending live_crypto_order_id=%s status=%s reconciliation_status=%s",
                live_crypto_order_id, status, response.reconciliation_status,
            )

    logger.info(
        "live_order_reconciliation_poll_completed candidates=%s reconciled=%s still_pending=%s failed=%s",
        len(candidate_ids), reconciled, still_pending, failed,
    )
    return ReconciliationPollOutcome(
        candidates_discovered=len(candidate_ids), reconciled=reconciled, still_pending=still_pending, failed=failed,
    )
