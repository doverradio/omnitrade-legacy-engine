from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_run import ControlledProofRun
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.services.asset_commissioning import get_asset_readiness
from app.services.capital_campaign_domain import get_governing_campaign_definition
from app.services.controlled_proof.exit_recovery import has_active_exit_recovery
from app.services.orchestration.reconciliation_guard import has_unresolved_reconciliation
from app.services.live.position_quantity import owned_position_exists as shared_owned_position_exists
from app.services.live.position_quantity import QUANTITY_BEARING_RECORD_TYPES
from app.services.mandates.lifecycle import get_governing_authorized_mandate_version
from app.services.position_lifecycle.source_adapter import load_position_snapshots
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
    persist_risk_decision,
)
from app.services.risk.risk_context import resolve_execution_risk_context
from app.services.strategies.identity import build_strategy_identity
from app.config import get_settings

logger = logging.getLogger(__name__)

# --- Server-enforced production scope for v1 ------------------------------
#
# None of these are caller-supplied. This is the entire "no arbitrary
# parameter surface" guarantee: an operator can name a product and an
# idempotency key, nothing else. Widening scope (a different campaign,
# provider, environment, or notional ceiling) requires a code change and a
# new review, never a request payload.
#
# Campaign VERSION is deliberately not pinned here the same way: it is
# resolved dynamically, on every call, from get_governing_campaign_definition
# -- the same single source of truth the normal autonomous path already trusts.
# By the time any version is governing it has already passed the full,
# separately-audited canonical-campaign-status-transition promotion gate
# (exact $5 bounds, fresh provider evidence, zero conflicts, confirm=true).
# A hardcoded version number would instead go stale the moment a new
# campaign version is legitimately promoted, permanently dead-ending every
# future controlled proof attempt for no safety benefit.
ALLOWED_PROVIDER = "kraken_spot"
ALLOWED_ENVIRONMENT = "production"
ALLOWED_CAMPAIGN_ID = uuid.UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
MAX_NOTIONAL_USD = Decimal("5")

_ACTIVE_STATES = (
    "REQUESTED", "CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN",
    "WAITING_FOR_PROFITABLE_EXIT",
)
_CANCELLABLE_STATES = ("REQUESTED", "CLAIMED")
_TERMINAL_PERSISTED_STATES = ("BLOCKED", "EXPIRED", "CANCELLED", "FAILED")
# Every LiveCryptoOrder status representing a genuinely final, already-
# resolved provider outcome. Must stay in sync with _TERMINAL_ORDER_STATUSES
# in app.services.orchestration.reconciliation_scheduler (duplicated locally
# rather than imported to avoid coupling this module's import graph to the
# orchestration package for one small constant).
_TERMINAL_LIVE_ORDER_STATUSES = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _reap_expired(*, db: AsyncSession) -> None:
    """Actively transitions any active-state row whose expires_at has
    passed to EXPIRED. Deliberately a write, not just a read-side filter --
    a status field nothing ever advances past its bounded window is exactly
    the class of defect already found and fixed twice elsewhere in this
    codebase (CanonicalProvingActivation, canonical preview packages)."""
    now = _utcnow()
    rows = (await db.scalars(
        select(ControlledProofRun).where(
            ControlledProofRun.status.in_(_ACTIVE_STATES),
            ControlledProofRun.expires_at <= now,
        )
    )).all()
    for row in rows:
        before = row.status
        row.status = "EXPIRED"
        row.updated_at = now
        db.add(AuditLog(
            actor="system:controlled_proof_expiry", action="controlled_proof_run.expired",
            entity_type="controlled_proof_run", entity_id=row.proof_id,
            before_state={"status": before}, after_state={"status": "EXPIRED"},
        ))
    if rows:
        await db.flush()


async def _resolve_live_trading_profile_id(
    *, db: AsyncSession, paper_account_id: uuid.UUID,
) -> uuid.UUID | None:
    """Same resolution continuous_pipeline_worker._load_live_trading_profile_for_paper_account
    uses to assign package.live_trading_profile_id when a package is created --
    the value that ends up as claim.profile_id, which is exactly what
    prepare_autonomous_claimed_buy's owned_position_exists check is scoped to.
    Any other resolution here would let this module's notion of "the live
    trading profile" silently diverge from the one execution actually uses."""
    return await db.scalar(
        select(LiveTradingProfile.id)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
        .limit(1)
    )


async def _owned_position_exists(
    *, db: AsyncSession, live_trading_profile_id: uuid.UUID, product_id: str,
) -> bool:
    """Thin wrapper over the single shared, canonical implementation
    (app.services.live.position_quantity.owned_position_exists) --
    prepare_autonomous_claimed_buy's owned_position_exists check
    (autonomous_order_preparation.py) uses the exact same function. Both
    must always agree: this module deciding "safe to replace" while that
    one independently decides "owned position exists" for the same real
    funds is exactly the production incident this function exists to
    prevent."""
    return await shared_owned_position_exists(
        db=db, live_trading_profile_id=live_trading_profile_id, symbol=product_id,
    )


async def _live_capital_blocker(*, db: AsyncSession, proof: ControlledProofRun) -> str | None:
    """Precise reason a proof may not be replaced, or None if replacement is
    safe.

    proof.buy_live_crypto_order_id / sell_live_crypto_order_id / position_id
    are checked first as a fast path, but are NOT trusted alone: all three
    are written in exactly one place in this codebase --
    get_controlled_proof_view's own opportunistic backfill -- which only
    runs as a side effect of that proof's status being read. A proof whose
    BUY was submitted and even fully filled, but whose view was never
    queried since (a fully unattended run has no reason to call it), would
    still show all three columns as NULL despite a real live order or open
    position existing. A false negative here is the dangerous direction --
    it would let create_controlled_proof cancel a proof that may control
    real funds -- so this always re-derives from the same authoritative,
    live sources claim_activated_package's own unresolved_order_exists/
    campaign_position_already_open checks and should_propose_controlled_sell
    use, never trusting a cached column as sufficient proof of safety.

    For the same reason, failing to resolve the ownership scope itself
    (no runtime campaign row, no paper_account_id, no live trading profile)
    is never treated as "safe" either -- an inability to prove no live
    capital exists is not proof that none exists. See
    "ownership_scope_unresolved" below."""
    if proof.buy_live_crypto_order_id is not None:
        return "live_buy_order_exists"
    if proof.sell_live_crypto_order_id is not None:
        return "live_sell_order_exists"
    if proof.position_id is not None:
        return "open_position_exists"

    live_order = await db.scalar(
        select(LiveCryptoOrder.side).where(
            LiveCryptoOrder.provider == proof.provider,
            LiveCryptoOrder.environment == proof.environment,
            LiveCryptoOrder.product_id == proof.product_id,
            LiveCryptoOrder.submitted_at.is_not(None),
            LiveCryptoOrder.status.not_in(_TERMINAL_LIVE_ORDER_STATUSES),
        ).limit(1)
    )
    if live_order is not None:
        return "live_buy_order_exists" if live_order.upper() == "BUY" else "live_sell_order_exists"

    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == proof.campaign_id).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        # Cannot resolve the runtime campaign or its paper account, so there
        # is no way to derive which live trading profile (if any) may hold
        # real capital for this proof's product. Not being able to prove
        # "no live capital exists" is not the same as it being safe --
        # fail closed rather than silently returning None (safe to replace).
        return "ownership_scope_unresolved"
    profile_id = await _resolve_live_trading_profile_id(db=db, paper_account_id=runtime.paper_account_id)
    if profile_id is None:
        # Same fail-closed reasoning: no live trading profile means the
        # authoritative accounting-record scope cannot be established, not
        # that it is provably empty.
        return "ownership_scope_unresolved"
    if await _owned_position_exists(db=db, live_trading_profile_id=profile_id, product_id=proof.product_id):
        return "open_position_exists"
    return None


async def controlled_proof_entry_blocker(*, db: AsyncSession, proof: ControlledProofRun) -> str | None:
    """Public pre-entry use of the canonical live-capital ownership guard."""
    return await _live_capital_blocker(db=db, proof=proof)


async def _stale_recovery_blocker(*, db: AsyncSession, proof: ControlledProofRun) -> str | None:
    """Every condition governed stale-proof recovery requires before an
    expired active proof may be automatically terminalized: everything
    _live_capital_blocker already checks (cached and re-derived buy/sell
    order evidence, open position), plus three conditions ordinary entry
    never has to consider but a proof sitting past its own expires_at
    specifically might: unresolved reconciliation, any not-yet-terminal
    execution claim (a claim can exist before any LiveCryptoOrder row does,
    so _live_capital_blocker's order-based checks alone would miss it), and
    any still-active Controlled Proof exit recovery. Same fail-closed
    contract as _live_capital_blocker: returns a precise reason, or None
    only when every one of these is genuinely absent."""
    blocker = await _live_capital_blocker(db=db, proof=proof)
    if blocker is not None:
        return blocker

    if await has_unresolved_reconciliation(
        db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id,
    ):
        return "unresolved_reconciliation_exists"

    # Scoped to this proof's own package lineage (package_id/sell_package_id),
    # never by provider/environment/product alone -- an unrelated ordinary
    # production claim for the same market (e.g. the normal autonomous cycle
    # trading the identical BTC-USD/kraken_spot/production scope) would
    # otherwise falsely block this proof's recovery. An AutonomousExecutionClaim
    # is always created for a specific package_id (claim_activated_package), and
    # a package only ever becomes "this proof's" via link_controlled_proof_package/
    # link_controlled_proof_sell_package -- the same moment proof.package_id/
    # sell_package_id are set -- so a claim genuinely belonging to this proof
    # must reference one of exactly these two package ids. If neither is set,
    # this proof has never been linked to any package, so no claim could exist
    # for it either -- not an unresolvable ambiguity, a provable absence.
    proof_package_ids = [pid for pid in (proof.package_id, proof.sell_package_id) if pid is not None]
    if proof_package_ids:
        active_claim = await db.scalar(select(AutonomousExecutionClaim.claim_id).where(
            AutonomousExecutionClaim.package_id.in_(proof_package_ids),
            AutonomousExecutionClaim.claim_status.in_((
                "CLAIMED", "EXECUTION_STARTED", "SUBMISSION_PENDING",
                "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED",
            )),
        ).limit(1))
        if active_claim is not None:
            return "unresolved_execution_claim_exists"

    if await has_active_exit_recovery(db=db, proof_id=proof.proof_id):
        return "exit_recovery_active"
    return None


@dataclass(frozen=True, slots=True)
class StaleControlledProofRecoveryOutcome:
    proof_id: uuid.UUID
    recovered: bool
    blocker: str | None


async def _recover_stale_active_proof_if_safe(
    *, db: AsyncSession, proof: ControlledProofRun, actor: str,
    replacement_idempotency_key: str | None = None,
) -> StaleControlledProofRecoveryOutcome:
    """`proof` must already be locked (SELECT ... FOR UPDATE) by the caller
    and known to still be in an active state. Terminalizes it to
    EXPIRED/terminal_verdict=FAILED only when it is genuinely past its own
    expires_at AND provably free of every condition _stale_recovery_blocker
    checks. Never deletes the row, never cancels or otherwise touches a live
    exchange order, and never guesses: any blocker at all -- including an
    unresolvable ownership scope -- fails closed and leaves the proof
    completely untouched, requiring explicit exit recovery or reconciliation
    instead. Caller is responsible for committing (this only flushes),
    matching every other controlled-proof state-transition helper in this
    module."""
    now = _utcnow()
    # Postgres's TIMESTAMPTZ always round-trips timezone-aware; sqlite (used
    # only by this module's own tests) has no native tz-aware type and can
    # hand back a naive value after a flush-triggered reload -- normalize
    # rather than let that test-environment quirk raise TypeError here.
    expires_at = proof.expires_at if proof.expires_at.tzinfo is not None else proof.expires_at.replace(tzinfo=timezone.utc)
    if expires_at > now:
        return StaleControlledProofRecoveryOutcome(proof_id=proof.proof_id, recovered=False, blocker="not_expired")

    blocker = await _stale_recovery_blocker(db=db, proof=proof)
    if blocker is not None:
        return StaleControlledProofRecoveryOutcome(proof_id=proof.proof_id, recovered=False, blocker=blocker)

    before = proof.status
    proof.status = "EXPIRED"
    proof.terminal_verdict = "FAILED"
    proof.failure_reason = "expired_before_execution_completion"
    proof.updated_at = now
    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.stale_recovery_expired", entity_type="controlled_proof_run",
        entity_id=proof.proof_id,
        before_state={"status": before, "expires_at": proof.expires_at.isoformat()},
        after_state={
            "status": "EXPIRED", "terminal_verdict": "FAILED",
            "failure_reason": "expired_before_execution_completion",
            "replacement_idempotency_key": replacement_idempotency_key,
        },
    ))
    await db.flush()
    return StaleControlledProofRecoveryOutcome(proof_id=proof.proof_id, recovered=True, blocker=None)


async def recover_stale_controlled_proof(*, db: AsyncSession, actor: str) -> StaleControlledProofRecoveryOutcome:
    """Explicit, operator-triggered counterpart to the automatic recovery
    create_controlled_proof performs inline whenever a stale active proof
    would otherwise block a new one -- same governed safety check, same
    terminal transition, same audit trail, just invocable standalone without
    also creating a replacement proof. Locks the current active proof (if
    any) under SELECT ... FOR UPDATE, exactly like create_controlled_proof's
    own active-proof check, so two concurrent recovery attempts (or a
    recovery racing a create) can never both act on the same row. Raises a
    specific, actionable InvalidRequestError rather than silently no-op-ing
    when there is nothing to recover or recovery is not currently safe."""
    existing_active = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.status.in_(_ACTIVE_STATES)).with_for_update().limit(1)
    )
    if existing_active is None:
        raise NotFoundError(message="No active controlled proof to recover", details={})
    outcome = await _recover_stale_active_proof_if_safe(db=db, proof=existing_active, actor=actor)
    if not outcome.recovered:
        if outcome.blocker == "not_expired":
            raise InvalidRequestError(
                message="Active controlled proof has not yet expired",
                details={"proof_id": str(existing_active.proof_id), "expires_at": existing_active.expires_at.isoformat()},
            )
        raise InvalidRequestError(
            message=(
                f"Cannot automatically recover stale controlled proof: exit recovery or "
                f"reconciliation is required ({outcome.blocker})"
            ),
            details={"proof_id": str(existing_active.proof_id), "blocker": outcome.blocker},
        )
    await db.commit()
    return outcome


@dataclass(frozen=True, slots=True)
class ControlledProofRuntimeScope:
    paper_account_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    exchange_connection_id: uuid.UUID
    capital_campaign_row_id: int


async def resolve_controlled_proof_runtime_scope(*, db: AsyncSession) -> ControlledProofRuntimeScope:
    """Resolves the exact scope get_controlled_proof_mandate_readiness checks a
    configured mandate against -- ALLOWED_CAMPAIGN_ID's runtime paper account and live
    trading profile, plus the single connected/credentials-valid ALLOWED_PROVIDER/
    ALLOWED_ENVIRONMENT exchange connection. Used by mandate provisioning so a freshly
    created CONTROLLED_PROOF mandate is scoped correctly by construction, never by
    coincidence. Raises InvalidRequestError (never returns a partial/guessed scope) if
    any part cannot be resolved -- the same conditions readiness reports as blockers are
    fatal here, since there is nothing correct to provision against."""
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == ALLOWED_CAMPAIGN_ID).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        raise InvalidRequestError(
            message="Controlled Proof runtime campaign/paper account cannot be resolved",
            details={"campaign_id": str(ALLOWED_CAMPAIGN_ID)},
        )

    profile_id = await _resolve_live_trading_profile_id(db=db, paper_account_id=runtime.paper_account_id)
    if profile_id is None:
        raise InvalidRequestError(
            message="no live trading profile found for Controlled Proof's runtime paper account",
            details={"paper_account_id": str(runtime.paper_account_id)},
        )

    connections = (await db.scalars(select(ExchangeConnection).where(
        ExchangeConnection.provider == ALLOWED_PROVIDER,
        ExchangeConnection.environment == ALLOWED_ENVIRONMENT,
        ExchangeConnection.status == "connected",
        ExchangeConnection.credentials_valid.is_(True),
    ))).all()
    if len(connections) != 1:
        raise InvalidRequestError(
            message=(
                f"{len(connections)} connected, credentials-valid exchange connections found for "
                f"{ALLOWED_PROVIDER}/{ALLOWED_ENVIRONMENT}; Controlled Proof requires exactly one"
            ),
            details={"count": len(connections), "provider": ALLOWED_PROVIDER, "environment": ALLOWED_ENVIRONMENT},
        )

    return ControlledProofRuntimeScope(
        paper_account_id=runtime.paper_account_id,
        live_trading_profile_id=profile_id,
        exchange_connection_id=connections[0].exchange_connection_id,
        capital_campaign_row_id=runtime.id,
    )


@dataclass(frozen=True, slots=True)
class ControlledProofStartResult:
    proof: ControlledProofRun
    created: bool


def _available_usd(connection: ExchangeConnection) -> Decimal | None:
    for item in connection.balances or []:
        if str(item.get("currency") or "").upper() == "USD":
            try:
                value = Decimal(str(item.get("available", item.get("balance"))))
            except (ArithmeticError, TypeError, ValueError):
                return None
            return value if value >= 0 else None
    return None


async def get_controlled_proof_mandate_readiness(*, db: AsyncSession) -> dict:
    """Read-only operator report: is the dedicated CONTROLLED_PROOF mandate
    (settings.controlled_proof_mandate_id) configured, ACTIVE, authorized,
    correctly scoped, and carrying the required $5 / position_limit=1 / BUY+SELL
    limits -- i.e. is Controlled Proof actually able to launch right now.
    Never mutates anything; a full eligibility re-check still happens for
    real at entry time (evaluate_mandate_eligibility) -- this only reports
    configuration state ahead of that."""
    from app.services.mandates.contracts import MANDATE_PURPOSE_CONTROLLED_PROOF

    settings = get_settings()
    mandate_id = getattr(settings, "controlled_proof_mandate_id", None)
    blockers: list[str] = []
    report: dict = {
        "configured": mandate_id is not None,
        "mandate_id": mandate_id,
        "mandate_found": False,
        "purpose": None,
        "status": None,
        "autonomy_level": None,
        "provider": None,
        "environment": None,
        "exchange_connection_id": None,
        "live_trading_profile_id": None,
        "paper_account_id": None,
        "capital_campaign_id": None,
        "governing_version_id": None,
        "governing_version_found": False,
        "max_order_notional_usd": None,
        "max_open_exposure_usd": None,
        "position_limit": None,
        "allowed_products": None,
        "allowed_order_sides": None,
        "ready": False,
        "blockers": blockers,
    }
    if mandate_id is None:
        blockers.append("controlled_proof_mandate_id is not configured")
        return report

    mandate = await db.get(AutonomousCapitalMandate, mandate_id)
    if mandate is None:
        blockers.append("configured mandate_id does not exist")
        return report

    report.update(
        mandate_found=True,
        purpose=mandate.purpose,
        status=mandate.status,
        autonomy_level=mandate.autonomy_level,
        provider=mandate.provider,
        environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
    )
    if mandate.purpose != MANDATE_PURPOSE_CONTROLLED_PROOF:
        blockers.append(f"mandate purpose is '{mandate.purpose}', expected 'CONTROLLED_PROOF'")
    if mandate.status != "ACTIVE":
        blockers.append(f"mandate status is '{mandate.status}', expected 'ACTIVE'")
    if mandate.autonomy_level != "LEVEL_2":
        blockers.append(f"mandate autonomy_level is '{mandate.autonomy_level}', expected 'LEVEL_2'")
    if mandate.provider != ALLOWED_PROVIDER:
        blockers.append(f"mandate provider is '{mandate.provider}', expected '{ALLOWED_PROVIDER}'")
    if mandate.exchange_environment != ALLOWED_ENVIRONMENT:
        blockers.append(f"mandate environment is '{mandate.exchange_environment}', expected '{ALLOWED_ENVIRONMENT}'")

    # Runtime scope compatibility: the exact paper account / live trading
    # profile / exchange connection / campaign Controlled Proof actually
    # resolves at entry time (same chain create_controlled_proof and
    # _live_capital_blocker use) must match what this mandate is pinned to
    # -- a mandate scoped to a different profile/connection/campaign would
    # never pass evaluate_mandate_eligibility's own scope checks at
    # runtime, so a mismatch here is reported now rather than discovered
    # only when a real BUY attempt fails.
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == ALLOWED_CAMPAIGN_ID).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        blockers.append("Controlled Proof runtime campaign/paper account cannot be resolved")
    else:
        if mandate.paper_account_id is not None and mandate.paper_account_id != runtime.paper_account_id:
            blockers.append(
                f"mandate paper_account_id is '{mandate.paper_account_id}', "
                f"expected '{runtime.paper_account_id}' (Controlled Proof's runtime paper account)"
            )
        if mandate.capital_campaign_id is not None and mandate.capital_campaign_id != runtime.id:
            blockers.append(
                f"mandate capital_campaign_id is {mandate.capital_campaign_id}, "
                f"expected {runtime.id} (Controlled Proof's pinned campaign)"
            )
        profile_id = await _resolve_live_trading_profile_id(db=db, paper_account_id=runtime.paper_account_id)
        if profile_id is None:
            blockers.append("no live trading profile found for Controlled Proof's runtime paper account")
        elif mandate.live_trading_profile_id != profile_id:
            blockers.append(
                f"mandate live_trading_profile_id is '{mandate.live_trading_profile_id}', "
                f"expected '{profile_id}' (Controlled Proof's runtime profile)"
            )

    connections = (await db.scalars(select(ExchangeConnection).where(
        ExchangeConnection.provider == ALLOWED_PROVIDER,
        ExchangeConnection.environment == ALLOWED_ENVIRONMENT,
        ExchangeConnection.status == "connected",
        ExchangeConnection.credentials_valid.is_(True),
    ))).all()
    if len(connections) != 1:
        blockers.append(
            f"{len(connections)} connected, credentials-valid exchange connections found for "
            f"{ALLOWED_PROVIDER}/{ALLOWED_ENVIRONMENT}; Controlled Proof requires exactly one"
        )
    elif mandate.exchange_connection_id != connections[0].exchange_connection_id:
        blockers.append(
            f"mandate exchange_connection_id is '{mandate.exchange_connection_id}', "
            f"expected '{connections[0].exchange_connection_id}' (the one authoritative production connection)"
        )

    version = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
    if version is None:
        blockers.append("no ACTIVE, authorized governing mandate version found")
        report["ready"] = False
        return report

    report.update(
        governing_version_id=version.mandate_version_id,
        governing_version_found=True,
        max_order_notional_usd=version.max_order_notional_usd,
        max_open_exposure_usd=version.max_open_exposure_usd,
        position_limit=version.position_limit,
        allowed_products=list(version.allowed_products),
        allowed_order_sides=list(version.allowed_order_sides),
    )
    if Decimal(version.max_order_notional_usd) != MAX_NOTIONAL_USD:
        blockers.append(f"max_order_notional_usd is {version.max_order_notional_usd}, expected {MAX_NOTIONAL_USD}")
    if Decimal(version.max_open_exposure_usd) != MAX_NOTIONAL_USD:
        blockers.append(f"max_open_exposure_usd is {version.max_open_exposure_usd}, expected {MAX_NOTIONAL_USD}")
    if version.position_limit != 1:
        blockers.append(f"position_limit is {version.position_limit}, expected 1")
    # Mirrors AUTONOMOUS_CYCLE_PRODUCT_ID (services/orchestration/asset_roster.py)
    # -- the sole product Controlled Proof's own pipeline actually trades
    # today. Not imported directly to avoid pulling asset_roster's
    # capital_campaign_domain -> commissioned_entry_execution ->
    # live_crypto_orders import chain into this module.
    if "BTC-USD" not in version.allowed_products:
        blockers.append("allowed_products does not include BTC-USD")
    if "BUY" not in version.allowed_order_sides:
        blockers.append("allowed_order_sides does not include BUY")
    if "SELL" not in version.allowed_order_sides:
        blockers.append("allowed_order_sides does not include SELL")

    report["ready"] = not blockers
    return report


async def start_live_controlled_proof(
    *, db: AsyncSession, product_id: str, notional_usd: Decimal,
    idempotency_key: str, expires_in_minutes: int, actor: str,
) -> ControlledProofStartResult:
    """Preflight and delegate one new live proof to canonical creation.

    This function creates no execution artifacts. Packages, claims and live
    orders remain exclusively owned by the existing worker pipeline.
    """
    product = product_id.strip().upper()
    key = idempotency_key.strip()
    try:
        requested_notional = Decimal(str(notional_usd))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise InvalidRequestError(message="notional_usd is invalid", details={}) from exc
    if requested_notional <= 0 or requested_notional != MAX_NOTIONAL_USD:
        raise InvalidRequestError(
            message="Controlled Proof requires the exact configured live notional",
            details={"requested_notional_usd": str(requested_notional), "required_notional_usd": str(MAX_NOTIONAL_USD)},
        )
    if not product or not key:
        raise InvalidRequestError(message="product and idempotency_key are required", details={})

    # A replay returns the original proof before mutable readiness is checked:
    # retrying an accepted request must never create a replacement merely
    # because balance or flags changed afterward.
    replay = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.idempotency_key == key))
    if replay is not None:
        return ControlledProofStartResult(proof=replay, created=False)

    settings = get_settings()
    if not settings.live_crypto_order_submission_enabled:
        raise InvalidRequestError(message="Live Controlled Proof execution is disabled", details={})

    connections = (await db.scalars(select(ExchangeConnection).where(
        ExchangeConnection.provider == ALLOWED_PROVIDER,
        ExchangeConnection.environment == ALLOWED_ENVIRONMENT,
        ExchangeConnection.status == "connected",
        ExchangeConnection.credentials_valid.is_(True),
    ))).all()
    if len(connections) != 1:
        raise InvalidRequestError(
            message="Controlled Proof requires one authoritative production connection",
            details={"matched_connections": len(connections)},
        )
    available = _available_usd(connections[0])
    if available is None or available < requested_notional:
        raise InvalidRequestError(
            message="Insufficient authoritative USD balance for Controlled Proof",
            details={"available_usd": None if available is None else str(available), "required_usd": str(requested_notional)},
        )

    from app.services.orchestration.continuous_pipeline_worker import _has_open_live_order
    if await _has_open_live_order(
        db=db, provider=ALLOWED_PROVIDER, environment=ALLOWED_ENVIRONMENT, product=product,
    ):
        raise InvalidRequestError(message="An open provider order blocks Controlled Proof", details={})
    if await has_unresolved_reconciliation(
        db=db, provider=ALLOWED_PROVIDER, environment=ALLOWED_ENVIRONMENT, product=product,
    ):
        raise InvalidRequestError(message="Unresolved reconciliation blocks Controlled Proof", details={})
    active_claim = await db.scalar(select(AutonomousExecutionClaim.claim_id).where(
        AutonomousExecutionClaim.provider == ALLOWED_PROVIDER,
        AutonomousExecutionClaim.environment == ALLOWED_ENVIRONMENT,
        AutonomousExecutionClaim.product == product,
        AutonomousExecutionClaim.claim_status.in_((
            "CLAIMED", "EXECUTION_STARTED", "SUBMISSION_PENDING",
            "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED",
        )),
    ).limit(1))
    if active_claim is not None:
        raise InvalidRequestError(message="Unresolved execution lineage blocks Controlled Proof", details={})

    proof, _replaced = await create_controlled_proof(
        db=db, product_id=product, idempotency_key=key,
        expires_in_minutes=expires_in_minutes, actor=actor, replace_active=False,
    )
    return ControlledProofStartResult(proof=proof, created=True)


async def create_controlled_proof(
    *, db: AsyncSession, product_id: str, idempotency_key: str, expires_in_minutes: int, actor: str,
    replace_active: bool = False,
) -> tuple[ControlledProofRun, ControlledProofRun | None]:
    """Returns (new_or_replayed_proof, replaced_proof). replaced_proof is
    non-None only when replace_active=True genuinely cancelled a prior
    active proof to make room for this one."""
    product_id = product_id.strip().upper()
    idempotency_key = idempotency_key.strip()
    if not product_id:
        raise InvalidRequestError(message="product_id is required", details={})
    if not idempotency_key:
        raise InvalidRequestError(message="idempotency_key is required", details={})

    existing = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        # Idempotent replay: never re-cancels or re-creates anything, and in
        # practice this call is only ever reached once per operator-action
        # idempotency_key anyway -- submit_operator_action's own idempotency
        # check already short-circuits before handler.submit is invoked
        # again for a repeated key.
        return existing, None

    # Fail closed: the campaign must genuinely be governing right now, and
    # must already authorize this product, before a proof is even created --
    # not something the proof itself is allowed to establish or work around.
    # The governing version itself is resolved here, not pinned, and used
    # verbatim below -- see the module-level comment on ALLOWED_CAMPAIGN_ID.
    governing = await get_governing_campaign_definition(db=db, campaign_id=ALLOWED_CAMPAIGN_ID)
    if governing is None:
        raise InvalidRequestError(
            message="Controlled proof scope requires a currently governing campaign version",
            details={"campaign_id": str(ALLOWED_CAMPAIGN_ID)},
        )
    resolved_campaign_version = governing.version
    if product_id not in governing.allowed_instruments:
        raise InvalidRequestError(
            message="Campaign does not authorize this product", details={"product_id": product_id},
        )

    readiness = await get_asset_readiness(db=db, product_id=product_id, campaign_id=ALLOWED_CAMPAIGN_ID)
    required_readiness_fields = (
        "provider_supported", "asset_registered", "market_data_current",
        "campaign_authorized", "mandate_authorized", "runtime_selected",
    )
    unmet = [field for field in required_readiness_fields if not readiness.get(field)]
    if unmet:
        raise InvalidRequestError(
            message="Product is not yet fully ready for a controlled proof",
            details={"product_id": product_id, "unmet_readiness": unmet, "blockers": readiness.get("blockers", [])},
        )

    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == ALLOWED_CAMPAIGN_ID).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        raise InvalidRequestError(message="Runtime campaign or paper account missing", details={})

    # Product-specific check deliberately bit-for-bit matches
    # prepare_autonomous_claimed_buy's owned_position_exists check (see
    # _owned_position_exists) -- profile+exact-symbol scoped, no
    # capital_campaign_id filter, since capital_campaign_id on a real fill's
    # accounting records can legitimately be NULL ("uncategorized", see
    # _resolve_campaign_for_live_order) or attributed to a different campaign
    # row than whichever one happens to be governing now. A campaign-scoped
    # query here would silently miss exactly the funds execution's own gate
    # still sees -- the production incident this function exists to prevent.
    profile_id = await _resolve_live_trading_profile_id(db=db, paper_account_id=runtime.paper_account_id)
    if profile_id is not None and await _owned_position_exists(
        db=db, live_trading_profile_id=profile_id, product_id=product_id,
    ):
        raise InvalidRequestError(
            message="An open production position already exists for this product", details={"product_id": product_id},
        )

    # Broader "no other product has an open position either" check. Also
    # deliberately not campaign_id-scoped for the same reason as above --
    # campaign_id=None here means "every accounting record for this paper
    # account's live trading profile(s), regardless of campaign
    # attribution", the safe (fail-closed) direction per
    # _live_capital_blocker's own docstring.
    open_positions = await load_position_snapshots(db=db, account_id=runtime.paper_account_id, campaign_id=None)
    if any(p.position_size != 0 for p in open_positions):
        raise InvalidRequestError(message="An open production position already exists", details={})

    # Locked (not just read) so a concurrent replace_active request racing
    # this one cannot both observe the same active row as replaceable --
    # the second request blocks here until the first's transaction commits
    # or rolls back, then re-evaluates against the now-current row. The
    # database's own uq_controlled_proof_runs_single_active partial unique
    # index remains the final, authoritative backstop regardless.
    existing_active = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.status.in_(_ACTIVE_STATES)).with_for_update().limit(1)
    )
    replaced_proof: ControlledProofRun | None = None
    if existing_active is not None:
        # Governed stale-proof recovery, attempted regardless of
        # replace_active: only when this row is genuinely past its own
        # expires_at AND provably free of every live-capital/unresolved-
        # lineage condition _stale_recovery_blocker checks does it get
        # terminalized here (EXPIRED, never CANCELLED -- this is expiry, not
        # an operator replacement) -- see _recover_stale_active_proof_if_safe.
        # A still-genuinely-active (non-expired) proof is entirely unaffected
        # by this step ("not_expired" is returned immediately, before any
        # capital check even runs) and falls through to the exact same
        # replace_active semantics as before.
        recovery = await _recover_stale_active_proof_if_safe(
            db=db, proof=existing_active, actor=actor, replacement_idempotency_key=idempotency_key,
        )
        if recovery.recovered:
            # Committed immediately, independent of whatever this call does
            # next: a later failure in this same request (e.g. the new
            # proof's own idempotency-key collision below) must never roll
            # back and silently undo this proof's now-durable EXPIRED
            # transition -- that would leave it blocking every subsequent
            # attempt in exactly the same way, forever, the same class of
            # bug the old unconditional _reap_expired()+commit() at this
            # call site existed to prevent.
            await db.commit()
        elif not replace_active:
            if recovery.blocker == "not_expired":
                raise InvalidRequestError(
                    message="Another controlled proof is already active",
                    details={"active_proof_id": str(existing_active.proof_id)},
                )
            raise InvalidRequestError(
                message=(
                    "Another controlled proof is active and past its expiry, but cannot be "
                    f"automatically recovered: exit recovery or reconciliation is required ({recovery.blocker})"
                ),
                details={"active_proof_id": str(existing_active.proof_id), "blocker": recovery.blocker},
            )
        else:
            blocker = await _live_capital_blocker(db=db, proof=existing_active)
            if blocker is not None:
                # Fail closed: never cancel or supersede a proof that may
                # control real funds. The proof itself, and every real
                # downstream row it references, is left completely untouched.
                raise InvalidRequestError(
                    message=f"Cannot replace active controlled proof: live-capital evidence exists ({blocker})",
                    details={"active_proof_id": str(existing_active.proof_id), "blocker": blocker},
                )
            before = existing_active.status
            existing_active.status = "CANCELLED"
            existing_active.cancelled_at = _utcnow()
            existing_active.cancelled_by = actor
            existing_active.updated_at = _utcnow()
            db.add(AuditLog(
                actor=actor, action="controlled_proof_run.cancelled", entity_type="controlled_proof_run",
                entity_id=existing_active.proof_id,
                before_state={"status": before},
                after_state={
                    "status": "CANCELLED", "reason": "replaced_by_operator_request",
                    "replacement_idempotency_key": idempotency_key,
                },
            ))
            await db.flush()
            replaced_proof = existing_active

    proof = ControlledProofRun(
        status="REQUESTED",
        provider=ALLOWED_PROVIDER,
        environment=ALLOWED_ENVIRONMENT,
        campaign_id=ALLOWED_CAMPAIGN_ID,
        campaign_version=resolved_campaign_version,
        product_id=product_id,
        max_notional_usd=MAX_NOTIONAL_USD,
        idempotency_key=idempotency_key,
        requested_by=actor,
        requested_at=_utcnow(),
        expires_at=_utcnow() + timedelta(minutes=expires_in_minutes),
    )
    db.add(proof)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Rolls back the whole transaction, including any cancellation just
        # performed above -- an extremely narrow compound race (this new
        # proof's own idempotency_key colliding at the same instant as a
        # replace_active call). Safe, fail-closed outcome: the old proof
        # simply remains active and the operator retries; nothing is left
        # inconsistent.
        await db.rollback()
        replay = await db.scalar(
            select(ControlledProofRun).where(ControlledProofRun.idempotency_key == idempotency_key)
        )
        if replay is not None:
            return replay, None
        raise InvalidRequestError(
            message="Another controlled proof is already active", details={},
        ) from exc

    if replaced_proof is not None:
        db.add(AuditLog(
            actor=actor, action="controlled_proof_run.replaced", entity_type="controlled_proof_run",
            entity_id=proof.proof_id,
            before_state={"replaced_proof_id": str(replaced_proof.proof_id)},
            after_state={"new_proof_id": str(proof.proof_id), "reason": "replaced_by_operator_request"},
        ))

    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.requested", entity_type="controlled_proof_run",
        entity_id=proof.proof_id,
        before_state=None,
        after_state={
            "status": proof.status, "product_id": product_id, "campaign_id": str(ALLOWED_CAMPAIGN_ID),
            "campaign_version": resolved_campaign_version, "max_notional_usd": str(MAX_NOTIONAL_USD),
            "expires_at": proof.expires_at.isoformat(),
            "replaced_proof_id": None if replaced_proof is None else str(replaced_proof.proof_id),
        },
    ))
    await db.commit()
    return proof, replaced_proof


async def claim_next_controlled_proof_for_scope(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int, provider: str, environment: str,
    product_id: str, cycle_id: uuid.UUID | None,
) -> ControlledProofRun | None:
    """Worker-facing, called every cycle for every product being evaluated.
    Cheap when nothing is pending (indexed lookups, no matching row). Two
    things happen here, both idempotent across repeated calls:

    1. At most one REQUESTED proof for this exact scope is atomically
       claimed (REQUESTED -> CLAIMED), using the same SELECT ... FOR UPDATE
       + Python-side status re-check pattern already used by
       claim_activated_package for exactly-once claims elsewhere in
       this codebase -- not a new pattern.
    2. Whether or not a new claim happened this cycle, the already-CLAIMED
       proof for this scope (if any -- from THIS cycle or an earlier one) is
       returned, since a real qualifying decision may not arrive on the same
       cycle the proof was claimed on, and the worker must keep recognizing
       it on every subsequent cycle until it's fulfilled or expires."""
    await _reap_expired(db=db)
    now = _utcnow()
    requested_row = await db.scalar(
        select(ControlledProofRun)
        .where(
            ControlledProofRun.status == "REQUESTED",
            ControlledProofRun.campaign_id == campaign_id,
            ControlledProofRun.campaign_version == campaign_version,
            ControlledProofRun.provider == provider,
            ControlledProofRun.environment == environment,
            ControlledProofRun.product_id == product_id,
            ControlledProofRun.expires_at > now,
        )
        .order_by(ControlledProofRun.requested_at.asc())
        .limit(1)
        .with_for_update()
    )
    if requested_row is not None and requested_row.status == "REQUESTED":
        await _claim_row(db=db, row=requested_row, cycle_id=cycle_id, now=now)

    return await db.scalar(
        select(ControlledProofRun).where(
            ControlledProofRun.status == "CLAIMED",
            ControlledProofRun.campaign_id == campaign_id,
            ControlledProofRun.campaign_version == campaign_version,
            ControlledProofRun.provider == provider,
            ControlledProofRun.environment == environment,
            ControlledProofRun.product_id == product_id,
            ControlledProofRun.expires_at > now,
        ).limit(1)
    )


async def claim_controlled_proof_by_id(
    *, db: AsyncSession, proof_id: uuid.UUID, cycle_id: uuid.UUID | None = None,
) -> ControlledProofRun | None:
    """Claim/reload one operator-selected proof independent of automation.

    The row lock makes duplicate immediate dispatches and the periodic
    worker converge on the same proof.  Only REQUESTED/CLAIMED rows are
    returned; scope and expiry are revalidated from persisted authority.
    """
    await _reap_expired(db=db)
    now = _utcnow()
    row = await db.scalar(
        select(ControlledProofRun)
        .where(ControlledProofRun.proof_id == proof_id)
        .limit(1)
        .with_for_update()
    )
    if row is None or row.expires_at <= now or row.status not in _ACTIVE_STATES:
        return None
    if row.status == "REQUESTED":
        await _claim_row(db=db, row=row, cycle_id=cycle_id, now=now)
    return row


async def find_pending_controlled_proof_id(*, db: AsyncSession) -> uuid.UUID | None:
    """Return the bounded operator workflow awaiting an entry attempt."""
    await _reap_expired(db=db)
    return await db.scalar(
        select(ControlledProofRun.proof_id)
        .where(ControlledProofRun.status.in_(_ACTIVE_STATES))
        .order_by(ControlledProofRun.requested_at.asc())
        .limit(1)
    )


async def record_controlled_proof_waiting(
    *, db: AsyncSession, proof: ControlledProofRun, reason: str, actor: str,
) -> None:
    """Persist an actionable retry reason for a nonterminal attempt.

    The v1 schema intentionally has no separate waiting-reason column;
    `failure_reason` is therefore the durable operational-reason field for
    CLAIMED retries.  The `retryable:` prefix is machine-readable and is
    cleared as soon as entry linkage advances the proof.
    """
    if proof.status != "CLAIMED":
        return
    value = f"retryable:{reason}"
    proof.failure_reason = value
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.waiting",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state={"status": "CLAIMED"},
        after_state={"status": "CLAIMED", "failure_reason": value, "retry_semantics": "next_worker_attempt_until_expiry"},
    ))
    await db.flush()


async def _claim_row(*, db: AsyncSession, row: ControlledProofRun, cycle_id: uuid.UUID | None, now: datetime) -> None:
    before = row.status
    row.status = "CLAIMED"
    row.claimed_at = now
    row.claimed_by_cycle_id = cycle_id
    row.updated_at = now
    db.add(AuditLog(
        actor="system:controlled_proof_worker", action="controlled_proof_run.claimed",
        entity_type="controlled_proof_run", entity_id=row.proof_id,
        before_state={"status": before}, after_state={"status": "CLAIMED", "claimed_by_cycle_id": None if cycle_id is None else str(cycle_id)},
    ))
    await db.flush()
    logger.info(
        "controlled_proof_claimed proof_id=%s campaign_id=%s campaign_version=%s product_id=%s cycle_id=%s",
        row.proof_id, row.campaign_id, row.campaign_version, row.product_id, cycle_id,
    )


async def link_controlled_proof_entry(
    *, db: AsyncSession, proof: ControlledProofRun, decision_record_id: uuid.UUID,
    mandate_id: uuid.UUID | None, mandate_version_id: uuid.UUID | None, mandate_evaluation_id: uuid.UUID | None,
) -> None:
    """Idempotent: a proof is linked to its one controlled entry exactly
    once. Never overwrites an existing linkage with a different decision --
    that would silently substitute the audited entry for a proof that
    already has one, which is exactly the "one controlled entry maximum"
    invariant this guards."""
    if proof.decision_record_id is not None:
        return
    proof.decision_record_id = decision_record_id
    proof.failure_reason = None
    proof.mandate_id = mandate_id
    proof.mandate_version_id = mandate_version_id
    proof.mandate_evaluation_id = mandate_evaluation_id
    proof.status = "ENTRY_PROPOSED"
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor="system:controlled_proof_worker", action="controlled_proof_run.entry_linked",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state={"decision_record_id": None},
        after_state={"decision_record_id": str(decision_record_id), "status": "ENTRY_PROPOSED"},
    ))
    await db.flush()


async def link_controlled_proof_package(
    *, db: AsyncSession, proof: ControlledProofRun, package_id: uuid.UUID,
) -> None:
    if proof.package_id is not None:
        return
    proof.package_id = package_id
    proof.status = "PACKAGE_CREATED"
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor="system:controlled_proof_worker", action="controlled_proof_run.package_linked",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state={"package_id": None}, after_state={"package_id": str(package_id), "status": "PACKAGE_CREATED"},
    ))
    await db.flush()


async def link_controlled_proof_sell_package(
    *, db: AsyncSession, proof: ControlledProofRun, sell_package_id: uuid.UUID,
    preserve_terminal_status: bool = False,
) -> None:
    """Idempotent, mirrors link_controlled_proof_package: a proof is linked
    to its one controlled SELL exactly once. Only meaningful once the
    controlled BUY package is already linked -- never called otherwise."""
    if proof.sell_package_id is not None:
        return
    proof.sell_package_id = sell_package_id
    if not preserve_terminal_status:
        proof.status = "WAITING_FOR_PROFITABLE_EXIT"
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor="system:controlled_proof_worker", action="controlled_proof_run.sell_package_linked",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state={"sell_package_id": None},
        after_state={
            "sell_package_id": str(sell_package_id),
            "status": proof.status,
            "exit_recovery": preserve_terminal_status,
        },
    ))
    await db.flush()


async def resolve_controlled_proof_strategy_identity(*, db: AsyncSession, mandate_id: uuid.UUID) -> str | None:
    """Resolves a real, active, mandate-authorized strategy identity for a
    controlled-proof-forced candidate -- the same resolution
    app.services.autonomous_cycle.orchestrator._run_approved_strategy uses
    for a genuine autonomous cycle (active Strategy rows intersected with
    the governing mandate version's allowed_strategy_versions), reused here
    rather than re-derived, so the candidate's strategy identity is always
    one the mandate version already recognizes -- never invented."""
    governing_version = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
    if governing_version is None:
        return None
    approved_versions = set(governing_version.allowed_strategy_versions or [])
    strategies = (await db.scalars(
        select(Strategy).where(Strategy.is_active.is_(True)).order_by(Strategy.created_at.desc())
    )).all()
    for item in strategies:
        identity = build_strategy_identity(slug=item.slug, module_version=item.module_version)
        if identity in approved_versions:
            return identity
    return None


async def should_propose_controlled_sell(*, db: AsyncSession, proof: ControlledProofRun) -> bool:
    """True only once the controlled BUY has a real, filled order and the
    resulting position is genuinely open, and no controlled SELL has been
    proposed yet. Read-only; never itself creates or submits anything."""
    buy_package_linked = proof.package_id is not None
    sell_package_unlinked = proof.sell_package_id is None
    _package, buy_claim, buy_order = await _proof_leg_lineage(
        db=db, proof=proof, package_id=proof.package_id, side="BUY",
    )
    buy_accounting = await _order_accounting(db=db, order=buy_order)
    buy_claim_linked = buy_claim is not None
    buy_order_linked = buy_order is not None
    buy_fill_accounting_exists = any(r.side.upper() == "BUY" for r in buy_accounting)
    proof_owned_quantity = sum(
        (r.filled_quantity for r in buy_accounting
         if r.side.upper() == "BUY" and r.record_type in QUANTITY_BEARING_RECORD_TYPES),
        Decimal("0"),
    )
    position_nonzero = proof_owned_quantity > 0

    prerequisites = (
        ("buy_package_linked", buy_package_linked),
        ("sell_package_unlinked", sell_package_unlinked),
        ("buy_claim_linked", buy_claim_linked),
        ("buy_order_linked", buy_order_linked),
        ("buy_fill_accounting_exists", buy_fill_accounting_exists),
        ("position_nonzero", position_nonzero),
    )
    eligible = all(value for _name, value in prerequisites)
    first_unmet = next((name for name, value in prerequisites if not value), None)
    logger.info(
        "controlled_proof_sell_evaluation proof_id=%s proof_status=%s "
        "buy_package_linked=%s sell_package_unlinked=%s buy_claim_linked=%s "
        "buy_order_linked=%s buy_fill_accounting_exists=%s "
        "position_nonzero=%s eligible=%s",
        proof.proof_id, proof.status,
        str(buy_package_linked).lower(), str(sell_package_unlinked).lower(),
        str(buy_claim_linked).lower(), str(buy_order_linked).lower(),
        str(buy_fill_accounting_exists).lower(),
        str(position_nonzero).lower(), str(eligible).lower(),
    )
    if not eligible:
        logger.info(
            "controlled_proof_sell_ineligible proof_id=%s proof_status=%s reason=%s",
            proof.proof_id, proof.status, first_unmet,
        )
    return eligible


async def resolve_controlled_proof_owned_quantity(*, db: AsyncSession, proof: ControlledProofRun) -> Decimal:
    """Canonical, lineage-derived owned base-asset quantity for one
    Controlled Proof: BUY fills minus any already-reconciled SELL fills,
    resolved exclusively through this proof's own canonical lineage
    (package -> claim -> order -> reconciled accounting) via
    _proof_leg_lineage/_order_accounting -- the exact same resolution
    should_propose_controlled_sell already trusts for eligibility.

    Deliberately never reads proof.buy_live_crypto_order_id /
    sell_live_crypto_order_id. Those columns are only an opportunistic
    read-side projection (see repair_controlled_proof_cached_order_ids's
    own docstring) written solely as a side effect of get_controlled_proof_
    view being called -- they can go stale relative to canonical lineage,
    or simply never have been populated yet for a proof the worker has
    only ever reached through its own periodic dispatch (which does not
    call get_controlled_proof_view until a SELL package already exists).
    That staleness gap is exactly what previously let
    should_propose_controlled_sell report eligible=true for a real,
    fully-reconciled BUY while a quantity resolver keyed off those cache
    columns still returned 0, raising canonical_owned_sell_quantity_missing
    for a genuinely owned, nonzero position.

    Fails closed to Decimal("0") -- never raises -- for every lineage
    state other than a single, unambiguous, scope-matched order: ABSENT,
    PACKAGE_ONLY, CLAIM_ONLY, and every INCONSISTENT reason (missing
    package, scope mismatch, multiple execution claims, a foreign order)
    all resolve `order` to None in _proof_leg_lineage, which this treats
    identically to "no provable quantity". Callers already reject a
    result <= 0 as ineligible to sell -- this function does not duplicate
    that guard, it only ever supplies the number for it to check.
    """
    _buy_package, _buy_claim, buy_order = await _proof_leg_lineage(
        db=db, proof=proof, package_id=proof.package_id, side="BUY",
    )
    if buy_order is None:
        logger.info(
            "controlled_proof_owned_quantity_unresolved proof_id=%s reason=buy_lineage_unresolved",
            proof.proof_id,
        )
        return Decimal("0")
    buy_accounting = await _order_accounting(db=db, order=buy_order)
    bought_quantity = sum(
        (r.filled_quantity for r in buy_accounting
         if r.side.upper() == "BUY" and r.record_type in QUANTITY_BEARING_RECORD_TYPES),
        Decimal("0"),
    )

    sold_quantity = Decimal("0")
    if proof.sell_package_id is not None:
        _sell_package, _sell_claim, sell_order = await _proof_leg_lineage(
            db=db, proof=proof, package_id=proof.sell_package_id, side="SELL",
        )
        if sell_order is not None:
            sell_accounting = await _order_accounting(db=db, order=sell_order)
            sold_quantity = sum(
                (r.filled_quantity for r in sell_accounting
                 if r.side.upper() == "SELL" and r.record_type in QUANTITY_BEARING_RECORD_TYPES),
                Decimal("0"),
            )

    net_quantity = bought_quantity - sold_quantity
    logger.info(
        "controlled_proof_owned_quantity_resolved proof_id=%s bought_quantity=%s sold_quantity=%s net_quantity=%s",
        proof.proof_id, bought_quantity, sold_quantity, net_quantity,
    )
    return net_quantity


@dataclass(frozen=True, slots=True)
class ControlledProofRiskOutcome:
    """Result of one genuine, fresh Risk Engine evaluation for a
    Controlled-Proof-forced candidate. "verdict" is always one of
    ALLOW/RESIZE/DENY/UNAVAILABLE -- never blank -- and callers must never
    treat anything other than ALLOW as permission to proceed."""

    verdict: str
    approved_notional_usd: Decimal | None
    reason_code: str | None
    risk_event_id: uuid.UUID | None


async def evaluate_controlled_proof_risk(
    *, db: AsyncSession, proof_id: uuid.UUID, campaign_id: uuid.UUID, campaign_version: int,
    paper_account_id: uuid.UUID, product_id: str, side: str, notional_usd: Decimal, actor: str,
) -> ControlledProofRiskOutcome:
    """Genuine, fresh Risk Engine evaluation for one Controlled-Proof-forced
    candidate -- reuses the exact same public Risk Engine services the
    organic autonomous path uses (resolve_execution_risk_context,
    evaluate_signal_risk, persist_risk_decision) rather than reusing the
    organic cycle's own risk_verdict, which reflects the original HOLD
    decision (or, for a pure strategy-consensus HOLD, reflects nothing at
    all -- risk was never invoked for it). Deliberately local to the forced-
    entry attempt: never reads or mutates the organic cycle/decision record.

    Known bounded audit behavior: a CLAIMED proof that remains risk-denied
    may persist one genuine risk evaluation per orchestration retry until
    expiration. Durable idempotency requires a separately authorized
    schema-level design.
    """
    logger.info(
        "controlled_proof_risk_evaluation_started proof_id=%s campaign_id=%s campaign_version=%s product=%s side=%s requested_notional_usd=%s",
        proof_id, campaign_id, campaign_version, product_id, side, notional_usd,
    )
    try:
        symbol_base = product_id.split("-")[0]
        asset = await db.scalar(select(Asset).where(Asset.symbol == symbol_base, Asset.exchange == ALLOWED_PROVIDER))
        if asset is None:
            raise LookupError(f"asset_not_registered:{product_id}")
        paper_account = await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id))
        if paper_account is None:
            raise LookupError("paper_account_missing")
        candle_row = (await db.execute(
            select(Candle.close, Candle.open_time, Candle.close_time, Candle.interval, Candle.source)
            .where(Candle.asset_id == asset.id, Candle.interval == "15m")
            .order_by(Candle.open_time.desc())
            .limit(1)
        )).first()
        if candle_row is None or candle_row[0] is None:
            raise LookupError("reference_price_unavailable")
        reference_price = Decimal(str(candle_row[0]))

        risk_context = await resolve_execution_risk_context(db=db, paper_account=paper_account, asset=asset)
        quantity = notional_usd / reference_price
        side_lower = "sell" if side.upper() == "SELL" else "buy"

        risk_result = evaluate_signal_risk(
            request=RiskEvaluationRequest(
                signal_id=uuid.UUID(int=0),
                paper_account_id=paper_account_id,
                asset_id=asset.id,
                side=side_lower,
                quantity=quantity,
                account_equity=risk_context.account_equity,
                max_position_size_pct=risk_context.max_position_size_pct,
                min_order_notional=asset.min_order_notional,
                campaign_authorized_notional=notional_usd,
                qty_step_size=asset.qty_step_size,
                supports_fractional=asset.supports_fractional,
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
                actor=actor,
            ),
            reference_price=reference_price,
            context=RiskEvaluationContext(
                global_kill_switch_engaged=bool(risk_context.global_kill_switch_engaged_state),
            ),
        )
    except Exception as exc:
        logger.exception(
            "controlled_proof_risk_evaluation_unavailable proof_id=%s campaign_id=%s campaign_version=%s product=%s reason=%s",
            proof_id, campaign_id, campaign_version, product_id, f"{exc.__class__.__name__}:{exc}",
        )
        return ControlledProofRiskOutcome(
            verdict="UNAVAILABLE", approved_notional_usd=None,
            reason_code=f"controlled_proof_risk_unavailable:{exc.__class__.__name__}", risk_event_id=None,
        )

    persist_result = await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account_id, signal_id=None, actor=actor, evaluation_result=risk_result,
            evidence_context={
                "purpose": "controlled_proof",
                "proof_id": proof_id,
                "campaign_id": campaign_id,
                "campaign_version": campaign_version,
                "paper_account_id": paper_account_id,
                "product_id": product_id,
                "venue": ALLOWED_PROVIDER,
                "side": side.upper(),
                "requested_notional_usd": notional_usd,
                "requested_quantity": quantity,
                "reference_price": reference_price,
                "reference_candle": {
                    "open_time": candle_row[1], "close_time": candle_row[2],
                    "interval": candle_row[3], "source": candle_row[4],
                },
                "evaluation_time": risk_context.evaluation_time,
                "data_quality": {
                    "data_is_stale": risk_context.data_is_stale,
                    "data_has_gaps": risk_context.data_has_gaps,
                    "candle_data_is_stale": risk_context.candle_data_is_stale,
                    "candle_latest_open_time": risk_context.candle_latest_open_time,
                    "candle_stale_cutoff": risk_context.candle_stale_cutoff,
                    "valuation_state": risk_context.valuation_state,
                    "valuation_latest_price_timestamp": risk_context.valuation_latest_price_timestamp,
                    "valuation_stale_cutoff": risk_context.valuation_stale_cutoff,
                    "missing_price_assets": risk_context.missing_price_assets,
                    "stale_price_assets": risk_context.stale_price_assets,
                    "baseline_state": risk_context.baseline_state,
                    "unresolved_reconciliation_count": risk_context.unresolved_reconciliation_count,
                    "unknown_provider_order_count": risk_context.unknown_provider_order_count,
                },
                "risk_policy": {
                    "source": risk_context.risk_policy_source,
                    "max_position_size_pct": risk_context.max_position_size_pct,
                    "max_daily_loss_pct": risk_context.max_daily_loss_pct,
                    "max_drawdown_pct": risk_context.max_drawdown_pct,
                },
                "equity": {
                    "account_equity": risk_context.account_equity,
                    "start_of_day_equity": risk_context.start_of_day_equity,
                    "current_equity": risk_context.current_equity,
                    "high_water_mark_equity": risk_context.high_water_mark_equity,
                    "start_of_day_equity_source": risk_context.start_of_day_equity_source,
                    "high_water_mark_equity_source": risk_context.high_water_mark_equity_source,
                },
                "kill_switches": {
                    "global_engaged": risk_context.global_kill_switch_engaged_state,
                    "global_rearm_required": risk_context.global_kill_switch_rearm_required,
                    "account_engaged": risk_context.account_kill_switch_engaged_state,
                    "account_rearm_required": risk_context.account_kill_switch_rearm_required,
                },
            },
        ),
    )
    approved_notional = risk_result.approved_quantity * reference_price if risk_result.approved_quantity else Decimal("0")

    if risk_result.action == RiskDecisionAction.APPROVE:
        verdict = "ALLOW"
        logger.info(
            "controlled_proof_risk_allow proof_id=%s campaign_id=%s campaign_version=%s product=%s requested_notional_usd=%s approved_notional_usd=%s reason_code=%s risk_event_id=%s",
            proof_id, campaign_id, campaign_version, product_id, notional_usd, approved_notional, risk_result.reason_code, persist_result.risk_event_id,
        )
    elif risk_result.action == RiskDecisionAction.RESIZE:
        verdict = "RESIZE"
        logger.info(
            "controlled_proof_risk_resize proof_id=%s campaign_id=%s campaign_version=%s product=%s requested_notional_usd=%s approved_notional_usd=%s reason_code=%s risk_event_id=%s",
            proof_id, campaign_id, campaign_version, product_id, notional_usd, approved_notional, risk_result.reason_code, persist_result.risk_event_id,
        )
    elif risk_result.action == RiskDecisionAction.REJECT:
        verdict = "DENY"
        logger.info(
            "controlled_proof_risk_deny proof_id=%s campaign_id=%s campaign_version=%s product=%s requested_notional_usd=%s reason_code=%s risk_event_id=%s",
            proof_id, campaign_id, campaign_version, product_id, notional_usd, risk_result.reason_code, persist_result.risk_event_id,
        )
    else:
        # Defensive: RiskDecisionAction is a closed three-member enum today,
        # but an unrecognized or blank action must never be silently treated
        # as ALLOW.
        verdict = "UNAVAILABLE"
        logger.error(
            "controlled_proof_risk_blank_verdict proof_id=%s campaign_id=%s campaign_version=%s product=%s raw_action=%s risk_event_id=%s",
            proof_id, campaign_id, campaign_version, product_id, risk_result.action, persist_result.risk_event_id,
        )

    return ControlledProofRiskOutcome(
        verdict=verdict,
        approved_notional_usd=approved_notional if verdict in {"ALLOW", "RESIZE"} else None,
        reason_code=risk_result.reason_code,
        risk_event_id=persist_result.risk_event_id,
    )


async def block_controlled_proof(*, db: AsyncSession, proof: ControlledProofRun, reason: str, actor: str) -> None:
    """Transitions a Controlled Proof to a truthful, terminal BLOCKED state
    with the exact reason a fresh evaluate_controlled_proof_risk DENY (or
    other fail-closed forced-entry condition) produced. Risk Engine
    authority is final: a genuine DENY must never leave the proof sitting
    silently in CLAIMED with only a log line as evidence.

    The only place ControlledProofRun.status is ever set to 'BLOCKED' --
    a value the model's CHECK constraint has always allowed, but nothing
    wrote until now. Idempotent and fail-safe: a no-op once the proof has
    already left the active-state set (already blocked, already progressed
    to a real entry, expired, or cancelled by a concurrent path) -- never
    overwrites a real outcome with a stale denial. Caller is responsible
    for the surrounding transaction's commit, matching every other
    controlled-proof linkage helper in this module (link_controlled_proof_
    entry/_package/_sell_package)."""
    if proof.status not in _ACTIVE_STATES:
        return
    before = proof.status
    proof.status = "BLOCKED"
    proof.blocked_reason = reason
    proof.terminal_verdict = "BLOCKED"
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.blocked", entity_type="controlled_proof_run",
        entity_id=proof.proof_id,
        before_state={"status": before},
        after_state={"status": "BLOCKED", "blocked_reason": reason},
    ))
    await db.flush()


async def cancel_controlled_proof(*, db: AsyncSession, proof_id: uuid.UUID, actor: str, reason: str | None) -> ControlledProofRun:
    proof = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.proof_id == proof_id).with_for_update())
    if proof is None:
        raise NotFoundError(message="Controlled proof not found", details={"proof_id": str(proof_id)})
    await _reap_expired(db=db)
    await db.refresh(proof)
    if proof.status not in _CANCELLABLE_STATES:
        raise InvalidRequestError(
            message="Controlled proof can no longer be cancelled once entry has been proposed",
            details={"proof_id": str(proof_id), "status": proof.status},
        )
    before = proof.status
    proof.status = "CANCELLED"
    proof.cancelled_at = _utcnow()
    proof.cancelled_by = actor
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.cancelled", entity_type="controlled_proof_run",
        entity_id=proof.proof_id,
        before_state={"status": before}, after_state={"status": "CANCELLED", "reason": reason},
    ))
    await db.commit()
    return proof


async def _proof_leg_lineage(
    *, db: AsyncSession, proof: ControlledProofRun, package_id: uuid.UUID | None, side: str,
) -> tuple[CanonicalPreviewPackage | None, AutonomousExecutionClaim | None, LiveCryptoOrder | None]:
    """Resolve one proof leg exclusively through package -> claim -> order.

    A cached order id on ``controlled_proof_runs`` is deliberately not an
    input.  Those columns are projections, not execution authority, and a
    campaign/product match is not proof ownership.
    """
    lineage = await resolve_controlled_proof_leg_execution_lineage(
        db=db, proof=proof, package_id=package_id, side=side,
    )
    claim = lineage.claims[0] if len(lineage.claims) == 1 else None
    return lineage.package, claim, lineage.order


@dataclass(frozen=True, slots=True)
class ControlledProofLegExecutionLineage:
    package: CanonicalPreviewPackage | None
    claims: tuple[AutonomousExecutionClaim, ...]
    order: LiveCryptoOrder | None
    state: str
    reason: str | None = None


async def resolve_controlled_proof_leg_execution_lineage(
    *, db: AsyncSession, proof: ControlledProofRun, package_id: uuid.UUID | None, side: str,
) -> ControlledProofLegExecutionLineage:
    """Resolve an execution leg without consulting denormalized proof caches.

    ``PACKAGE_ONLY`` is the sole state proving that first execution has not
    crossed into claim/order lineage. Every inconsistency is explicit so an
    activation caller can fail closed instead of mistaking missing evidence
    for permission.
    """
    if package_id is None:
        return ControlledProofLegExecutionLineage(None, (), None, "ABSENT")
    package = await db.get(CanonicalPreviewPackage, package_id)
    if package is None:
        return ControlledProofLegExecutionLineage(None, (), None, "INCONSISTENT", "package_missing")
    identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
    package_matches = (
        package.campaign_id == proof.campaign_id
        and package.campaign_version == proof.campaign_version
        and package.provider == proof.provider
        and package.environment == proof.environment
        and package.product == proof.product_id
        and package.side == side
        and identity.get("controlled_proof_id") == str(proof.proof_id)
    )
    if not package_matches:
        return ControlledProofLegExecutionLineage(package, (), None, "INCONSISTENT", "package_scope_mismatch")

    claims = tuple((await db.scalars(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package.package_id)
    )).all())
    if not claims:
        return ControlledProofLegExecutionLineage(package, (), None, "PACKAGE_ONLY")
    if len(claims) != 1:
        return ControlledProofLegExecutionLineage(package, claims, None, "INCONSISTENT", "multiple_execution_claims")
    claim = claims[0]
    claim_matches = (
        claim.campaign_id == proof.campaign_id
        and claim.campaign_version == proof.campaign_version
        and claim.provider == proof.provider
        and claim.environment == proof.environment
        and claim.product == proof.product_id
        and claim.side == side
    )
    if not claim_matches:
        return ControlledProofLegExecutionLineage(package, claims, None, "INCONSISTENT", "claim_scope_mismatch")
    if claim.live_order_id is None:
        return ControlledProofLegExecutionLineage(package, claims, None, "CLAIM_ONLY", "claim_order_unresolved")
    order = await db.get(LiveCryptoOrder, claim.live_order_id)
    if order is None:
        return ControlledProofLegExecutionLineage(package, claims, None, "INCONSISTENT", "claimed_order_missing")
    order_matches = (
        order.provider == proof.provider
        and order.environment == proof.environment
        and order.product_id == proof.product_id
        and order.side == side
    )
    if not order_matches:
        return ControlledProofLegExecutionLineage(package, claims, order, "INCONSISTENT", "order_scope_mismatch")
    return ControlledProofLegExecutionLineage(package, claims, order, "ORDER_LINKED")


async def repair_controlled_proof_cached_order_ids(
    *, db: AsyncSession, proof: ControlledProofRun,
) -> bool:
    """Auditably align denormalized order caches with exact canonical lineage.

    Only conclusive ``ABSENT``, ``PACKAGE_ONLY``, and ``ORDER_LINKED`` states
    are repairable. Claim-only or inconsistent lineage is deliberately left
    untouched and must remain blocked by execution authority callers.
    """
    before = {
        "buy_live_crypto_order_id": None if proof.buy_live_crypto_order_id is None else str(proof.buy_live_crypto_order_id),
        "sell_live_crypto_order_id": None if proof.sell_live_crypto_order_id is None else str(proof.sell_live_crypto_order_id),
    }
    evidence: dict[str, Any] = {}
    changed = False
    for side, package_id, attribute in (
        ("BUY", proof.package_id, "buy_live_crypto_order_id"),
        ("SELL", proof.sell_package_id, "sell_live_crypto_order_id"),
    ):
        lineage = await resolve_controlled_proof_leg_execution_lineage(
            db=db, proof=proof, package_id=package_id, side=side,
        )
        evidence[side.lower()] = {"state": lineage.state, "reason": lineage.reason}
        if lineage.state not in {"ABSENT", "PACKAGE_ONLY", "ORDER_LINKED"}:
            continue
        expected = lineage.order.live_crypto_order_id if lineage.order is not None else None
        if getattr(proof, attribute) != expected:
            setattr(proof, attribute, expected)
            changed = True
    if not changed:
        return False
    proof.updated_at = _utcnow()
    after = {
        "buy_live_crypto_order_id": None if proof.buy_live_crypto_order_id is None else str(proof.buy_live_crypto_order_id),
        "sell_live_crypto_order_id": None if proof.sell_live_crypto_order_id is None else str(proof.sell_live_crypto_order_id),
    }
    db.add(AuditLog(
        actor="system:controlled_proof_lineage_projection",
        action="controlled_proof_run.cached_order_lineage_repaired",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state=before, after_state={**after, "canonical_lineage": evidence},
    ))
    await db.flush()
    return True


async def _order_accounting(
    *, db: AsyncSession, order: LiveCryptoOrder | None,
) -> list[LiveAccountingRecord]:
    if order is None:
        return []
    if order.provider_order_id is None:
        return []
    return list((await db.scalars(
        select(LiveAccountingRecord)
        .where(
            LiveAccountingRecord.live_crypto_order_id == order.live_crypto_order_id,
            LiveAccountingRecord.provider_order_id == order.provider_order_id,
            LiveAccountingRecord.symbol == order.product_id,
            LiveAccountingRecord.side == order.side.lower(),
        )
        .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.id.asc())
    )).all())


async def _latest_order_reconciliation(
    *, db: AsyncSession, order: LiveCryptoOrder | None,
) -> LiveReconciliationEvent | None:
    if order is None:
        return None
    return await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.live_crypto_order_id == order.live_crypto_order_id)
        .order_by(LiveReconciliationEvent.sequence_number.desc(), LiveReconciliationEvent.created_at.desc())
        .limit(1)
    )


def _authoritative_fee_total(records: list[LiveAccountingRecord]) -> Decimal | None:
    if not records:
        return None
    by_fill: dict[tuple[uuid.UUID | None, str], list[LiveAccountingRecord]] = {}
    for record in records:
        fill_identity = record.provider_fill_id or str(record.reconciliation_event_id)
        by_fill.setdefault((record.live_crypto_order_id, fill_identity), []).append(record)
    total = Decimal("0")
    for fill_records in by_fill.values():
        fee_attributions = [row for row in fill_records if row.record_type == "fee_attribution"]
        authoritative = fee_attributions or [
            row for row in fill_records if row.record_type in QUANTITY_BEARING_RECORD_TYPES
        ]
        total += sum((row.fee_amount for row in authoritative), Decimal("0"))
    return total


async def get_controlled_proof_view(*, db: AsyncSession, proof_id: uuid.UUID) -> dict[str, Any]:
    proof = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.proof_id == proof_id))
    if proof is None:
        raise NotFoundError(message="Controlled proof not found", details={"proof_id": str(proof_id)})

    await _reap_expired(db=db)
    await db.refresh(proof)
    await repair_controlled_proof_cached_order_ids(db=db, proof=proof)
    recovered_audit = await db.scalar(
        select(AuditLog)
        .join(
            ControlledProofExitRecovery,
            ControlledProofExitRecovery.recovery_id == AuditLog.entity_id,
        )
        .where(
            ControlledProofExitRecovery.proof_id == proof.proof_id,
            ControlledProofExitRecovery.status.in_(("BLOCKED", "COMPLETED")),
            AuditLog.entity_type == "controlled_proof_exit_recovery",
            AuditLog.action == "controlled_proof_exit_recovery.recovered_outcome_published",
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    recovered_projection: tuple[Decimal, str] | None = None
    if recovered_audit is not None and isinstance(recovered_audit.after_state, dict):
        recovered_payload = recovered_audit.after_state
        recovered_recovery_id = recovered_payload.get("original_recovery_id")
        recovered_verdict = recovered_payload.get("recovered_terminal_verdict")
        if (
            recovered_payload.get("status") == "COMPLETED_RECONCILED"
            and recovered_payload.get("proof_id") == str(proof.proof_id)
            and recovered_recovery_id == str(recovered_audit.entity_id)
            and recovered_verdict in {
                "LIFECYCLE_PROVEN_PROFIT", "LIFECYCLE_PROVEN_LOSS", "LIFECYCLE_PROVEN_FLAT",
            }
        ):
            try:
                recovered_projection = (
                    Decimal(str(recovered_payload.get("recovered_net_pnl_usd"))), recovered_verdict,
                )
            except (TypeError, ArithmeticError, ValueError):
                recovered_projection = None

    decision_payload: dict[str, Any] | None = None
    if proof.decision_record_id is not None:
        decision = await db.scalar(select(DecisionRecord).where(DecisionRecord.decision_id == proof.decision_record_id))
        if decision is not None:
            decision_payload = {
                "decision_record_id": str(decision.decision_id),
                "timestamp": decision.timestamp.isoformat() if decision.timestamp else None,
                "trade_accepted": decision.trade_accepted,
                "trade_rejected_reason": decision.trade_rejected_reason,
            }

    mandate_payload: dict[str, Any] | None = None
    if proof.mandate_evaluation_id is not None:
        evaluation = await db.scalar(
            select(AutonomousCapitalMandateEvaluation)
            .where(AutonomousCapitalMandateEvaluation.evaluation_id == proof.mandate_evaluation_id)
        )
        if evaluation is not None:
            mandate_payload = {
                "mandate_id": str(evaluation.mandate_id),
                "mandate_version_id": str(evaluation.mandate_version_id),
                "authorization_result": evaluation.authorization_result,
                "approval_result": evaluation.approval_result,
                "risk_verdict": evaluation.risk_verdict,
                "reason_code": evaluation.reason_code,
            }
    elif proof.mandate_id is not None:
        mandate_payload = {"mandate_id": str(proof.mandate_id), "mandate_version_id": None if proof.mandate_version_id is None else str(proof.mandate_version_id)}

    buy_package, buy_claim, buy_order = await _proof_leg_lineage(
        db=db, proof=proof, package_id=proof.package_id, side="BUY",
    )
    sell_package, sell_claim, sell_order = await _proof_leg_lineage(
        db=db, proof=proof, package_id=proof.sell_package_id, side="SELL",
    )
    package_payload: dict[str, Any] | None = None
    if buy_package is not None:
        package_payload = {"package_id": str(buy_package.package_id), "package_state": buy_package.package_state}

    buy_accounting = await _order_accounting(db=db, order=buy_order)
    sell_accounting = await _order_accounting(db=db, order=sell_order)
    accounting_records = [*buy_accounting, *sell_accounting]

    buy_order_payload: dict[str, Any] | None = None
    buy_order_id = buy_order.live_crypto_order_id if buy_order is not None else None
    if buy_order is not None:
        buy_order_payload = {
            "live_crypto_order_id": str(buy_order.live_crypto_order_id), "status": buy_order.status,
            "provider_order_id": buy_order.provider_order_id, "filled_at": buy_order.filled_at.isoformat() if buy_order.filled_at else None,
        }
        if proof.buy_live_crypto_order_id != buy_order.live_crypto_order_id:
            proof.buy_live_crypto_order_id = buy_order.live_crypto_order_id

    sell_order_payload: dict[str, Any] | None = None
    sell_order_id = sell_order.live_crypto_order_id if sell_order is not None else None
    if sell_order is not None:
        sell_order_payload = {
            "live_crypto_order_id": str(sell_order.live_crypto_order_id), "status": sell_order.status,
            "provider_order_id": sell_order.provider_order_id, "filled_at": sell_order.filled_at.isoformat() if sell_order.filled_at else None,
        }
        if proof.sell_live_crypto_order_id != sell_order.live_crypto_order_id:
            proof.sell_live_crypto_order_id = sell_order.live_crypto_order_id

    position_payload: dict[str, Any] | None = None
    position_open = False
    bought_quantity = sum((
        r.filled_quantity for r in buy_accounting
        if r.side.lower() == "buy" and r.record_type in QUANTITY_BEARING_RECORD_TYPES
    ), Decimal("0"))
    sold_quantity = sum((
        r.filled_quantity for r in sell_accounting
        if r.side.lower() == "sell" and r.record_type in QUANTITY_BEARING_RECORD_TYPES
    ), Decimal("0"))
    position_size = bought_quantity - sold_quantity
    if buy_accounting and position_size > 0:
        position_open = True
        buy_notional = sum((r.gross_notional for r in buy_accounting if r.side.lower() == "buy"), Decimal("0"))
        entry_price = buy_notional / bought_quantity if bought_quantity > 0 else Decimal("0")
        position_payload = {
            "position_id": proof.position_id,
            "position_size": str(position_size),
            "entry_price": str(entry_price),
            "opened_at": buy_accounting[0].recorded_at.isoformat() if buy_accounting[0].recorded_at else None,
        }

    fees_usd = _authoritative_fee_total(accounting_records)
    # A BUY cash outflow is not proof P&L.  P&L exists only after both exact
    # proof legs have authoritative accounting and the proof-owned quantity
    # is closed.
    net_pnl_usd = (
        sum((r.net_cash_impact for r in accounting_records), Decimal("0"))
        if buy_accounting and sell_accounting and position_size == 0
        else None
    )

    reconciliation_payload: dict[str, Any] | None = None
    buy_reconciliation: LiveReconciliationEvent | None = None
    sell_reconciliation: LiveReconciliationEvent | None = None
    if buy_order_id is not None or sell_order_id is not None:
        buy_reconciliation = await _latest_order_reconciliation(db=db, order=buy_order)
        sell_reconciliation = await _latest_order_reconciliation(db=db, order=sell_order)
        required = [buy_reconciliation]
        if sell_order is not None:
            required.append(sell_reconciliation)
        resolved_statuses = {"filled", "canceled", "rejected"}
        unresolved = any(event is None or event.reconciliation_status not in resolved_statuses for event in required)
        reconciliation_payload = {"unresolved": unresolved}

    derived_status = _derive_fine_grained_status(
        proof=proof, decision_linked=proof.decision_record_id is not None,
        package_linked=buy_package is not None, position_open=position_open,
        sell_linked=sell_order_id is not None,
        reconciliation_unresolved=None if reconciliation_payload is None else reconciliation_payload["unresolved"],
        net_pnl_usd=net_pnl_usd,
    )
    if derived_status != proof.status and proof.status not in _TERMINAL_PERSISTED_STATES:
        before_status = proof.status
        proof.status = derived_status
        proof.updated_at = _utcnow()
        db.add(AuditLog(
            actor="system:controlled_proof_lineage_projection",
            action="controlled_proof_run.status_projected",
            entity_type="controlled_proof_run", entity_id=proof.proof_id,
            before_state={"status": before_status}, after_state={"status": derived_status},
        ))

    lineage_terminal = (
        derived_status in {"RECONCILED", "PROFIT_CONFIRMED"}
        and net_pnl_usd is not None
        and reconciliation_payload is not None
        and reconciliation_payload["unresolved"] is False
        and buy_reconciliation is not None
        and buy_reconciliation.reconciliation_status == "filled"
        and sell_reconciliation is not None
        and sell_reconciliation.reconciliation_status == "filled"
    )
    if recovered_projection is not None:
        proof.net_pnl_usd, proof.terminal_verdict = recovered_projection
    elif lineage_terminal:
        proof.net_pnl_usd = net_pnl_usd
    elif (
        proof.net_pnl_usd is not None
        or proof.terminal_verdict in {"LIFECYCLE_PROVEN_PROFIT", "LIFECYCLE_PROVEN_LOSS", "LIFECYCLE_PROVEN_FLAT"}
    ):
        before_projection = {
            "net_pnl_usd": None if proof.net_pnl_usd is None else str(proof.net_pnl_usd),
            "terminal_verdict": proof.terminal_verdict,
        }
        proof.net_pnl_usd = None
        if proof.terminal_verdict in {
            "LIFECYCLE_PROVEN_PROFIT", "LIFECYCLE_PROVEN_LOSS", "LIFECYCLE_PROVEN_FLAT",
        }:
            proof.terminal_verdict = None
        proof.updated_at = _utcnow()
        db.add(AuditLog(
            actor="system:controlled_proof_lineage_projection",
            action="controlled_proof_run.foreign_lineage_projection_cleared",
            entity_type="controlled_proof_run", entity_id=proof.proof_id,
            before_state=before_projection,
            after_state={"net_pnl_usd": None, "terminal_verdict": proof.terminal_verdict},
        ))

    # Terminal verdict: computed once from real, already-derived downstream
    # state and then frozen -- never recomputed once set, so a later read
    # can never flip a reported PROFIT/LOSS/FLAT result. Deliberately
    # separate from `status`: a lifecycle can reach a genuine PROFIT/LOSS/
    # FLAT verdict while `status` itself reads RECONCILED or PROFIT_CONFIRMED,
    # and a proof that expires before ever getting a real decision record
    # (mandate/risk/evidence correctly kept refusing it) is BLOCKED, not a
    # silent no-op -- "do not call a loss a profit" also means never staying
    # silent about an outcome that did happen.
    if recovered_projection is None and proof.terminal_verdict is None:
        if lineage_terminal and proof.net_pnl_usd is not None:
            if proof.net_pnl_usd > 0:
                proof.terminal_verdict = "LIFECYCLE_PROVEN_PROFIT"
            elif proof.net_pnl_usd < 0:
                proof.terminal_verdict = "LIFECYCLE_PROVEN_LOSS"
            else:
                proof.terminal_verdict = "LIFECYCLE_PROVEN_FLAT"
            proof.updated_at = _utcnow()
        elif proof.status == "EXPIRED":
            proof.terminal_verdict = "BLOCKED" if proof.decision_record_id is None else "FAILED"
            proof.updated_at = _utcnow()
        elif proof.status in {"BLOCKED", "FAILED"}:
            proof.terminal_verdict = proof.status
            proof.updated_at = _utcnow()
    await db.commit()

    return {
        "proof_id": proof.proof_id, "status": proof.status, "provider": proof.provider,
        "environment": proof.environment, "campaign_id": proof.campaign_id, "campaign_version": proof.campaign_version,
        "product_id": proof.product_id, "max_notional_usd": proof.max_notional_usd, "requested_by": proof.requested_by,
        "requested_at": proof.requested_at, "expires_at": proof.expires_at, "claimed_at": proof.claimed_at,
        "blocked_reason": proof.blocked_reason, "failure_reason": proof.failure_reason,
        "cancelled_at": proof.cancelled_at, "cancelled_by": proof.cancelled_by,
        "audit_correlation_id": proof.audit_correlation_id,
        "decision": decision_payload, "mandate": mandate_payload, "package": package_payload,
        "buy_order": buy_order_payload, "position": position_payload, "sell_order": sell_order_payload,
        "reconciliation": reconciliation_payload, "fees_usd": fees_usd, "net_pnl_usd": proof.net_pnl_usd,
        "terminal_verdict": proof.terminal_verdict,
    }


def _derive_fine_grained_status(
    *, proof: ControlledProofRun, decision_linked: bool, package_linked: bool, position_open: bool,
    sell_linked: bool, reconciliation_unresolved: bool | None, net_pnl_usd: Decimal | None,
) -> str:
    if proof.status in _TERMINAL_PERSISTED_STATES:
        return proof.status
    if proof.status == "REQUESTED":
        return "REQUESTED"
    if not decision_linked:
        return "CLAIMED"
    if not package_linked:
        return "ENTRY_PROPOSED"
    if position_open:
        return "WAITING_FOR_PROFITABLE_EXIT" if proof.status != "PACKAGE_CREATED" else "POSITION_OPEN"
    if not sell_linked:
        return "PACKAGE_CREATED"
    if reconciliation_unresolved:
        return "EXITED"
    if net_pnl_usd is not None and net_pnl_usd > 0:
        return "PROFIT_CONFIRMED"
    return "RECONCILED"
