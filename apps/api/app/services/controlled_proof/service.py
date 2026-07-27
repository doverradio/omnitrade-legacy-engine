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
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_run import ControlledProofRun
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.services.asset_commissioning import get_asset_readiness
from app.services.capital_campaign_domain import get_governing_campaign_definition
from app.services.live.position_quantity import owned_position_exists as shared_owned_position_exists
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
    live sources claim_activated_buy_package's own unresolved_order_exists/
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

    # Committed immediately, independent of whatever this call does next --
    # otherwise a later failure in this same request (any InvalidRequestError
    # below, including "already active" itself) propagates up through
    # get_db()'s exception path and rolls back the whole transaction,
    # silently undoing this reap's in-memory EXPIRED transition every time,
    # permanently: the row is correctly identified and flipped on every
    # attempt, but never durably persisted, so it blocks every subsequent
    # attempt in exactly the same way, forever. This commit is scoped only
    # to this call site -- claim_next_controlled_proof_for_scope and
    # cancel_controlled_proof are untouched, since cancel already holds a
    # row-level lock before reaping and an early commit there would release
    # it prematurely.
    await _reap_expired(db=db)
    await db.commit()

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
        if not replace_active:
            raise InvalidRequestError(
                message="Another controlled proof is already active",
                details={"active_proof_id": str(existing_active.proof_id)},
            )
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
       claim_activated_buy_package for exactly-once claims elsewhere in
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
) -> None:
    """Idempotent, mirrors link_controlled_proof_package: a proof is linked
    to its one controlled SELL exactly once. Only meaningful once the
    controlled BUY package is already linked -- never called otherwise."""
    if proof.sell_package_id is not None:
        return
    proof.sell_package_id = sell_package_id
    proof.status = "WAITING_FOR_PROFITABLE_EXIT"
    proof.updated_at = _utcnow()
    db.add(AuditLog(
        actor="system:controlled_proof_worker", action="controlled_proof_run.sell_package_linked",
        entity_type="controlled_proof_run", entity_id=proof.proof_id,
        before_state={"sell_package_id": None},
        after_state={"sell_package_id": str(sell_package_id), "status": "WAITING_FOR_PROFITABLE_EXIT"},
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
    if proof.package_id is None or proof.sell_package_id is not None:
        return False
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == proof.campaign_id).limit(1))
    if runtime is None or runtime.paper_account_id is None:
        return False
    accounting_records = await _accounting_records_for_product(db=db, runtime_campaign_id=runtime.id, product_id=proof.product_id)
    buy_filled = any(r.side.upper() == "BUY" for r in accounting_records)
    if not buy_filled:
        return False
    snapshots = await load_position_snapshots(db=db, account_id=runtime.paper_account_id, campaign_id=runtime.id)
    symbol_base = proof.product_id.split("-")[0]
    match = next((s for s in snapshots if s.symbol.split("-")[0].upper() == symbol_base), None)
    return match is not None and match.position_size != 0


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
            select(Candle.close)
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


async def _accounting_records_for_product(
    *, db: AsyncSession, runtime_campaign_id: int, product_id: str,
) -> list[LiveAccountingRecord]:
    symbol_base = product_id.split("-")[0]
    rows = (await db.scalars(
        select(LiveAccountingRecord)
        .where(LiveAccountingRecord.capital_campaign_id == runtime_campaign_id)
        .order_by(LiveAccountingRecord.recorded_at.asc())
    )).all()
    return [r for r in rows if r.symbol.split("-")[0].upper() == symbol_base]


async def get_controlled_proof_view(*, db: AsyncSession, proof_id: uuid.UUID) -> dict[str, Any]:
    proof = await db.scalar(select(ControlledProofRun).where(ControlledProofRun.proof_id == proof_id))
    if proof is None:
        raise NotFoundError(message="Controlled proof not found", details={"proof_id": str(proof_id)})

    await _reap_expired(db=db)
    await db.refresh(proof)

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

    package_payload: dict[str, Any] | None = None
    if proof.package_id is not None:
        package = await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == proof.package_id))
        if package is not None:
            package_payload = {"package_id": str(package.package_id), "package_state": package.package_state}

    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == proof.campaign_id).limit(1))
    accounting_records: list[LiveAccountingRecord] = []
    if runtime is not None:
        accounting_records = await _accounting_records_for_product(db=db, runtime_campaign_id=runtime.id, product_id=proof.product_id)

    buy_record = next((r for r in accounting_records if r.side.upper() == "BUY"), None)
    sell_record = next((r for r in accounting_records if r.side.upper() == "SELL"), None)

    buy_order_payload: dict[str, Any] | None = None
    buy_order_id = proof.buy_live_crypto_order_id or (buy_record.live_crypto_order_id if buy_record else None)
    if buy_order_id is not None:
        buy_order = await db.scalar(select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == buy_order_id))
        if buy_order is not None:
            buy_order_payload = {
                "live_crypto_order_id": str(buy_order.live_crypto_order_id), "status": buy_order.status,
                "provider_order_id": buy_order.provider_order_id, "filled_at": buy_order.filled_at.isoformat() if buy_order.filled_at else None,
            }
            if proof.buy_live_crypto_order_id is None:
                proof.buy_live_crypto_order_id = buy_order.live_crypto_order_id

    sell_order_payload: dict[str, Any] | None = None
    sell_order_id = proof.sell_live_crypto_order_id or (sell_record.live_crypto_order_id if sell_record else None)
    if sell_order_id is not None:
        sell_order = await db.scalar(select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == sell_order_id))
        if sell_order is not None:
            sell_order_payload = {
                "live_crypto_order_id": str(sell_order.live_crypto_order_id), "status": sell_order.status,
                "provider_order_id": sell_order.provider_order_id, "filled_at": sell_order.filled_at.isoformat() if sell_order.filled_at else None,
            }
            if proof.sell_live_crypto_order_id is None:
                proof.sell_live_crypto_order_id = sell_order.live_crypto_order_id

    position_payload: dict[str, Any] | None = None
    position_open = False
    if runtime is not None and runtime.paper_account_id is not None:
        snapshots = await load_position_snapshots(db=db, account_id=runtime.paper_account_id, campaign_id=runtime.id)
        symbol_base = proof.product_id.split("-")[0]
        match = next((s for s in snapshots if s.symbol.split("-")[0].upper() == symbol_base), None)
        if match is not None:
            position_open = match.position_size != 0
            position_payload = {
                "position_id": match.position_id, "position_size": str(match.position_size),
                "entry_price": str(match.entry_price), "opened_at": match.opened_at.isoformat() if match.opened_at else None,
            }
            if proof.position_id is None:
                proof.position_id = match.position_id

    fees_usd = sum((r.fee_amount for r in accounting_records), Decimal("0")) if accounting_records else None
    net_pnl_usd = sum((r.net_cash_impact for r in accounting_records), Decimal("0")) if accounting_records else None

    reconciliation_payload: dict[str, Any] | None = None
    if buy_order_id is not None or sell_order_id is not None:
        from app.services.orchestration.continuous_pipeline_worker import _has_unresolved_reconciliation
        unresolved = await _has_unresolved_reconciliation(
            db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id,
        )
        reconciliation_payload = {"unresolved": unresolved}

    derived_status = _derive_fine_grained_status(
        proof=proof, decision_linked=proof.decision_record_id is not None,
        package_linked=proof.package_id is not None, position_open=position_open,
        sell_linked=sell_order_id is not None,
        reconciliation_unresolved=None if reconciliation_payload is None else reconciliation_payload["unresolved"],
        net_pnl_usd=net_pnl_usd if sell_order_id is not None and not position_open else None,
    )
    if derived_status != proof.status and proof.status not in _TERMINAL_PERSISTED_STATES:
        proof.status = derived_status
        proof.updated_at = _utcnow()
        if net_pnl_usd is not None and derived_status in {"RECONCILED", "PROFIT_CONFIRMED"}:
            proof.net_pnl_usd = net_pnl_usd

    # Terminal verdict: computed once from real, already-derived downstream
    # state and then frozen -- never recomputed once set, so a later read
    # can never flip a reported PROFIT/LOSS/FLAT result. Deliberately
    # separate from `status`: a lifecycle can reach a genuine PROFIT/LOSS/
    # FLAT verdict while `status` itself reads RECONCILED or PROFIT_CONFIRMED,
    # and a proof that expires before ever getting a real decision record
    # (mandate/risk/evidence correctly kept refusing it) is BLOCKED, not a
    # silent no-op -- "do not call a loss a profit" also means never staying
    # silent about an outcome that did happen.
    if proof.terminal_verdict is None:
        if proof.status in {"RECONCILED", "PROFIT_CONFIRMED"} and proof.net_pnl_usd is not None:
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
