"""Authoritative, restart-safe lifecycle for BUY_LIMIT entry-intelligence
decisions (docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md
Phases 6-9): propose -> Risk-evaluate -> submit -> supervise (poll,
partial-fill, expire, cancel, bounded replace) -> reconcile.

This is a NEW, narrow, BUY-only execution lane, deliberately separate from
the canonical-package / AutonomousExecutionClaim machinery that drives
market-order BUYs (see autonomous_order_preparation.py /
autonomous_execution_claims.py) -- building this on top of that heavier
machinery in one session was judged too large a change to make safely and
was explicitly out of scope for this pass. It reuses the SAME provider
adapter (kraken_spot.py), the SAME Risk Engine, and the SAME reconciliation
primitive (reconcile_live_order_and_fills) as the rest of the system.

Known, explicit gap: a FILLED attempt is reconciled and accounted for via
reconcile_live_order_and_fills (the same function the canonical-package
path uses), but does NOT establish AutonomousPositionCustody -- that
requires an AutonomousExecutionClaim-based lineage this module does not
create. See establish_buy_custody in autonomous_position_custody.py for
what a future integration would need to wire up. This module never calls
it, so no custody is ever falsely established from this lane.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autonomous_limit_entry_attempt import (
    STAGE_CANCEL_REQUESTED,
    STAGE_CANCELLED,
    STAGE_EXPIRED,
    STAGE_FILLED,
    STAGE_OPEN,
    STAGE_PARTIALLY_FILLED,
    STAGE_PROPOSED,
    STAGE_READY,
    STAGE_RECONCILIATION_REQUIRED,
    STAGE_REJECTED,
    STAGE_REPLACED,
    STAGE_SUBMITTED,
    TERMINAL_STAGES,
    AutonomousLimitEntryAttempt,
)
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.entry_intelligence.decision import EntryIntelligenceCandidate
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.registry import get_exchange_provider
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live_crypto_orders import _load_decrypted_credentials
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
    persist_risk_decision,
)

logger = logging.getLogger(__name__)

_POLL_BACKOFF_SECONDS = 20
_MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED = 10


def _idempotency_key(*, campaign_id: UUID, campaign_version: int, instrument: str, decision_record_id: UUID | None) -> str:
    payload = f"{campaign_id}:{campaign_version}:{instrument}:{decision_record_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _resolve_provider_and_credentials(*, db: AsyncSession, provider: str, environment: str):
    connection = await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .where(ExchangeConnection.status == "connected")
        .where(ExchangeConnection.credentials_valid.is_(True))
        .limit(1)
    )
    if connection is None:
        return None, None, None
    client = get_exchange_provider(provider, environment=environment)
    credentials = _load_decrypted_credentials(connection)
    return client, credentials, connection


async def propose_and_risk_evaluate_limit_entry(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    campaign_version: int,
    instrument: str,
    environment: str,
    decision_record_id: UUID | None,
    candidate: EntryIntelligenceCandidate,
    paper_account_id: UUID,
    asset_id: UUID,
    asset_min_order_notional: Decimal | None,
    asset_qty_step_size: Decimal | None,
    asset_supports_fractional: bool,
    risk_context: Any,
    now: datetime,
) -> AutonomousLimitEntryAttempt:
    """Creates (or returns the existing still-active) attempt row for a
    BUY_LIMIT decision, running a REAL Risk Engine evaluation at the
    proposed limit price before persisting stage=READY or REJECTED. This is
    what makes entry intelligence an AUTHORITATIVE pre-execution decision
    stage rather than diagnostic evidence attached after the fact: nothing
    downstream (submission) can happen until Risk has evaluated and
    approved THIS specific attempt.

    Quantity is deliberately re-derived from the SAME approved_notional at
    the (lower) limit price -- deploying the same authorized dollar budget
    at a better price -- rather than reusing the market-price-implied base
    quantity, which would understate notional at any real discount and make
    every BUY_LIMIT fail a min-notional check near the campaign's approved
    floor (a real interaction this session's earlier pass identified)."""
    idempotency_key = _idempotency_key(
        campaign_id=campaign_id, campaign_version=campaign_version,
        instrument=instrument, decision_record_id=decision_record_id,
    )
    existing = await db.scalar(
        select(AutonomousLimitEntryAttempt).where(AutonomousLimitEntryAttempt.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    assert candidate.preferred_limit_price is not None
    assert candidate.maximum_profitable_entry_price is not None
    assert candidate.expiration_time is not None
    requested_base_quantity = candidate.approved_notional / candidate.preferred_limit_price

    attempt = AutonomousLimitEntryAttempt(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        decision_record_id=decision_record_id,
        instrument=instrument,
        provider="kraken_spot",
        environment=environment,
        side="BUY",
        stage=STAGE_PROPOSED,
        preferred_limit_price=candidate.preferred_limit_price,
        maximum_profitable_entry_price=candidate.maximum_profitable_entry_price,
        invalidation_price=candidate.invalidation_price,
        requested_base_quantity=requested_base_quantity,
        approved_notional=candidate.approved_notional,
        expires_at=candidate.expiration_time,
        max_replacement_count=candidate.maximum_replacement_count,
        min_repricing_interval_minutes=candidate.minimum_repricing_interval_minutes,
        evidence_provenance={
            "evidence_provenance": candidate.evidence_provenance,
            "expected_net_edge_at_limit_pct": None if candidate.expected_net_edge_at_limit_pct is None else format(candidate.expected_net_edge_at_limit_pct, "f"),
            "confidence_sample_size": candidate.confidence_sample_size,
            "strategy_identity": candidate.strategy_identity,
        },
        idempotency_key=idempotency_key,
        next_attempt_at=now,
    )
    db.add(attempt)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(AutonomousLimitEntryAttempt).where(AutonomousLimitEntryAttempt.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
        raise

    risk_result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=UUID(int=0),
            paper_account_id=paper_account_id,
            asset_id=asset_id,
            side="buy",
            quantity=requested_base_quantity,
            account_equity=risk_context.account_equity,
            max_position_size_pct=risk_context.max_position_size_pct,
            min_order_notional=asset_min_order_notional,
            campaign_authorized_notional=candidate.approved_notional,
            qty_step_size=asset_qty_step_size,
            supports_fractional=asset_supports_fractional,
            start_of_day_equity=risk_context.start_of_day_equity,
            current_equity=risk_context.current_equity,
            max_daily_loss_pct=risk_context.max_daily_loss_pct,
            high_water_mark_equity=risk_context.high_water_mark_equity,
            max_drawdown_pct=risk_context.max_drawdown_pct,
            consecutive_losses_on_pair=risk_context.consecutive_losses_on_pair,
            cooldown_after_losses=risk_context.cooldown_after_losses,
            last_loss_at=risk_context.last_loss_at,
            cooldown_duration_minutes=risk_context.cooldown_duration_minutes,
            evaluation_time=risk_context.evaluation_time,
            data_is_stale=risk_context.data_is_stale,
            data_has_gaps=risk_context.data_has_gaps,
            global_kill_switch_engaged_state=risk_context.global_kill_switch_engaged_state,
            global_kill_switch_rearm_required=risk_context.global_kill_switch_rearm_required,
            account_kill_switch_engaged_state=risk_context.account_kill_switch_engaged_state,
            account_kill_switch_rearm_required=risk_context.account_kill_switch_rearm_required,
            global_kill_switch_state_observed=risk_context.global_kill_switch_state_observed,
            account_kill_switch_state_observed=risk_context.account_kill_switch_state_observed,
            actor="autonomous_limit_entry_worker",
        ),
        reference_price=candidate.preferred_limit_price,
        context=RiskEvaluationContext(
            global_kill_switch_engaged=bool(risk_context.global_kill_switch_engaged_state),
            has_computable_stop_loss=True,
        ),
    )
    persist_result = await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account_id,
            signal_id=None,
            actor="autonomous_limit_entry_worker",
            evaluation_result=risk_result,
        ),
    )
    attempt.risk_event_id = persist_result.risk_event_id
    if risk_result.action == RiskDecisionAction.REJECT:
        attempt.stage = STAGE_REJECTED
        attempt.terminal_reason = risk_result.reason_code or "risk_rejected"
    else:
        attempt.stage = STAGE_READY
        # Risk may resize; never allow a resize to move quantity such that
        # requested_base_quantity * preferred_limit_price could exceed the
        # originally-approved notional -- resize only ever narrows.
        if risk_result.approved_quantity < attempt.requested_base_quantity:
            attempt.requested_base_quantity = risk_result.approved_quantity
    attempt.next_attempt_at = now
    await db.flush()
    logger.info(
        "limit_entry_attempt_proposed attempt_id=%s campaign_id=%s instrument=%s stage=%s "
        "preferred_limit_price=%s maximum_profitable_entry_price=%s requested_base_quantity=%s "
        "risk_event_id=%s reason=%s",
        attempt.attempt_id, campaign_id, instrument, attempt.stage,
        attempt.preferred_limit_price, attempt.maximum_profitable_entry_price,
        attempt.requested_base_quantity, attempt.risk_event_id, attempt.terminal_reason,
    )
    return attempt


async def _submit_ready_attempt(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, now: datetime) -> None:
    client, credentials, connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None or connection is None:
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "no_connected_exchange_connection"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    client_order_id = f"lea-{attempt.attempt_id}"
    preview = CryptoOrderPreview(
        # Assigned client-side rather than left to the column's server
        # default (gen_random_uuid()) -- this ID is needed immediately
        # below (LiveCryptoOrder.crypto_order_preview_id) within the same
        # in-memory step, before any real round trip to the database would
        # populate a server-generated default.
        crypto_order_preview_id=uuid4(),
        idempotency_key=client_order_id,
        exchange_connection_id=connection.exchange_connection_id,
        provider=attempt.provider,
        environment=attempt.environment,
        product_id=attempt.instrument,
        side="BUY",
        order_type="LIMIT",
        base_size=attempt.requested_base_quantity,
        requested_amount=attempt.approved_notional,
        requested_amount_currency="USD",
        status="SUBMITTED",
        risk_event_id=attempt.risk_event_id,
        decision_record_id=attempt.decision_record_id,
        expires_at=attempt.expires_at,
        generated_by="autonomous_limit_entry_worker",
    )
    db.add(preview)
    await db.flush()

    submission = await client.submit_order(
        credentials=credentials,
        environment=attempt.environment,
        request=ExchangeOrderSubmissionRequest(
            product_id=attempt.instrument,
            side="BUY",
            order_type="LIMIT",
            quote_size=None,
            base_size=attempt.requested_base_quantity,
            client_order_id=client_order_id,
            idempotency_key=client_order_id,
            raw_payload={},
            limit_price=attempt.preferred_limit_price,
            time_in_force="GTC",
        ),
    )

    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(),  # see crypto_order_preview_id comment above
        crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=connection.exchange_connection_id,
        provider=attempt.provider,
        environment=attempt.environment,
        product_id=attempt.instrument,
        side="BUY",
        order_type="LIMIT",
        limit_price=attempt.preferred_limit_price,
        time_in_force="GTC",
        requested_quote_size=attempt.approved_notional,
        client_order_id=client_order_id,
        status="SUBMISSION_PENDING",
        risk_event_id=attempt.risk_event_id,
        decision_record_id=attempt.decision_record_id,
        audit_correlation_id=attempt.attempt_id,
        safe_provider_response={},
    )

    if submission.classification == "rejected":
        live_order.status = "REJECTED"
        live_order.failure_code = submission.rejection.code if submission.rejection else "unknown"
        live_order.failure_reason = submission.rejection.message if submission.rejection else None
        db.add(live_order)
        await db.flush()
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = f"submission_rejected:{live_order.failure_code}"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    if submission.classification == "ambiguous" and (submission.order is None or submission.order.provider_order_id is None):
        live_order.status = "RECONCILIATION_REQUIRED"
        db.add(live_order)
        await db.flush()
        attempt.live_crypto_order_id = live_order.live_crypto_order_id
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "ambiguous_submission_missing_provider_order_id"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    # success (or ambiguous-with-an-id, which still needs re-verification --
    # treat it the same as success and let the very next poll confirm real
    # provider state, never assuming success from an ambiguous response).
    order = submission.order
    live_order.provider_order_id = order.provider_order_id if order else None
    live_order.provider_status = order.status if order else "UNKNOWN"
    live_order.status = "ACKNOWLEDGED" if (order and order.provider_order_id) else "RECONCILIATION_REQUIRED"
    db.add(live_order)
    await db.flush()
    attempt.live_crypto_order_id = live_order.live_crypto_order_id
    attempt.stage = STAGE_SUBMITTED
    attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
    logger.info(
        "limit_entry_submitted attempt_id=%s live_crypto_order_id=%s provider_order_id=%s "
        "limit_price=%s base_quantity=%s classification=%s",
        attempt.attempt_id, live_order.live_crypto_order_id, live_order.provider_order_id,
        attempt.preferred_limit_price, attempt.requested_base_quantity, submission.classification,
    )


async def _poll_open_attempt(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, live_order: LiveCryptoOrder, now: datetime) -> None:
    client, credentials, _connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None:
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    order = await client.lookup_order(
        credentials=credentials,
        environment=attempt.environment,
        provider_order_id=live_order.provider_order_id,
        client_order_id=live_order.client_order_id,
        product_id=attempt.instrument,
    )
    if order is None:
        attempt.retry_count += 1
        if attempt.retry_count >= _MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "lookup_order_returned_nothing_repeatedly"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    live_order.provider_status = order.status
    fills = await client.list_fills(
        credentials=credentials, environment=attempt.environment,
        provider_order_id=live_order.provider_order_id,
    )
    filled_quantity = sum((fill.size for fill in fills), Decimal("0"))
    attempt.filled_base_quantity = min(filled_quantity, attempt.requested_base_quantity)

    if order.status == "UNKNOWN":
        # Fail closed: never guess. An unrecognized provider state requires
        # operator attention, not a silent assumption of OPEN/FILLED.
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "unknown_provider_state"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    if order.status == "FILLED" or attempt.filled_base_quantity >= attempt.requested_base_quantity:
        await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
        attempt.stage = STAGE_FILLED
        attempt.next_attempt_at = now
        logger.info(
            "limit_entry_filled attempt_id=%s live_crypto_order_id=%s filled_base_quantity=%s",
            attempt.attempt_id, live_order.live_crypto_order_id, attempt.filled_base_quantity,
        )
        return

    if order.status == "CANCELLED":
        # Provider-side cancellation we didn't request (e.g. an operator
        # cancelled it directly on the exchange) -- treat as terminal.
        attempt.stage = STAGE_CANCELLED
        attempt.cancel_confirmed_at = now
        attempt.terminal_reason = "cancelled_by_provider_outside_supervisor"
        attempt.next_attempt_at = now
        return

    if attempt.filled_base_quantity > Decimal("0"):
        attempt.stage = STAGE_PARTIALLY_FILLED
        await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
    else:
        attempt.stage = STAGE_OPEN

    # Expiration and invalidation checks (Phase 9): request cancellation
    # rather than transitioning straight to CANCELLED -- cancellation must
    # be provider-confirmed before this attempt is treated as inactive.
    expired = now >= attempt.expires_at
    invalidated = (
        attempt.invalidation_price is not None
        and order.status in {"OPEN", "PENDING"}
    )
    if expired or invalidated:
        attempt.stage = STAGE_CANCEL_REQUESTED
        attempt.cancel_requested_at = now
        attempt.terminal_reason = "expired" if expired else "invalidation_price_crossed"
        attempt.next_attempt_at = now
        return

    attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)


async def _confirm_cancellation(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, live_order: LiveCryptoOrder, now: datetime) -> None:
    client, credentials, _connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None:
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    cancel_result = await client.cancel_order(
        credentials=credentials, environment=attempt.environment,
        provider_order_id=live_order.provider_order_id,
        client_order_id=live_order.client_order_id,
    )
    if cancel_result.classification == "ambiguous":
        attempt.retry_count += 1
        if attempt.retry_count >= _MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "cancellation_ambiguous_repeatedly"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    # "success" or "already_resolved" both require re-verification via
    # lookup_order before this attempt is trusted as inactive -- a
    # cancel-request response is never itself treated as confirmation.
    order = await client.lookup_order(
        credentials=credentials, environment=attempt.environment,
        provider_order_id=live_order.provider_order_id,
        client_order_id=live_order.client_order_id,
        product_id=attempt.instrument,
    )
    if order is not None and order.status == "FILLED":
        # Race: filled before the cancel took effect. Fill wins.
        await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
        attempt.stage = STAGE_FILLED
        attempt.next_attempt_at = now
        return
    if order is not None and order.status not in {"CANCELLED", "UNKNOWN"}:
        # Still resting -- cancel not yet effective. Retry.
        attempt.retry_count += 1
        if attempt.retry_count >= _MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "cancellation_never_confirmed"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        return

    live_order.status = "CANCELLED"
    live_order.cancelled_at = now
    attempt.stage = STAGE_CANCELLED
    attempt.cancel_confirmed_at = now
    attempt.next_attempt_at = now
    logger.info("limit_entry_cancellation_confirmed attempt_id=%s live_crypto_order_id=%s", attempt.attempt_id, live_order.live_crypto_order_id)


async def _maybe_replace(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, now: datetime, current_reference_price: Decimal | None) -> None:
    """Phase 9 bounded replacement: only when economics still supports it
    (current_reference_price, if known, is still at or below this attempt's
    maximum_profitable_entry_price -- itself never re-derived upward, so a
    replacement can never chase above the original economic bound),
    replacement_count is below max, and the repricing interval has elapsed.
    Marks this attempt REPLACED and creates exactly one new PROPOSED row."""
    attempt.stage = STAGE_REPLACED
    if (
        current_reference_price is None
        or current_reference_price > attempt.maximum_profitable_entry_price
        or attempt.replacement_count >= attempt.max_replacement_count
    ):
        attempt.next_attempt_at = now
        return

    new_idempotency_key = f"{attempt.idempotency_key}:replace{attempt.replacement_count + 1}"
    replacement = AutonomousLimitEntryAttempt(
        campaign_id=attempt.campaign_id,
        campaign_version=attempt.campaign_version,
        decision_record_id=attempt.decision_record_id,
        instrument=attempt.instrument,
        provider=attempt.provider,
        environment=attempt.environment,
        side="BUY",
        stage=STAGE_PROPOSED,
        preferred_limit_price=min(current_reference_price, attempt.maximum_profitable_entry_price),
        maximum_profitable_entry_price=attempt.maximum_profitable_entry_price,
        invalidation_price=attempt.invalidation_price,
        requested_base_quantity=attempt.requested_base_quantity,
        approved_notional=attempt.approved_notional,
        expires_at=attempt.expires_at,
        replaces_attempt_id=attempt.attempt_id,
        replacement_count=attempt.replacement_count + 1,
        max_replacement_count=attempt.max_replacement_count,
        min_repricing_interval_minutes=attempt.min_repricing_interval_minutes,
        evidence_provenance=attempt.evidence_provenance,
        idempotency_key=new_idempotency_key,
        next_attempt_at=now,
    )
    db.add(replacement)
    attempt.next_attempt_at = now


async def advance_one_limit_entry_attempt(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, now: datetime, current_reference_price: Decimal | None = None) -> None:
    """Advances exactly one attempt by exactly one stage-step. Restart-safe:
    every branch either persists a new terminal/next stage before returning
    or leaves the row unchanged with a bumped next_attempt_at for retry --
    never partial, in-memory-only progress that a crash could lose."""
    if attempt.stage == STAGE_READY:
        await _submit_ready_attempt(db=db, attempt=attempt, now=now)
        return

    if attempt.stage in {STAGE_SUBMITTED, STAGE_OPEN, STAGE_PARTIALLY_FILLED}:
        if attempt.live_crypto_order_id is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "missing_live_crypto_order_reference"
            return
        live_order = await db.get(LiveCryptoOrder, attempt.live_crypto_order_id)
        if live_order is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "live_crypto_order_not_found"
            return
        await _poll_open_attempt(db=db, attempt=attempt, live_order=live_order, now=now)
        return

    if attempt.stage == STAGE_CANCEL_REQUESTED:
        if attempt.live_crypto_order_id is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "missing_live_crypto_order_reference"
            return
        live_order = await db.get(LiveCryptoOrder, attempt.live_crypto_order_id)
        if live_order is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "live_crypto_order_not_found"
            return
        await _confirm_cancellation(db=db, attempt=attempt, live_order=live_order, now=now)
        return

    if attempt.stage == STAGE_CANCELLED:
        await _maybe_replace(db=db, attempt=attempt, now=now, current_reference_price=current_reference_price)
        return

    # PROPOSED, REJECTED, FILLED, EXPIRED, REPLACED, RECONCILIATION_REQUIRED:
    # nothing further for this worker to do (PROPOSED is advanced
    # synchronously by propose_and_risk_evaluate_limit_entry, never left for
    # this function to pick up).


async def advance_due_limit_entry_attempts(*, db: AsyncSession, now: datetime, current_reference_prices: dict[str, Decimal] | None = None) -> list[AutonomousLimitEntryAttempt]:
    """Per-orchestration-cycle supervisor tick: advances every attempt whose
    next_attempt_at has elapsed, one stage-step each, using SKIP LOCKED so a
    concurrent worker (or a restart mid-cycle) never double-processes the
    same row. Returns the attempts that were advanced this call."""
    reference_prices = current_reference_prices or {}
    rows = list(
        (
            await db.execute(
                select(AutonomousLimitEntryAttempt)
                .where(AutonomousLimitEntryAttempt.next_attempt_at <= now)
                .where(AutonomousLimitEntryAttempt.stage.notin_(tuple(TERMINAL_STAGES)))
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for attempt in rows:
        await advance_one_limit_entry_attempt(
            db=db, attempt=attempt, now=now,
            current_reference_price=reference_prices.get(attempt.instrument),
        )
        await db.flush()
    return rows
