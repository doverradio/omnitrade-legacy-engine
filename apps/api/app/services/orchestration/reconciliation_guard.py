from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent


UNRESOLVED_RECONCILIATION_STATES = {
    "open",
    "partially_filled",
    "reconciliation_required",
    "unknown",
    "conflict",
    "balance_mismatch",
}


def latest_reconciliation_event_per_order(*, provider: str, environment: str, product: str):
    """Return the effective reconciliation sequence for each order in market scope."""
    return (
        select(
            LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
            func.max(LiveReconciliationEvent.sequence_number).label("max_seq"),
        )
        .join(LiveCryptoOrder, LiveCryptoOrder.live_crypto_order_id == LiveReconciliationEvent.live_crypto_order_id)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.product_id == product)
        .where(LiveReconciliationEvent.live_crypto_order_id.is_not(None))
        .group_by(LiveReconciliationEvent.live_crypto_order_id)
        .subquery()
    )


async def has_unresolved_reconciliation(*, db: AsyncSession, provider: str, environment: str, product: str) -> bool:
    """True when the latest reconciliation event per order in this
    provider/environment/product scope is still in an unresolved state.

    live_reconciliation_events is append-only (immutable audit log): an
    order accumulates a new row every time it is re-reconciled -- existing
    rows are never updated or deleted -- so only the LATEST row per order
    reflects its current effective state; matching any historical row would
    report an order unresolved forever purely because of superseded history
    (a confirmed past production defect). Shared by the continuous pipeline
    worker's own automatic-package gate and Controlled Proof's stale-proof
    recovery safety check, so both agree on exactly the same definition of
    "unresolved" for the same market scope."""
    latest = latest_reconciliation_event_per_order(provider=provider, environment=environment, product=product)
    row = await db.scalar(
        select(LiveReconciliationEvent.id)
        .join(
            latest,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest.c.order_id,
                LiveReconciliationEvent.sequence_number == latest.c.max_seq,
            ),
        )
        .where(LiveReconciliationEvent.reconciliation_status.in_(UNRESOLVED_RECONCILIATION_STATES))
        .limit(1)
    )
    return row is not None


def claim_blocking_reconciliation_statement(*, provider: str, environment: str, product: str):
    """Select one current unresolved or unscopable reconciliation, fail closed."""
    latest = latest_reconciliation_event_per_order(
        provider=provider, environment=environment, product=product,
    )
    scoped = (
        select(LiveReconciliationEvent.id)
        .join(
            latest,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest.c.order_id,
                LiveReconciliationEvent.sequence_number == latest.c.max_seq,
            ),
        )
        .where(LiveReconciliationEvent.reconciliation_status.in_(UNRESOLVED_RECONCILIATION_STATES))
    )
    # A provider reconciliation whose order identity is absent cannot be
    # proven to belong to a different environment/product. Keep it blocking.
    ambiguous = (
        select(LiveReconciliationEvent.id)
        .outerjoin(LiveCryptoOrder, LiveCryptoOrder.live_crypto_order_id == LiveReconciliationEvent.live_crypto_order_id)
        .where(LiveReconciliationEvent.provider_name == provider)
        .where(LiveReconciliationEvent.reconciliation_status.in_(UNRESOLVED_RECONCILIATION_STATES))
        .where(LiveCryptoOrder.live_crypto_order_id.is_(None))
    )
    return scoped.union_all(ambiguous).limit(1)
