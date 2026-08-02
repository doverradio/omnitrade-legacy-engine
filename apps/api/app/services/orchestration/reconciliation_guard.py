from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, select
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


def _latest_reconciliation_event_ids_for_scope(scope_clause: ColumnElement[bool]):
    """(order_id, max sequence_number) for every IDENTIFIED order within an
    arbitrary LiveReconciliationEvent-column scope (e.g. one live trading
    profile or one capital campaign) -- the same "latest per order, tie-
    broken by the highest sequence_number" rule as
    latest_reconciliation_event_per_order above, just scoped directly off
    columns that already live on LiveReconciliationEvent itself
    (live_trading_profile_id, capital_campaign_id) rather than through a
    LiveCryptoOrder join keyed on provider/environment/product. Rows with no
    live_crypto_order_id are deliberately excluded here; the caller
    (_count_unresolved_reconciliation_events) accounts for those separately
    and fail-closed, since there is no order identity to collapse repeat
    history into."""
    return (
        select(
            LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
            func.max(LiveReconciliationEvent.sequence_number).label("max_seq"),
        )
        .where(scope_clause)
        .where(LiveReconciliationEvent.live_crypto_order_id.is_not(None))
        .group_by(LiveReconciliationEvent.live_crypto_order_id)
        .subquery()
    )


async def _count_unresolved_reconciliation_events(
    *,
    db: AsyncSession,
    scope_clause: ColumnElement[bool],
    unresolved_statuses: Collection[str] = UNRESOLVED_RECONCILIATION_STATES,
) -> int:
    """Counts reconciliation state currently blocking within an arbitrary
    LiveReconciliationEvent-column scope, applying the same two rules as
    has_unresolved_reconciliation/claim_blocking_reconciliation_statement:

    1. An identified order (live_crypto_order_id is set) counts once, only
       if its LATEST event (highest sequence_number) is still unresolved --
       matching any of its own superseded history would count it forever
       (the confirmed campaign-promotion production defect: an order's
       earlier partially_filled/reconciliation_required rows kept blocking
       promotion long after a later reconciliation pass recorded that same
       order as filled).
    2. An event with no live_crypto_order_id can never be attributed to a
       specific order's later resolution, so it can never be proven
       superseded -- every such row counts individually and unconditionally
       whenever its own status is unresolved. Fail closed: never
       deduplicated or collapsed with any other row, identityless or not.

    unresolved_statuses defaults to the full UNRESOLVED_RECONCILIATION_STATES
    vocabulary, but callers with their own narrower/different blocking-status
    definition (e.g. commissioned-readiness preview, which deliberately does
    not treat 'open'/'partially_filled' as blocking) can supply their own set
    -- only the latest-per-order/identityless rules are shared, never the
    status vocabulary itself.
    """
    latest = _latest_reconciliation_event_ids_for_scope(scope_clause)
    identified_count = await db.scalar(
        select(func.count())
        .select_from(LiveReconciliationEvent)
        .join(
            latest,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest.c.order_id,
                LiveReconciliationEvent.sequence_number == latest.c.max_seq,
            ),
        )
        .where(scope_clause)
        .where(LiveReconciliationEvent.reconciliation_status.in_(unresolved_statuses))
    )
    identityless_count = await db.scalar(
        select(func.count())
        .select_from(LiveReconciliationEvent)
        .where(scope_clause)
        .where(LiveReconciliationEvent.live_crypto_order_id.is_(None))
        .where(LiveReconciliationEvent.reconciliation_status.in_(unresolved_statuses))
    )
    return int(identified_count or 0) + int(identityless_count or 0)


async def count_unresolved_reconciliation_events_for_profile(*, db: AsyncSession, live_trading_profile_id: UUID) -> int:
    """Canonical campaign-binding's profile-scoped readiness counter. See
    _count_unresolved_reconciliation_events for the two rules applied."""
    return await _count_unresolved_reconciliation_events(
        db=db,
        scope_clause=LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id,
    )


async def count_unresolved_reconciliation_events_for_campaign(*, db: AsyncSession, capital_campaign_id: int) -> int:
    """Canonical campaign-binding's campaign-scoped readiness counter. See
    _count_unresolved_reconciliation_events for the two rules applied."""
    return await _count_unresolved_reconciliation_events(
        db=db,
        scope_clause=LiveReconciliationEvent.capital_campaign_id == capital_campaign_id,
    )


async def has_unresolved_reconciliation_for_campaign(
    *,
    db: AsyncSession,
    capital_campaign_id: int,
    unresolved_statuses: Collection[str] = UNRESOLVED_RECONCILIATION_STATES,
) -> bool:
    """Boolean campaign-scoped conflict check for callers (e.g. commissioned-
    readiness preview) that need a status vocabulary narrower than or
    otherwise different from UNRESOLVED_RECONCILIATION_STATES, while still
    sharing the exact same latest-per-order and identityless fail-closed
    rules as every other function in this module -- never a second,
    independently-derived ordering rule."""
    count = await _count_unresolved_reconciliation_events(
        db=db,
        scope_clause=LiveReconciliationEvent.capital_campaign_id == capital_campaign_id,
        unresolved_statuses=unresolved_statuses,
    )
    return count > 0
