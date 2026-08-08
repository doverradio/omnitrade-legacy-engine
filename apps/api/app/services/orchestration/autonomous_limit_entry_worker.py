"""Authoritative, restart-safe lifecycle for BUY_LIMIT entry-intelligence
decisions (docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md
Phases 6-9): propose -> Risk-evaluate -> claim -> submit -> supervise (poll,
partial-fill, expire, cancel, bounded replace) -> reconcile -> custody.

This is a narrow, BUY-only execution lane for the ORDER SUBMISSION/
SUPERVISION mechanics themselves, but it reuses the canonical
CanonicalPreviewPackage / CanonicalProvingActivation /
AutonomousExecutionClaim / AutonomousPositionCustody machinery UNCHANGED
for authority, claim uniqueness/locking, and custody establishment (see
_establish_claim_lineage and _resolve_claim_scope_and_custody) --
`create_canonical_preview_package` gained one new, narrow
`commissioning_entry_mode` ("autonomous_limit_entry") for this purpose;
nothing about claim_activated_package, establish_buy_custody, or
release_execution_claim_scope_if_order_resolved was modified. It also
reuses the SAME provider adapter (kraken_spot.py), the SAME Risk Engine,
and the SAME reconciliation primitive (reconcile_live_order_and_fills) as
the rest of the system.

Known, explicit, deliberate limitation: a provider-confirmed cancellation
where SOME quantity filled beforehand (partial-fill-then-cancel) does NOT
establish custody -- establish_buy_custody requires a genuinely "filled"
(never "partially_filled") reconciliation event, by design, shared with
the market-BUY path; weakening that exact-match invariant to accommodate
this lane was judged unsafe. That scenario surfaces as
RECONCILIATION_REQUIRED (see _confirm_cancellation), preserving the exact
reconciled fill quantity for manual review, rather than fabricating a
custody-establishment path the shared authority function was never
designed to support.
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

from app.config import get_settings
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
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
from app.models.live_trading_profile import LiveTradingProfile
from app.services.entry_intelligence.decision import EntryIntelligenceCandidate
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.registry import get_exchange_provider
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live_crypto_orders import _load_decrypted_credentials
from app.services.orchestration.process_trace import (
    STAGE_AUTHORIZE_TRADE,
    STAGE_CONSTRUCT_TRADE,
    STAGE_EXECUTE,
    STAGE_EXIT,
    STAGE_MONITOR,
    append_trace_event,
    build_process_trace_event,
)
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


def _trace(
    *,
    attempt: AutonomousLimitEntryAttempt,
    now: datetime,
    process_stage: str,
    gate: str,
    verdict: str,
    reason: str | None,
    next_step: str | None = None,
    observed_value: Decimal | str | None = None,
    threshold: Decimal | str | None = None,
) -> None:
    """Appends one observational PROCESS trace event to `attempt`, reusing
    the attempt's own identity (attempt_id, decision_record_id, instrument)
    -- never mints a new correlation id. Purely additive: never reads back
    from the trace, never affects `attempt.stage` or any other field this
    worker's real state machine relies on. See process_trace.py for why this
    can never raise or otherwise influence control flow."""
    attempt.evidence_provenance = append_trace_event(
        attempt.evidence_provenance,
        build_process_trace_event(
            process_stage=process_stage,
            gate=gate,
            verdict=verdict,
            reason=reason,
            now=now,
            instrument=attempt.instrument,
            decision_record_id=attempt.decision_record_id,
            attempt_id=attempt.attempt_id,
            observed_value=observed_value,
            threshold=threshold,
            next_step=next_step,
        ),
    )


async def _load_live_trading_profile_for_paper_account(*, db: AsyncSession, paper_account_id: UUID) -> LiveTradingProfile | None:
    return await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
        .limit(1)
    )


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
        paper_account_id=paper_account_id,
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
    _trace(
        attempt=attempt, now=now, process_stage=STAGE_CONSTRUCT_TRADE,
        gate="limit_entry_attempt_construction", verdict="PASS", reason="attempt_created",
        observed_value=attempt.preferred_limit_price, threshold=attempt.maximum_profitable_entry_price,
        next_step="limit_entry_risk_engine_gate",
    )
    _trace(
        attempt=attempt, now=now, process_stage=STAGE_AUTHORIZE_TRADE,
        gate="limit_entry_risk_engine_gate", verdict=risk_result.action.value.upper(),
        reason=attempt.terminal_reason if risk_result.action == RiskDecisionAction.REJECT else None,
        observed_value=requested_base_quantity, threshold=risk_result.approved_quantity,
        next_step="terminal" if risk_result.action == RiskDecisionAction.REJECT else "execute",
    )
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


async def _establish_claim_lineage(
    *, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, connection: ExchangeConnection, now: datetime,
) -> str | None:
    """Idempotently establishes the SAME canonical
    package -> mandate authorization -> dry run -> activation -> claim
    lineage a market BUY goes through, using the EXACT SAME, unmodified
    authority functions (create_canonical_preview_package,
    authorize_canonical_preview_package_under_mandate,
    run_dry_run_for_canonical_preview_package,
    activate_canonical_proving_campaign, claim_activated_package). This is
    what lets a FILLED BUY_LIMIT reach establish_buy_custody afterward --
    that function (and claim_activated_package before it) both hard-require
    a real CanonicalPreviewPackage/CanonicalProvingActivation; there is no
    shortcut around them, by design (see the ck_aec_reduce_only_custody_claim
    /buy_package_id-etc NOT NULL constraints on the shared models).

    Returns None on success, or a short reason string if lineage could not
    be established this call (caller decides how to reschedule/fail
    closed -- this function itself never marks the attempt
    RECONCILIATION_REQUIRED, since a transient mandate/profile issue should
    simply retry, not escalate immediately).

    Restart-safe: if attempt.claim_id is already set, does nothing (the
    prior lineage-establishment call already completed); each individual
    step below is itself idempotent (idempotency_key/unique-constraint
    based), so a crash between two steps simply re-runs the completed ones
    as no-ops on the next call.
    """
    # Imported here, not at module level: canonical_preview_package.py
    # transitively imports capital_campaign_orchestration.authoritative,
    # which imports THIS module (for propose_and_risk_evaluate_limit_entry)
    # -- a module-level import here would be a circular import. Both
    # modules are already fully loaded by the time any attempt reaches
    # submission, so a call-time import is safe and cheap (Python caches it).
    from app.services.canonical_preview_package import (
        _AUTONOMOUS_LIMIT_ENTRY_MODE,
        _resolve_strategy_and_parameter_binding,
        CanonicalPreviewPackageActivationRequest,
        CanonicalPreviewPackageCreateRequest,
        CanonicalPreviewPackageDryRunRequest,
        CanonicalPreviewPackageMandateAuthorizeRequest,
        activate_canonical_proving_campaign,
        authorize_canonical_preview_package_under_mandate,
        create_canonical_preview_package,
        run_dry_run_for_canonical_preview_package,
    )
    from app.services.orchestration.autonomous_execution_claims import claim_activated_package

    if attempt.claim_id is not None:
        return None

    if attempt.paper_account_id is None:
        return "missing_paper_account_id"
    profile = await _load_live_trading_profile_for_paper_account(db=db, paper_account_id=attempt.paper_account_id)
    if profile is None:
        return "live_trading_profile_missing"

    strategy_identity = str((attempt.evidence_provenance or {}).get("strategy_identity") or "").strip()
    if not strategy_identity:
        return "strategy_identity_missing"
    strategy, parameter_set = await _resolve_strategy_and_parameter_binding(db=db, strategy_identity=strategy_identity)
    if strategy is None or parameter_set is None:
        return "strategy_or_parameter_set_unresolved"

    if attempt.package_id is None:
        preview_client_order_id = f"lea-preview-{attempt.attempt_id}"
        preview = CryptoOrderPreview(
            crypto_order_preview_id=uuid4(),
            idempotency_key=preview_client_order_id,
            exchange_connection_id=connection.exchange_connection_id,
            provider=attempt.provider,
            environment=attempt.environment,
            product_id=attempt.instrument,
            side="BUY",
            order_type="LIMIT",
            base_size=attempt.requested_base_quantity,
            requested_amount=attempt.approved_notional,
            requested_amount_currency="USD",
            status="PREVIEW_READY",
            risk_event_id=attempt.risk_event_id,
            decision_record_id=attempt.decision_record_id,
            strategy_id=strategy.id,
            parameter_set_id=parameter_set.id,
            expires_at=attempt.expires_at,
            generated_by="system_recommendation",
        )
        db.add(preview)
        await db.flush()

        package_response = await create_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageCreateRequest(
                campaign_id=attempt.campaign_id,
                campaign_version=attempt.campaign_version,
                paper_account_id=attempt.paper_account_id,
                live_trading_profile_id=profile.id,
                provider=attempt.provider,
                environment=attempt.environment,
                product=attempt.instrument,
                max_proposed_order_amount=Decimal("5"),
                actor="autonomous_limit_entry_worker",
                idempotency_key=f"lea-package:{attempt.attempt_id}",
                commissioning_entry_mode=_AUTONOMOUS_LIMIT_ENTRY_MODE,
                expected_decision_record_id=attempt.decision_record_id,
                forced_action="OPEN_POSITION_PROPOSED",
            ),
        )
        package_payload = package_response["package"]
        if package_payload is None:
            return str(package_response.get("reason_code") or "canonical_package_not_created")
        attempt.package_id = UUID(str(package_payload["package_id"]))
        await db.flush()

    mandate_response = await authorize_canonical_preview_package_under_mandate(
        db=db,
        request=CanonicalPreviewPackageMandateAuthorizeRequest(
            package_id=attempt.package_id,
            idempotency_key=f"lea-authorize:{attempt.attempt_id}",
        ),
    )
    package_state = str(mandate_response["package"]["package_state"])

    if package_state == "AUTHORIZED":
        dry_run_response = await run_dry_run_for_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageDryRunRequest(
                package_id=attempt.package_id,
                approval_event_id=None,
                operator_identity=None,
                idempotency_token=f"lea-dryrun:{attempt.attempt_id}",
            ),
        )
        package_state = str(dry_run_response["package"]["package_state"])

    dry_run_order_id_raw = None
    if package_state in {"DRY_RUN_PASSED", "ACTIVATED"}:
        package_row = await _load_package_by_id(db=db, package_id=attempt.package_id)
        if package_row is None:
            return "canonical_package_missing_after_dry_run"
        dry_run_order_id_raw = package_row.dry_run_live_crypto_order_id
        if package_state == "DRY_RUN_PASSED":
            activation_response = await activate_canonical_proving_campaign(
                db=db,
                request=CanonicalPreviewPackageActivationRequest(
                    package_id=attempt.package_id,
                    approval_event_id=None,
                    dry_run_live_crypto_order_id=dry_run_order_id_raw,
                    actor=None,
                    expires_at=None,
                    idempotency_key=f"lea-activate:{attempt.attempt_id}",
                ),
            )
            attempt.activation_id = UUID(str(activation_response["activation"]["activation_id"]))
            await db.flush()

    package_row = await _load_package_by_id(db=db, package_id=attempt.package_id)
    if package_row is None or package_row.package_state != "ACTIVATED":
        return f"canonical_package_not_yet_activated:{package_row.package_state if package_row else 'missing'}"
    if attempt.activation_id is None:
        activation_row = await db.scalar(
            select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == attempt.package_id).limit(1)
        )
        if activation_row is None:
            return "canonical_activation_missing"
        attempt.activation_id = activation_row.activation_id
        await db.flush()

    claim_outcome = await claim_activated_package(
        db=db, package_id=attempt.package_id, claim_owner="autonomous_limit_entry_worker", now=now,
    )
    if claim_outcome.claim is None:
        return claim_outcome.reason_code
    attempt.claim_id = claim_outcome.claim.claim_id
    await db.flush()
    logger.info(
        "limit_entry_claim_lineage_established attempt_id=%s package_id=%s activation_id=%s claim_id=%s",
        attempt.attempt_id, attempt.package_id, attempt.activation_id, attempt.claim_id,
    )
    return None


async def _load_package_by_id(*, db: AsyncSession, package_id: UUID) -> CanonicalPreviewPackage | None:
    return await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).limit(1))


async def _submit_ready_attempt(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, now: datetime) -> None:
    settings = get_settings()
    if not settings.autonomous_limit_entry_submission_enabled:
        # Diagnostics, shadow evaluation, and Risk approval all already
        # happened (propose_and_risk_evaluate_limit_entry runs regardless of
        # this flag) -- only the actual provider-facing submission is
        # gated. Fail closed by simply not submitting, never by escalating
        # to RECONCILIATION_REQUIRED (this is an expected, deliberate,
        # operator-controlled state, not an error).
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        logger.info(
            "limit_entry_submission_disabled attempt_id=%s campaign_id=%s instrument=%s stage=%s",
            attempt.attempt_id, attempt.campaign_id, attempt.instrument, attempt.stage,
        )
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="submission_enabled_gate",
            verdict="WAIT", reason="autonomous_limit_entry_submission_enabled=false", next_step="retry",
        )
        return

    client, credentials, connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None or connection is None:
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "no_connected_exchange_connection"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="exchange_connection_gate",
            verdict="BLOCKED", reason="no_connected_exchange_connection", next_step="terminal",
        )
        return

    if attempt.live_crypto_order_id is not None:
        # Restart between a successful submission and the stage=SUBMITTED
        # write: the order already exists, never submit a second one.
        attempt.stage = STAGE_SUBMITTED
        attempt.next_attempt_at = now
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="submission_idempotency_gate",
            verdict="PASS", reason="restart_safe_resume_already_submitted", next_step="monitor",
        )
        return

    lineage_blocker = await _establish_claim_lineage(db=db, attempt=attempt, connection=connection, now=now)
    if lineage_blocker is not None:
        attempt.retry_count += 1
        if attempt.retry_count >= _MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = f"claim_lineage_failed:{lineage_blocker}"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        logger.info(
            "limit_entry_claim_lineage_pending attempt_id=%s blocker=%s retry_count=%s",
            attempt.attempt_id, lineage_blocker, attempt.retry_count,
        )
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="claim_lineage_gate",
            verdict="BLOCKED" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "WAIT",
            reason=lineage_blocker, observed_value=attempt.retry_count,
            threshold=_MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED,
            next_step="terminal" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "retry",
        )
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

    async def _link_claim_to_order(*, claim_status: str) -> None:
        # The claim was created (or reused) by _establish_claim_lineage
        # above -- link it to the REAL submitted order and advance its
        # status through the same vocabulary the market-BUY path uses at
        # this exact point (EXECUTION_STARTED just before the provider
        # call, SUBMISSION_PENDING just after), so
        # release_execution_claim_scope_if_order_resolved (called later,
        # unchanged) can find and resolve it purely from
        # claim.live_order_id.
        if attempt.claim_id is None:
            return
        claim = await db.get(AutonomousExecutionClaim, attempt.claim_id)
        if claim is None:
            return
        claim.live_order_id = live_order.live_crypto_order_id
        claim.claim_status = claim_status
        await db.flush()

    if submission.classification == "rejected":
        live_order.status = "REJECTED"
        live_order.failure_code = submission.rejection.code if submission.rejection else "unknown"
        live_order.failure_reason = submission.rejection.message if submission.rejection else None
        db.add(live_order)
        await db.flush()
        attempt.live_crypto_order_id = live_order.live_crypto_order_id
        await _link_claim_to_order(claim_status="RECONCILIATION_REQUIRED")
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = f"submission_rejected:{live_order.failure_code}"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="provider_submission_gate",
            verdict="REJECT", reason=attempt.terminal_reason, next_step="terminal",
        )
        return

    if submission.classification == "ambiguous" and (submission.order is None or submission.order.provider_order_id is None):
        live_order.status = "RECONCILIATION_REQUIRED"
        db.add(live_order)
        await db.flush()
        attempt.live_crypto_order_id = live_order.live_crypto_order_id
        await _link_claim_to_order(claim_status="RECONCILIATION_REQUIRED")
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "ambiguous_submission_missing_provider_order_id"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="provider_submission_gate",
            verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
        )
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
    await _link_claim_to_order(claim_status="SUBMISSION_PENDING")
    attempt.stage = STAGE_SUBMITTED
    attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
    logger.info(
        "limit_entry_submitted attempt_id=%s live_crypto_order_id=%s provider_order_id=%s "
        "limit_price=%s base_quantity=%s classification=%s",
        attempt.attempt_id, live_order.live_crypto_order_id, live_order.provider_order_id,
        attempt.preferred_limit_price, attempt.requested_base_quantity, submission.classification,
    )
    _trace(
        attempt=attempt, now=now, process_stage=STAGE_EXECUTE, gate="provider_submission_gate",
        verdict="PASS", reason=submission.classification, next_step="monitor",
    )


async def _resolve_claim_scope_and_custody(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, live_order: LiveCryptoOrder, now: datetime) -> None:
    """Called AFTER reconcile_live_order_and_fills has just set live_order.status
    to an authoritative terminal outcome. Calls the EXACT SAME, unmodified
    release_execution_claim_scope_if_order_resolved the market-BUY path
    uses -- this is the single function that (for a genuinely, fully FILLED
    BUY, under an ordinary-production, non-Controlled-Proof mandate)
    invokes establish_buy_custody. Never establishes custody itself; only
    observes the result afterward (attempt.custody_id) for reporting."""
    from app.services.orchestration.autonomous_execution_claims import release_execution_claim_scope_if_order_resolved

    await release_execution_claim_scope_if_order_resolved(
        db=db, live_crypto_order_id=live_order.live_crypto_order_id, order_status=live_order.status, now=now,
    )
    if attempt.claim_id is not None:
        custody = await db.scalar(
            select(AutonomousPositionCustody).where(AutonomousPositionCustody.buy_claim_id == attempt.claim_id).limit(1)
        )
        if custody is not None:
            attempt.custody_id = custody.custody_id
            logger.info(
                "limit_entry_custody_established attempt_id=%s claim_id=%s custody_id=%s custody_state=%s",
                attempt.attempt_id, attempt.claim_id, custody.custody_id, custody.custody_state,
            )


async def _poll_open_attempt(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, live_order: LiveCryptoOrder, now: datetime) -> None:
    client, credentials, _connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None:
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="exchange_connection_gate",
            verdict="WAIT", reason="provider_client_or_credentials_unavailable", next_step="retry",
        )
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
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="order_lookup_gate",
            verdict="BLOCKED" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "WAIT",
            reason="lookup_order_returned_nothing", observed_value=attempt.retry_count,
            threshold=_MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED,
            next_step="terminal" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "retry",
        )
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
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="provider_order_status_gate",
            verdict="BLOCKED", reason="unknown_provider_state", next_step="terminal",
        )
        return

    if order.status == "FILLED" or attempt.filled_base_quantity >= attempt.requested_base_quantity:
        await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
        await _resolve_claim_scope_and_custody(db=db, attempt=attempt, live_order=live_order, now=now)
        attempt.stage = STAGE_FILLED
        attempt.next_attempt_at = now
        logger.info(
            "limit_entry_filled attempt_id=%s live_crypto_order_id=%s filled_base_quantity=%s custody_id=%s",
            attempt.attempt_id, live_order.live_crypto_order_id, attempt.filled_base_quantity, attempt.custody_id,
        )
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="fill_gate",
            verdict="PASS", reason="filled", observed_value=attempt.filled_base_quantity,
            threshold=attempt.requested_base_quantity, next_step="terminal",
        )
        return

    if order.status == "CANCELLED":
        # Provider-side cancellation we didn't request (e.g. an operator
        # cancelled it directly on the exchange). Reconcile first to get
        # the AUTHORITATIVE post-cancellation status -- reconcile_live_
        # order_and_fills sets live_order.status to PARTIALLY_FILLED
        # (never CANCELLED) when some quantity filled before the provider
        # cancelled the remainder, and release_execution_claim_scope_if_
        # order_resolved has no resolution mapping for PARTIALLY_FILLED,
        # so a partial fill here can never silently become custody nor
        # silently vanish into a released CANCELLED claim -- it surfaces
        # as RECONCILIATION_REQUIRED for manual review instead. See
        # _confirm_cancellation for the (identical) supervisor-requested
        # cancellation path.
        await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
        if live_order.status == "PARTIALLY_FILLED":
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "cancelled_by_provider_with_unresolved_partial_fill"
            attempt.next_attempt_at = now
            _trace(
                attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="provider_cancellation_gate",
                verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
            )
            return
        await _resolve_claim_scope_and_custody(db=db, attempt=attempt, live_order=live_order, now=now)
        attempt.stage = STAGE_CANCELLED
        attempt.cancel_confirmed_at = now
        attempt.terminal_reason = "cancelled_by_provider_outside_supervisor"
        attempt.next_attempt_at = now
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="provider_cancellation_gate",
            verdict="REJECT", reason=attempt.terminal_reason, next_step="terminal",
        )
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
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="expiration_invalidation_gate",
            verdict="EXIT_REQUESTED", reason=attempt.terminal_reason, next_step="exit",
        )
        return

    _trace(
        attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="expiration_invalidation_gate",
        verdict="PASS", reason=f"resting_stage={attempt.stage}", next_step="retry",
    )
    attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)


async def _confirm_cancellation(*, db: AsyncSession, attempt: AutonomousLimitEntryAttempt, live_order: LiveCryptoOrder, now: datetime) -> None:
    client, credentials, _connection = await _resolve_provider_and_credentials(
        db=db, provider=attempt.provider, environment=attempt.environment,
    )
    if client is None or credentials is None:
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="exchange_connection_gate",
            verdict="WAIT", reason="provider_client_or_credentials_unavailable", next_step="retry",
        )
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
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="cancel_request_gate",
            verdict="BLOCKED" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "WAIT",
            reason="cancellation_ambiguous", observed_value=attempt.retry_count,
            threshold=_MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED,
            next_step="terminal" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "retry",
        )
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
        await _resolve_claim_scope_and_custody(db=db, attempt=attempt, live_order=live_order, now=now)
        attempt.stage = STAGE_FILLED
        attempt.next_attempt_at = now
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="cancel_confirmation_gate",
            verdict="PASS", reason="filled_before_cancel_took_effect", next_step="terminal",
        )
        return
    if order is not None and order.status not in {"CANCELLED", "UNKNOWN"}:
        # Still resting -- cancel not yet effective. Retry.
        attempt.retry_count += 1
        if attempt.retry_count >= _MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "cancellation_never_confirmed"
        attempt.next_attempt_at = now + timedelta(seconds=_POLL_BACKOFF_SECONDS)
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="cancel_confirmation_gate",
            verdict="BLOCKED" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "WAIT",
            reason="still_resting_cancel_not_yet_effective", observed_value=attempt.retry_count,
            threshold=_MAX_RETRY_COUNT_BEFORE_RECONCILIATION_REQUIRED,
            next_step="terminal" if attempt.stage == STAGE_RECONCILIATION_REQUIRED else "retry",
        )
        return

    # Confirmed cancelled at the provider. Reconcile to get the
    # AUTHORITATIVE post-cancellation status rather than setting it by
    # hand -- reconcile_live_order_and_fills sets PARTIALLY_FILLED (never
    # CANCELLED) when some quantity filled before the remainder was
    # cancelled, and release_execution_claim_scope_if_order_resolved has
    # no resolution mapping for that status, so a partial fill here can
    # never silently become custody nor silently vanish into a released
    # CANCELLED claim -- it surfaces as RECONCILIATION_REQUIRED instead,
    # preserving the exact reconciled fill quantity for manual review.
    await reconcile_live_order_and_fills(db=db, live_crypto_order_id=live_order.live_crypto_order_id, operator_identity="autonomous_limit_entry_worker")
    if live_order.status == "PARTIALLY_FILLED":
        attempt.stage = STAGE_RECONCILIATION_REQUIRED
        attempt.terminal_reason = "cancelled_with_unresolved_partial_fill_requires_manual_reconciliation"
        attempt.next_attempt_at = now
        logger.info(
            "limit_entry_cancellation_confirmed_with_partial_fill attempt_id=%s live_crypto_order_id=%s filled_base_quantity=%s",
            attempt.attempt_id, live_order.live_crypto_order_id, attempt.filled_base_quantity,
        )
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="cancel_confirmation_gate",
            verdict="BLOCKED", reason=attempt.terminal_reason, observed_value=attempt.filled_base_quantity,
            next_step="terminal",
        )
        return

    await _resolve_claim_scope_and_custody(db=db, attempt=attempt, live_order=live_order, now=now)
    attempt.stage = STAGE_CANCELLED
    attempt.cancel_confirmed_at = now
    attempt.next_attempt_at = now
    logger.info("limit_entry_cancellation_confirmed attempt_id=%s live_crypto_order_id=%s", attempt.attempt_id, live_order.live_crypto_order_id)
    _trace(
        attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="cancel_confirmation_gate",
        verdict="PASS", reason="cancellation_confirmed", next_step="terminal",
    )


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
        if current_reference_price is None:
            replacement_blocked_reason = "current_reference_price_unavailable"
        elif current_reference_price > attempt.maximum_profitable_entry_price:
            replacement_blocked_reason = "reference_price_above_maximum_profitable_entry_price"
        else:
            replacement_blocked_reason = "replacement_count_exhausted"
        _trace(
            attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="bounded_replacement_gate",
            verdict="TERMINAL", reason=replacement_blocked_reason,
            observed_value=current_reference_price, threshold=attempt.maximum_profitable_entry_price,
            next_step="terminal",
        )
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
    _trace(
        attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="bounded_replacement_gate",
        verdict="REPLACED", reason="replacement_attempt_created",
        observed_value=current_reference_price, threshold=attempt.maximum_profitable_entry_price,
        next_step="construct_trade",
    )


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
            _trace(
                attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="live_order_reference_gate",
                verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
            )
            return
        live_order = await db.get(LiveCryptoOrder, attempt.live_crypto_order_id)
        if live_order is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "live_crypto_order_not_found"
            _trace(
                attempt=attempt, now=now, process_stage=STAGE_MONITOR, gate="live_order_reference_gate",
                verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
            )
            return
        await _poll_open_attempt(db=db, attempt=attempt, live_order=live_order, now=now)
        return

    if attempt.stage == STAGE_CANCEL_REQUESTED:
        if attempt.live_crypto_order_id is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "missing_live_crypto_order_reference"
            _trace(
                attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="live_order_reference_gate",
                verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
            )
            return
        live_order = await db.get(LiveCryptoOrder, attempt.live_crypto_order_id)
        if live_order is None:
            attempt.stage = STAGE_RECONCILIATION_REQUIRED
            attempt.terminal_reason = "live_crypto_order_not_found"
            _trace(
                attempt=attempt, now=now, process_stage=STAGE_EXIT, gate="live_order_reference_gate",
                verdict="BLOCKED", reason=attempt.terminal_reason, next_step="terminal",
            )
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


async def _load_fresh_reference_price(*, db: AsyncSession, provider: str, environment: str, instrument: str) -> Decimal | None:
    """Real, authoritative, live Kraken price evidence for replacement
    evaluation -- NEVER candle/historical data, a placeholder, or the
    previous limit price. Fails closed (returns None, meaning "no
    replacement this cycle") on any staleness, mismatch, or provider
    failure rather than ever falling back to something less authoritative;
    _maybe_replace already treats current_reference_price=None as "do not
    replace"."""
    from app.services.execution_price_evidence import load_current_execution_price_evidence

    settings = get_settings()
    client, credentials, _connection = await _resolve_provider_and_credentials(
        db=db, provider=provider, environment=environment,
    )
    if client is None or credentials is None:
        return None
    try:
        _evidence, reference_price, _age_minutes = await load_current_execution_price_evidence(
            provider_client=client,
            credentials=credentials,
            environment=environment,
            expected_provider=provider,
            product_id=instrument,
            max_age_minutes=settings.autonomous_limit_entry_reference_price_max_age_minutes,
        )
        return reference_price
    except Exception:
        logger.warning(
            "limit_entry_reference_price_unavailable provider=%s environment=%s instrument=%s",
            provider, environment, instrument, exc_info=True,
        )
        return None


async def advance_due_limit_entry_attempts(*, db: AsyncSession, now: datetime, current_reference_prices: dict[str, Decimal] | None = None) -> list[AutonomousLimitEntryAttempt]:
    """Per-orchestration-cycle supervisor tick: advances every attempt whose
    next_attempt_at has elapsed, one stage-step each, using SKIP LOCKED so a
    concurrent worker (or a restart mid-cycle) never double-processes the
    same row. Returns the attempts that were advanced this call.

    current_reference_prices is an optional override (used by tests, and
    available to any future caller that already has fresher evidence in
    hand); when omitted, a real, fresh Kraken price is resolved internally
    (via _load_fresh_reference_price) for exactly the instruments of any
    CANCELLED-stage rows in this batch -- the only stage where a reference
    price is ever consulted (_maybe_replace) -- never sourced from candle
    data or the attempt's own prior limit price.
    """
    reference_prices = dict(current_reference_prices or {})
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
    if current_reference_prices is None:
        cancelled_scopes = {(attempt.provider, attempt.environment, attempt.instrument) for attempt in rows if attempt.stage == STAGE_CANCELLED}
        for provider, environment, instrument in cancelled_scopes:
            price = await _load_fresh_reference_price(db=db, provider=provider, environment=environment, instrument=instrument)
            if price is not None:
                reference_prices[instrument] = price
    for attempt in rows:
        await advance_one_limit_entry_attempt(
            db=db, attempt=attempt, now=now,
            current_reference_price=reference_prices.get(attempt.instrument),
        )
        await db.flush()
    return rows
