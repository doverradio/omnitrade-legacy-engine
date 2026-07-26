from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_run import ControlledProofRun
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.strategy import Strategy
from app.services.asset_commissioning import get_asset_readiness
from app.services.capital_campaign_domain import get_governing_campaign_definition
from app.services.mandates.lifecycle import get_governing_authorized_mandate_version
from app.services.position_lifecycle.source_adapter import load_position_snapshots
from app.services.strategies.identity import build_strategy_identity

# --- Server-enforced production scope for v1 ------------------------------
#
# None of these are caller-supplied. This is the entire "no arbitrary
# parameter surface" guarantee: an operator can name a product and an
# idempotency key, nothing else. Widening scope (a different campaign,
# provider, environment, or notional ceiling) requires a code change and a
# new review, never a request payload.
ALLOWED_PROVIDER = "kraken_spot"
ALLOWED_ENVIRONMENT = "production"
ALLOWED_CAMPAIGN_ID = uuid.UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
ALLOWED_CAMPAIGN_VERSION = 1
MAX_NOTIONAL_USD = Decimal("5")

_ACTIVE_STATES = (
    "REQUESTED", "CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN",
    "WAITING_FOR_PROFITABLE_EXIT",
)
_CANCELLABLE_STATES = ("REQUESTED", "CLAIMED")
_TERMINAL_PERSISTED_STATES = ("BLOCKED", "EXPIRED", "CANCELLED", "FAILED")


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


async def create_controlled_proof(
    *, db: AsyncSession, product_id: str, idempotency_key: str, expires_in_minutes: int, actor: str,
) -> ControlledProofRun:
    product_id = product_id.strip().upper()
    idempotency_key = idempotency_key.strip()
    if not product_id:
        raise InvalidRequestError(message="product_id is required", details={})
    if not idempotency_key:
        raise InvalidRequestError(message="idempotency_key is required", details={})

    await _reap_expired(db=db)

    existing = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    # Fail closed: the campaign must genuinely still be governing at exactly
    # the pinned version, and must already authorize this product, before a
    # proof is even created -- not something the proof itself is allowed to
    # establish or work around.
    governing = await get_governing_campaign_definition(db=db, campaign_id=ALLOWED_CAMPAIGN_ID)
    if governing is None or governing.version != ALLOWED_CAMPAIGN_VERSION:
        raise InvalidRequestError(
            message="Controlled proof scope requires the pinned campaign version to be governing",
            details={"campaign_id": str(ALLOWED_CAMPAIGN_ID), "required_version": ALLOWED_CAMPAIGN_VERSION},
        )
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

    open_positions = await load_position_snapshots(db=db, account_id=runtime.paper_account_id, campaign_id=runtime.id)
    symbol_base = product_id.split("-")[0]
    if any(p.symbol.split("-")[0] == symbol_base and p.position_size != 0 for p in open_positions):
        raise InvalidRequestError(
            message="An open production position already exists for this product", details={"product_id": product_id},
        )
    if any(p.position_size != 0 for p in open_positions):
        raise InvalidRequestError(message="An open production position already exists", details={})

    # Fast, friendly application-level check for the common (non-racing)
    # case. The authoritative, race-safe guarantee is the database's own
    # uq_controlled_proof_runs_single_active partial unique index (Postgres);
    # this check exists so a plain sequential second request gets a clear
    # error immediately rather than depending on that index alone.
    existing_active = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.status.in_(_ACTIVE_STATES)).limit(1)
    )
    if existing_active is not None:
        raise InvalidRequestError(message="Another controlled proof is already active", details={})

    proof = ControlledProofRun(
        status="REQUESTED",
        provider=ALLOWED_PROVIDER,
        environment=ALLOWED_ENVIRONMENT,
        campaign_id=ALLOWED_CAMPAIGN_ID,
        campaign_version=ALLOWED_CAMPAIGN_VERSION,
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
        await db.rollback()
        replay = await db.scalar(
            select(ControlledProofRun).where(ControlledProofRun.idempotency_key == idempotency_key)
        )
        if replay is not None:
            return replay
        raise InvalidRequestError(
            message="Another controlled proof is already active", details={},
        ) from exc

    db.add(AuditLog(
        actor=actor, action="controlled_proof_run.requested", entity_type="controlled_proof_run",
        entity_id=proof.proof_id,
        before_state=None,
        after_state={
            "status": proof.status, "product_id": product_id, "campaign_id": str(ALLOWED_CAMPAIGN_ID),
            "campaign_version": ALLOWED_CAMPAIGN_VERSION, "max_notional_usd": str(MAX_NOTIONAL_USD),
            "expires_at": proof.expires_at.isoformat(),
        },
    ))
    await db.commit()
    return proof


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
