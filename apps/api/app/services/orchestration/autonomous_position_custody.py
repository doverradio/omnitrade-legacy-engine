from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_accounting_record import LiveAccountingRecord
from app.services.live.position_quantity import QUANTITY_BEARING_RECORD_TYPES, compute_signed_owned_quantity

NONTERMINAL_CUSTODY_STATES = {"HANDOFF_PENDING", "ACTIVE", "EXIT_PENDING", "BLOCKED"}
TERMINAL_CUSTODY_STATES = {"CLOSED", "RECOVERED"}
ALLOWED_CUSTODY_TRANSITIONS = {
    "HANDOFF_PENDING": {"ACTIVE", "BLOCKED"},
    "ACTIVE": {"EXIT_PENDING", "CLOSED", "BLOCKED", "RECOVERED"},
    "EXIT_PENDING": {"ACTIVE", "CLOSED", "BLOCKED", "RECOVERED"},
    "BLOCKED": {"ACTIVE", "CLOSED", "RECOVERED"},
    "CLOSED": set(), "RECOVERED": set(),
}
SCHEDULED_PRODUCTION_PROVENANCE = "SCHEDULED_PRODUCTION_AUTONOMOUS"


@dataclass(frozen=True, slots=True)
class CustodyProjection:
    custody_id: uuid.UUID
    custody_state: str
    authoritative_remaining_quantity: Decimal | None
    blockers: tuple[str, ...]
    sell_supervisor_connected: bool = False


def validate_custody_transition(*, current: str, target: str) -> None:
    if target not in ALLOWED_CUSTODY_TRANSITIONS.get(current, set()):
        raise InvalidRequestError(
            message="Invalid autonomous position custody transition",
            details={"current": current, "target": target},
        )


async def _scheduled_origin(
    *, db: AsyncSession, package: CanonicalPreviewPackage, mandate: AutonomousCapitalMandate,
) -> tuple[AutonomousCycleRun, AutonomousCycleRun]:
    campaign_cycles = list((await db.scalars(
        select(AutonomousCycleRun).where(
            AutonomousCycleRun.cycle_kind == "campaign",
            AutonomousCycleRun.decision_record_id == package.decision_record_id,
            AutonomousCycleRun.capital_campaign_id == package.campaign_id,
            AutonomousCycleRun.capital_campaign_version == package.campaign_version,
        ).limit(2)
    )).all())
    if len(campaign_cycles) != 1:
        raise InvalidRequestError(message="Scheduled campaign lineage is ambiguous", details={"match_count": len(campaign_cycles)})
    campaign_cycle = campaign_cycles[0]
    context = campaign_cycle.cycle_context if isinstance(campaign_cycle.cycle_context, dict) else {}
    raw_origin = context.get("originating_autonomous_cycle_id")
    try:
        origin_id = uuid.UUID(str(raw_origin))
    except (TypeError, ValueError):
        raise InvalidRequestError(message="Scheduled autonomous origin is missing") from None
    autonomous_cycle = await db.scalar(select(AutonomousCycleRun).where(
        AutonomousCycleRun.cycle_id == origin_id,
        AutonomousCycleRun.cycle_kind == "autonomous",
        AutonomousCycleRun.mandate_id == mandate.mandate_id,
        AutonomousCycleRun.mandate_version_id == package.mandate_version_id,
    ).limit(1))
    if autonomous_cycle is None:
        raise InvalidRequestError(message="Scheduled autonomous origin does not match production authority")
    return autonomous_cycle, campaign_cycle


async def compute_buy_order_acquired_quantity(
    *, db: AsyncSession, live_trading_profile_id: uuid.UUID,
    live_crypto_order_id: uuid.UUID, symbol: str,
) -> Decimal:
    """Accounting quantity attributable to exactly one BUY order lineage."""
    total = await db.scalar(select(func.coalesce(func.sum(case(
        (LiveAccountingRecord.side == "buy", LiveAccountingRecord.filled_quantity),
        else_=-LiveAccountingRecord.filled_quantity,
    )), Decimal("0"))).where(
        LiveAccountingRecord.live_trading_profile_id == live_trading_profile_id,
        LiveAccountingRecord.live_crypto_order_id == live_crypto_order_id,
        LiveAccountingRecord.symbol == symbol,
        LiveAccountingRecord.record_type.in_(QUANTITY_BEARING_RECORD_TYPES),
    ))
    return Decimal(str(total or 0))


async def establish_buy_custody(
    *, db: AsyncSession, claim: AutonomousExecutionClaim, observed_at: datetime,
) -> AutonomousPositionCustody:
    if claim.side != "BUY" or claim.live_order_id is None:
        raise InvalidRequestError(message="Custody handoff requires a reconciled autonomous BUY claim")
    existing = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.buy_claim_id == claim.claim_id,
    ).with_for_update().limit(1))
    if existing is not None:
        return existing

    package = await db.scalar(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.package_id == claim.package_id,
    ).limit(1))
    mandate = await db.scalar(select(AutonomousCapitalMandate).where(
        AutonomousCapitalMandate.mandate_id == claim.mandate_id,
    ).limit(1))
    if package is None or mandate is None:
        raise InvalidRequestError(message="Custody handoff lineage is incomplete")
    if package.side != "BUY" or package.authorization_source != "MANDATE" or mandate.purpose != "PRODUCTION":
        raise InvalidRequestError(message="Custody handoff is not ordinary-production mandate authority")
    proof = await db.scalar(select(ControlledProofRun.proof_id).where(or_(
        ControlledProofRun.package_id == package.package_id,
        ControlledProofRun.buy_live_crypto_order_id == claim.live_order_id,
    )).limit(1))
    market_identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
    if proof is not None or market_identity.get("controlled_proof_id") is not None:
        raise InvalidRequestError(message="Controlled Proof lineage cannot enter production custody")

    autonomous_cycle, campaign_cycle = await _scheduled_origin(db=db, package=package, mandate=mandate)
    reconciliation = await db.scalar(select(LiveReconciliationEvent).where(
        LiveReconciliationEvent.live_crypto_order_id == claim.live_order_id,
        LiveReconciliationEvent.reconciliation_status == "filled",
    ).order_by(desc(LiveReconciliationEvent.sequence_number)).limit(1))
    if reconciliation is None:
        raise InvalidRequestError(message="Authoritative filled reconciliation is missing")
    acquired = await compute_buy_order_acquired_quantity(
        db=db, live_trading_profile_id=claim.profile_id,
        live_crypto_order_id=claim.live_order_id, symbol=claim.product,
    )
    remaining = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=claim.profile_id, symbol=claim.product,
    )
    if acquired <= Decimal("0") or remaining <= Decimal("0"):
        raise InvalidRequestError(message="Positive authoritative BUY ownership is required for custody")
    if remaining != acquired:
        raise InvalidRequestError(
            message="Aggregate ownership cannot be attributed exclusively to the autonomous BUY",
            details={
                "buy_order_quantity": format(acquired, "f"),
                "aggregate_owned_quantity": format(remaining, "f"),
            },
        )

    custody = AutonomousPositionCustody(
        custody_state="ACTIVE",
        originating_autonomous_cycle_id=autonomous_cycle.cycle_id,
        originating_campaign_cycle_id=campaign_cycle.cycle_id,
        campaign_id=claim.campaign_id, campaign_version=claim.campaign_version,
        runtime_campaign_id=package.runtime_campaign_id,
        mandate_id=claim.mandate_id, mandate_version_id=claim.mandate_version_id,
        decision_record_id=package.decision_record_id,
        buy_package_id=package.package_id, buy_activation_id=claim.activation_id,
        buy_claim_id=claim.claim_id, buy_live_order_id=claim.live_order_id,
        buy_reconciliation_event_id=reconciliation.id,
        paper_account_id=claim.account_id, live_trading_profile_id=claim.profile_id,
        exchange_connection_id=claim.connection_id,
        provider=claim.provider, environment=claim.environment, product=claim.product,
        original_acquired_quantity=acquired, observed_remaining_quantity=remaining,
        quantity_authority="live_accounting_records", autonomous_origin=True,
        provenance_classification=SCHEDULED_PRODUCTION_PROVENANCE,
        proof_eligible=True, continuing_exit_authority_state="UNARMED",
        audit_metadata={
            "handoff": "atomic_before_buy_claim_terminalization",
            "quantity_authority": "live_accounting_records",
            "ownership_attribution": "aggregate_profile_product_equals_buy_order_quantity",
            "exclusive_scope": "live_trading_profile_id_product",
            "sell_supervisor_connected": False,
        },
        created_at=observed_at, updated_at=observed_at,
    )
    db.add(custody)
    await db.flush()
    db.add(AuditLog(
        actor="system:reconciliation", action="autonomous_position_custody.established",
        entity_type="autonomous_position_custody", entity_id=custody.custody_id,
        before_state=None,
        after_state={
            "custody_state": "ACTIVE", "buy_claim_id": str(claim.claim_id),
            "buy_live_order_id": str(claim.live_order_id),
            "remaining_quantity": format(remaining, "f"),
            "provenance_classification": SCHEDULED_PRODUCTION_PROVENANCE,
        },
    ))
    await db.flush()
    return custody


async def requires_production_custody_handoff(
    *, db: AsyncSession, claim: AutonomousExecutionClaim,
) -> bool:
    """Classify only mandate-authorized ordinary production claims.

    Controlled Proof and human/manual packages remain valid recovery paths,
    but they neither create nor block on this autonomous-proof aggregate.
    """
    if claim.side != "BUY":
        return False
    package = await db.scalar(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.package_id == claim.package_id,
    ).limit(1))
    mandate = await db.scalar(select(AutonomousCapitalMandate).where(
        AutonomousCapitalMandate.mandate_id == claim.mandate_id,
    ).limit(1))
    if package is None or mandate is None:
        raise InvalidRequestError(message="BUY claim authority cannot be classified for custody")
    if package.authorization_source != "MANDATE" or mandate.purpose != "PRODUCTION":
        return False
    proof = await db.scalar(select(ControlledProofRun.proof_id).where(or_(
        ControlledProofRun.package_id == package.package_id,
        ControlledProofRun.buy_live_crypto_order_id == claim.live_order_id,
    )).limit(1))
    market_identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
    return proof is None and market_identity.get("controlled_proof_id") is None


async def discover_nonterminal_custodies(
    *, db: AsyncSession, provider: str | None = None,
    environment: str | None = None, product: str | None = None,
) -> list[CustodyProjection]:
    statement = select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_state.in_(NONTERMINAL_CUSTODY_STATES),
    )
    if provider is not None:
        statement = statement.where(AutonomousPositionCustody.provider == provider)
    if environment is not None:
        statement = statement.where(AutonomousPositionCustody.environment == environment)
    if product is not None:
        statement = statement.where(AutonomousPositionCustody.product == product)
    rows = list((await db.scalars(statement.order_by(
        AutonomousPositionCustody.created_at.asc(),
    ))).all())
    projections: list[CustodyProjection] = []
    for row in rows:
        blockers: list[str] = []
        remaining: Decimal | None = None
        try:
            remaining = await compute_signed_owned_quantity(
                db=db, live_trading_profile_id=row.live_trading_profile_id, symbol=row.product,
            )
            if remaining < 0:
                blockers.append("authoritative_quantity_negative")
            if row.custody_state in {"ACTIVE", "EXIT_PENDING", "BLOCKED"} and remaining <= 0:
                blockers.append("custody_open_without_positive_ownership")
            if remaining > row.original_acquired_quantity:
                blockers.append("ownership_exceeds_custodied_acquisition")
        except Exception:
            blockers.append("authoritative_quantity_unavailable")
        projections.append(CustodyProjection(
            custody_id=row.custody_id, custody_state=row.custody_state,
            authoritative_remaining_quantity=remaining, blockers=tuple(blockers),
        ))
    return projections


async def permanently_disqualify_custody(
    *, db: AsyncSession, custody_id: uuid.UUID, reason: str, actor: str,
    observed_at: datetime | None = None,
) -> AutonomousPositionCustody:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise InvalidRequestError(message="Custody disqualification requires a reason")
    row = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == custody_id,
    ).with_for_update().limit(1))
    if row is None:
        raise InvalidRequestError(message="Autonomous position custody not found")
    if not row.proof_eligible:
        return row
    now = observed_at or datetime.now(timezone.utc)
    row.proof_eligible = False
    row.disqualification_reason = normalized_reason
    row.disqualified_at = now
    row.updated_at = now
    db.add(AuditLog(
        actor=actor, action="autonomous_position_custody.proof_disqualified",
        entity_type="autonomous_position_custody", entity_id=row.custody_id,
        before_state={"custody_state": row.custody_state, "proof_eligible": True},
        after_state={
            "custody_state": row.custody_state, "proof_eligible": False,
            "disqualification_reason": normalized_reason,
        },
    ))
    await db.flush()
    return row


async def close_custody_if_unowned(
    *, db: AsyncSession, custody_id: uuid.UUID, actor: str,
    observed_at: datetime | None = None,
) -> AutonomousPositionCustody:
    """Close responsibility only after accounting proves quantity is zero."""
    row = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == custody_id,
    ).with_for_update().limit(1))
    if row is None:
        raise InvalidRequestError(message="Autonomous position custody not found")
    if row.custody_state in TERMINAL_CUSTODY_STATES:
        return row
    remaining = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=row.live_trading_profile_id, symbol=row.product,
    )
    if remaining != Decimal("0"):
        raise InvalidRequestError(
            message="Custody cannot close while authoritative inventory remains",
            details={"authoritative_remaining_quantity": format(remaining, "f")},
        )
    validate_custody_transition(current=row.custody_state, target="CLOSED")
    now = observed_at or datetime.now(timezone.utc)
    before = row.custody_state
    row.custody_state = "CLOSED"
    row.observed_remaining_quantity = Decimal("0")
    row.terminal_at = now
    row.updated_at = now
    db.add(AuditLog(
        actor=actor, action="autonomous_position_custody.closed_unowned",
        entity_type="autonomous_position_custody", entity_id=row.custody_id,
        before_state={"custody_state": before},
        after_state={"custody_state": "CLOSED", "authoritative_remaining_quantity": "0"},
    ))
    await db.flush()
    return row


async def discover_uncustodied_reconciled_buys(
    *, db: AsyncSession, provider: str | None = None,
    environment: str | None = None, product: str | None = None,
) -> list[dict[str, Any]]:
    """Fail-closed audit for legacy/interrupted positive BUY ownership."""
    statement = (
        select(AutonomousExecutionClaim)
        .outerjoin(AutonomousPositionCustody, AutonomousPositionCustody.buy_claim_id == AutonomousExecutionClaim.claim_id)
        .where(
            AutonomousExecutionClaim.side == "BUY",
            AutonomousExecutionClaim.claim_status.in_(["BUY_RECONCILED", "POSITION_OPENED"]),
            AutonomousPositionCustody.custody_id.is_(None),
        )
    )
    if provider is not None:
        statement = statement.where(AutonomousExecutionClaim.provider == provider)
    if environment is not None:
        statement = statement.where(AutonomousExecutionClaim.environment == environment)
    if product is not None:
        statement = statement.where(AutonomousExecutionClaim.product == product)
    claims = list((await db.scalars(statement.order_by(
        AutonomousExecutionClaim.created_at.asc(),
    ))).all())
    missing: list[dict[str, Any]] = []
    for claim in claims:
        try:
            if not await requires_production_custody_handoff(db=db, claim=claim):
                continue
        except Exception:
            missing.append({"claim_id": str(claim.claim_id), "reason": "custody_authority_classification_unavailable"})
            continue
        try:
            remaining = await compute_signed_owned_quantity(
                db=db, live_trading_profile_id=claim.profile_id, symbol=claim.product,
            )
        except Exception:
            missing.append({"claim_id": str(claim.claim_id), "reason": "authoritative_quantity_unavailable"})
            continue
        if remaining > 0:
            missing.append({
                "claim_id": str(claim.claim_id), "live_order_id": str(claim.live_order_id),
                "remaining_quantity": format(remaining, "f"),
                "reason": "positive_autonomous_ownership_without_custody",
            })
    return missing


async def custody_status(
    *, db: AsyncSession, provider: str | None = None,
    environment: str | None = None, product: str | None = None,
) -> dict[str, Any]:
    statement = select(AutonomousPositionCustody)
    if provider is not None:
        statement = statement.where(AutonomousPositionCustody.provider == provider)
    if environment is not None:
        statement = statement.where(AutonomousPositionCustody.environment == environment)
    if product is not None:
        statement = statement.where(AutonomousPositionCustody.product == product)
    rows = list((await db.scalars(statement.order_by(
        AutonomousPositionCustody.created_at.desc(), AutonomousPositionCustody.custody_id.desc(),
    ))).all())
    projections = {
        item.custody_id: item
        for item in await discover_nonterminal_custodies(
            db=db, provider=provider, environment=environment, product=product,
        )
    }
    items = []
    for row in rows:
        projection = projections.get(row.custody_id)
        raw_metadata = getattr(row, "audit_metadata", None)
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        exit_evaluation = metadata.get("latest_exit_evaluation")
        if not isinstance(exit_evaluation, dict):
            exit_evaluation = {}
        campaign = await db.scalar(select(CapitalCampaign).where(
            CapitalCampaign.uuid == row.runtime_campaign_id,
        ).limit(1))
        mandate = await db.scalar(select(AutonomousCapitalMandate).where(
            AutonomousCapitalMandate.mandate_id == row.mandate_id,
        ).limit(1))
        authority = await db.scalar(select(AutonomousPositionExitAuthority).where(
            AutonomousPositionExitAuthority.custody_id == row.custody_id,
        ).order_by(
            AutonomousPositionExitAuthority.authority_version.desc(),
            AutonomousPositionExitAuthority.created_at.desc(),
        ).limit(1))
        items.append({
            "custody_id": str(row.custody_id), "state": row.custody_state,
            "originating_autonomous_cycle_id": str(row.originating_autonomous_cycle_id),
            "originating_campaign_cycle_id": str(row.originating_campaign_cycle_id),
            "buy_claim_id": str(row.buy_claim_id), "buy_order_id": str(row.buy_live_order_id),
            "buy_reconciliation_event_id": str(row.buy_reconciliation_event_id),
            "campaign_id": str(row.campaign_id), "campaign_version": row.campaign_version,
            "mandate_id": str(row.mandate_id), "provider": row.provider,
            "entry_campaign_status": None if campaign is None else campaign.status,
            "entry_mandate_status": None if mandate is None else mandate.status,
            "environment": row.environment, "product": row.product,
            "original_acquired_quantity": format(row.original_acquired_quantity, "f"),
            "authoritative_remaining_quantity": None if projection is None or projection.authoritative_remaining_quantity is None else format(projection.authoritative_remaining_quantity, "f"),
            "quantity_authority": row.quantity_authority,
            "autonomous_provenance": row.provenance_classification,
            "proof_eligible": row.proof_eligible,
            "disqualification_reason": row.disqualification_reason,
            "latest_exit_evaluation_at": row.latest_exit_evaluation_at,
            "next_exit_evaluation_at": row.next_exit_evaluation_at,
            "active_sell_decision_id": row.active_sell_decision_id,
            "active_sell_package_id": row.active_sell_package_id,
            "active_sell_claim_id": row.active_sell_claim_id,
            "active_sell_order_id": row.active_sell_order_id,
            "continuing_exit_authority": row.continuing_exit_authority_state,
            "sell_supervisor_connected": False,
            "evaluation_scheduler_connected": row.latest_exit_evaluation_at is not None,
            "evaluation_disposition": exit_evaluation.get("disposition"),
            "price": exit_evaluation.get("price"),
            "price_observed_at": exit_evaluation.get("price_observed_at"),
            "price_fresh": bool(exit_evaluation.get("price_fresh", False)),
            "estimated_net_exit_result": exit_evaluation.get("estimated_net_exit_result"),
            "profitable_exit": bool(exit_evaluation.get("profitable_exit", False)),
            "stop_loss_triggered": bool(exit_evaluation.get("stop_loss_triggered", False)),
            "maximum_hold_exceeded": bool(exit_evaluation.get("maximum_hold_exceeded", False)),
            "mandatory_safety_exit": bool(exit_evaluation.get("mandatory_safety_exit", False)),
            "evaluation_reason_codes": exit_evaluation.get("reason_codes", []),
            "automatic_sell_execution": False,
            "continuing_authority_id": None if authority is None else str(authority.authority_id),
            "continuing_authority_state": None if authority is None else authority.authority_state,
            "continuing_authority_version": None if authority is None else authority.authority_version,
            "continuing_authority_classification": None if authority is None else authority.classification,
            "continuing_authority_evaluation_at": None if authority is None else authority.evaluation_at,
            "authorized_maximum_sell_quantity": None if authority is None else format(authority.maximum_sell_quantity, "f"),
            "authority_issued_at": None if authority is None else authority.issued_at,
            "authority_expires_at": None if authority is None else authority.expires_at,
            "authority_reserved_at": None if authority is None else authority.reserved_at,
            "authority_revoked_at": None if authority is None else authority.revoked_at,
            "authority_consumed_at": None if authority is None else authority.consumed_at,
            "authority_policy_evidence": None if authority is None else authority.policy_evidence,
            "authority_risk_evidence": None if authority is None else authority.risk_evidence,
            "authority_blockers": [] if authority is None else authority.blockers,
            "sell_decision_construction_connected": False,
            "blockers": [] if projection is None else list(projection.blockers),
            "positive_inventory_supervised": bool(
                projection is not None
                and projection.authoritative_remaining_quantity is not None
                and projection.authoritative_remaining_quantity > 0
            ),
            "ownership_attribution_ambiguous": bool(
                projection is not None
                and "ownership_exceeds_custodied_acquisition" in projection.blockers
            ),
        })
    uncustodied = await discover_uncustodied_reconciled_buys(
        db=db, provider=provider, environment=environment, product=product,
    )
    return {
        "verdict": "SELL_SUPERVISION_NOT_IMPLEMENTED",
        "custody_count": len(items), "items": items,
        "uncustodied_positive_buy_count": len(uncustodied),
        "uncustodied_positive_buys": uncustodied,
        "read_only": True, "automatic_sell_submission": False,
    }
