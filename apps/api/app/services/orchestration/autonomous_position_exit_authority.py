from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.services.live.position_quantity import compute_signed_owned_quantity

AUTHORITY_TTL = timedelta(minutes=15)
RESERVATION_TTL = timedelta(minutes=5)
ACTIVE_STATES = ("ARMED", "RESERVED")
NONTERMINAL_CUSTODY_STATES = ("HANDOFF_PENDING", "ACTIVE", "EXIT_PENDING", "BLOCKED")
ALLOWED_TRANSITIONS = {
    "UNARMED": {"ARMED", "BLOCKED"}, "ARMED": {"RESERVED", "REVOKED", "EXPIRED", "CONSUMED"},
    "RESERVED": {"ARMED", "REVOKED", "EXPIRED", "CONSUMED"},
    "BLOCKED": {"REVOKED", "EXPIRED"}, "CONSUMED": set(), "REVOKED": set(), "EXPIRED": set(),
}


@dataclass(frozen=True, slots=True)
class AuthorityIssuanceOutcome:
    discovered: int
    armed: int
    replayed: int
    blocked: int


def validate_authority_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidRequestError(message="Invalid continuing exit authority transition", details={"current": current, "target": target})


def _evaluation(row: AutonomousPositionCustody) -> dict[str, Any]:
    metadata = row.audit_metadata if isinstance(row.audit_metadata, dict) else {}
    value = metadata.get("latest_exit_evaluation")
    return value if isinstance(value, dict) else {}


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


async def issue_exit_authority(
    *, db: AsyncSession, custody_id: uuid.UUID, now: datetime | None = None,
) -> tuple[AutonomousPositionExitAuthority | None, tuple[str, ...], bool]:
    observed_at = now or datetime.now(timezone.utc)
    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == custody_id,
    ).with_for_update().limit(1))
    if custody is None:
        return None, ("custody_unavailable",), False
    evaluation = _evaluation(custody)
    evaluation_hash = _digest(evaluation)
    latest = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.custody_id == custody.custody_id,
    ).order_by(AutonomousPositionExitAuthority.authority_version.desc()).limit(1))
    if latest is not None and latest.evaluation_integrity_hash == evaluation_hash:
        return latest, tuple(latest.blockers or ()), True

    blockers: list[str] = []
    evaluated_at = _parse_time(evaluation.get("evaluated_at"))
    quantity_text = evaluation.get("authoritative_remaining_quantity")
    try:
        evaluated_quantity = Decimal(str(quantity_text))
    except Exception:
        evaluated_quantity = Decimal("0")
        blockers.append("evaluation_quantity_invalid")
    authoritative_quantity = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product,
    )
    if custody.custody_state not in NONTERMINAL_CUSTODY_STATES:
        blockers.append("custody_terminal")
    if evaluation.get("disposition") != "EXIT_RECOMMENDED":
        blockers.append("evaluation_not_exit_recommended")
    if evaluated_at is None or observed_at - evaluated_at > AUTHORITY_TTL or evaluated_at > observed_at:
        blockers.append("evaluation_stale_or_invalid")
    if evaluation.get("price_fresh") is not True:
        blockers.append("market_evidence_not_fresh")
    if not (evaluation.get("profitable_exit") is True or evaluation.get("mandatory_safety_exit") is True):
        blockers.append("exit_basis_unavailable")
    if not evaluation.get("policy_id") or not evaluation.get("policy_version"):
        blockers.append("policy_identity_unavailable")
    if evaluation.get("campaign_status") is None or evaluation.get("mandate_status") is None:
        blockers.append("entry_authority_lineage_status_unavailable")
    if evaluation.get("policy_conflicts") and evaluation.get("mandatory_safety_exit") is not True:
        blockers.append("policy_conflict_unresolved")
    if not custody.proof_eligible and evaluation.get("mandatory_safety_exit") is not True:
        blockers.append("proof_ineligible_requires_protective_safety_exit")
    if evaluation.get("reason_codes") and any(code in {
        "authoritative_quantity_unavailable", "position_quantity_ambiguous", "position_snapshot_ambiguous",
        "buy_claim_scope_ambiguous", "buy_order_scope_ambiguous", "unresolved_sell_execution_reference",
    } for code in evaluation["reason_codes"]):
        blockers.append("evaluation_contains_authority_blocker")
    if authoritative_quantity <= 0 or evaluated_quantity != authoritative_quantity:
        blockers.append("authoritative_quantity_changed_or_ambiguous")
    if custody.active_sell_claim_id is not None or custody.active_sell_order_id is not None:
        blockers.append("unresolved_sell_reference")
    active = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.custody_id == custody.custody_id,
        AutonomousPositionExitAuthority.authority_state.in_(ACTIVE_STATES),
    ).with_for_update().limit(1))
    if active is not None:
        blockers.append("active_authority_exists")

    if authoritative_quantity <= 0:
        db.add(AuditLog(
            actor="system:autonomous_custody_authority", action="autonomous_position_exit_authority.blocked",
            entity_type="autonomous_position_custody", entity_id=custody.custody_id,
            before_state=None,
            after_state={"blockers": sorted(set(blockers)), "buy_forbidden": True,
                         "increased_exposure_forbidden": True, "sell_construction_connected": False},
        ))
        await db.flush()
        return None, tuple(sorted(set(blockers))), False

    classification = "PROOF_ELIGIBLE_AUTONOMOUS" if custody.proof_eligible else "NONQUALIFYING_PROTECTIVE_EXIT"
    state = "BLOCKED" if blockers else "ARMED"
    authority = AutonomousPositionExitAuthority(
        authority_version=1 if latest is None else latest.authority_version + 1,
        authority_state=state, custody_id=custody.custody_id,
        live_trading_profile_id=custody.live_trading_profile_id, paper_account_id=custody.paper_account_id,
        exchange_connection_id=custody.exchange_connection_id, provider=custody.provider,
        environment=custody.environment, product=custody.product,
        originating_buy_claim_id=custody.buy_claim_id,
        originating_reconciliation_event_id=custody.buy_reconciliation_event_id,
        provenance_classification=custody.provenance_classification,
        proof_eligible=custody.proof_eligible, classification=classification,
        evaluation_at=evaluated_at or observed_at, evaluation_integrity_hash=evaluation_hash,
        authoritative_quantity_at_issuance=authoritative_quantity,
        maximum_sell_quantity=authoritative_quantity,
        side="SELL", exposure_effect="REDUCE_ONLY", buy_forbidden=True,
        increased_exposure_forbidden=True,
        policy_evidence={
            "policy_id": evaluation.get("policy_id"), "policy_version": evaluation.get("policy_version"),
            "minimum_net_profit_to_exit": evaluation.get("minimum_net_profit_to_exit"),
            "dust_threshold": evaluation.get("dust_threshold"), "policy_conflicts": evaluation.get("policy_conflicts", []),
        },
        risk_evidence={
            "authority": "owned_position_reduction_only", "new_exposure_permitted": False,
            "buy_permitted": False, "sell_construction_connected": False, "provider_submission_connected": False,
        },
        blockers=sorted(set(blockers)), issued_at=observed_at, expires_at=observed_at + AUTHORITY_TTL,
    )
    db.add(authority)
    custody.continuing_exit_authority_state = state
    custody.updated_at = observed_at
    await db.flush()
    db.add(AuditLog(
        actor="system:autonomous_custody_authority", action=f"autonomous_position_exit_authority.{state.lower()}",
        entity_type="autonomous_position_exit_authority", entity_id=authority.authority_id,
        before_state=None,
        after_state={
            "authority_state": state, "custody_id": str(custody.custody_id), "classification": classification,
            "evaluation_integrity_hash": evaluation_hash, "authoritative_quantity": format(authoritative_quantity, "f"),
            "maximum_sell_quantity": format(authority.maximum_sell_quantity, "f"), "blockers": authority.blockers,
            "buy_forbidden": True, "increased_exposure_forbidden": True,
            "sell_construction_connected": False, "provider_submission_connected": False,
        },
    ))
    await db.flush()
    return authority, tuple(authority.blockers), False


async def issue_due_exit_authorities(
    *, db: AsyncSession, now: datetime | None = None, limit: int = 25,
) -> AuthorityIssuanceOutcome:
    observed_at = now or datetime.now(timezone.utc)
    rows = list((await db.scalars(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_state.in_(NONTERMINAL_CUSTODY_STATES),
        AutonomousPositionCustody.latest_exit_evaluation_at.is_not(None),
    ).order_by(AutonomousPositionCustody.latest_exit_evaluation_at.asc(), AutonomousPositionCustody.custody_id.asc())
        .limit(limit).with_for_update(skip_locked=True))).all())
    armed = replayed = blocked = 0
    for row in rows:
        authority, blockers, replay = await issue_exit_authority(db=db, custody_id=row.custody_id, now=observed_at)
        replayed += int(replay)
        armed += int(authority is not None and authority.authority_state == "ARMED" and not replay)
        blocked += int(bool(blockers) and not replay)
    return AuthorityIssuanceOutcome(len(rows), armed, replayed, blocked)


async def revalidate_active_exit_authorities(
    *, db: AsyncSession, now: datetime | None = None, limit: int = 25,
) -> int:
    observed_at = now or datetime.now(timezone.utc)
    rows = list((await db.scalars(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_state.in_(ACTIVE_STATES),
    ).order_by(AutonomousPositionExitAuthority.expires_at.asc(), AutonomousPositionExitAuthority.authority_id.asc())
        .limit(limit).with_for_update(skip_locked=True))).all())
    changed = 0
    for row in rows:
        custody = await db.scalar(select(AutonomousPositionCustody).where(
            AutonomousPositionCustody.custody_id == row.custody_id,
        ).with_for_update().limit(1))
        target = reason = None
        quantity = Decimal("0") if custody is None else await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product,
        )
        if observed_at >= row.expires_at:
            target, reason = "EXPIRED", "authority_expired"
        elif (row.authority_state == "RESERVED" and getattr(row, "reserved_package_id", None) is None
              and row.reservation_expires_at is not None and observed_at >= row.reservation_expires_at):
            target, reason = "ARMED", "abandoned_reservation_recovered"
        elif custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES:
            target, reason = "REVOKED", "custody_terminal_or_unavailable"
        elif quantity == 0:
            target, reason = "CONSUMED", "authoritative_quantity_zero"
        elif quantity < row.maximum_sell_quantity:
            target, reason = "REVOKED", "authoritative_quantity_reduced"
        elif custody.proof_eligible != row.proof_eligible:
            target, reason = "REVOKED", "proof_classification_changed"
        elif _digest(_evaluation(custody)) != row.evaluation_integrity_hash:
            target, reason = "REVOKED", "evaluation_superseded"
            row.superseded_at = observed_at
        if target is None:
            continue
        validate_authority_transition(row.authority_state, target)
        before = row.authority_state; row.authority_state = target; row.updated_at = observed_at
        if target == "EXPIRED": row.expired_at = observed_at
        if target == "REVOKED": row.revoked_at = observed_at
        if target == "CONSUMED": row.consumed_at = observed_at
        if target == "ARMED":
            row.reserved_at = None; row.reservation_expires_at = None
            row.reserved_decision_id = None; row.reserved_package_id = None
        if custody is not None: custody.continuing_exit_authority_state = target
        db.add(AuditLog(
            actor="system:autonomous_custody_authority", action=f"autonomous_position_exit_authority.{target.lower()}",
            entity_type="autonomous_position_exit_authority", entity_id=row.authority_id,
            before_state={"authority_state": before}, after_state={"authority_state": target, "reason": reason},
        ))
        changed += 1
    await db.flush()
    return changed


async def revoke_exit_authority(
    *, db: AsyncSession, authority_id: uuid.UUID, reason: str, actor: str,
    now: datetime | None = None,
) -> AutonomousPositionExitAuthority:
    if not reason.strip():
        raise InvalidRequestError(message="Authority revocation requires a reason")
    row = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == authority_id,
    ).with_for_update().limit(1))
    if row is None or row.authority_state not in ACTIVE_STATES:
        raise InvalidRequestError(message="Active continuing exit authority not found")
    validate_authority_transition(row.authority_state, "REVOKED")
    observed_at = now or datetime.now(timezone.utc)
    before = row.authority_state; row.authority_state = "REVOKED"; row.revoked_at = observed_at; row.updated_at = observed_at
    db.add(AuditLog(actor=actor, action="autonomous_position_exit_authority.revoked",
        entity_type="autonomous_position_exit_authority", entity_id=row.authority_id,
        before_state={"authority_state": before}, after_state={"authority_state": "REVOKED", "reason": reason.strip()}))
    await db.flush(); return row


async def reserve_exit_authority(
    *, db: AsyncSession, authority_id: uuid.UUID, side: str, quantity: Decimal,
    custody_id: uuid.UUID, profile_id: uuid.UUID, account_id: uuid.UUID,
    connection_id: uuid.UUID, provider: str, environment: str, product: str,
    now: datetime | None = None,
) -> AutonomousPositionExitAuthority:
    row = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == authority_id,
    ).with_for_update().limit(1))
    if row is None or row.authority_state != "ARMED":
        raise InvalidRequestError(message="Continuing exit authority is not armable for reservation")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at >= row.expires_at:
        raise InvalidRequestError(message="Continuing exit authority expired")
    if side != "SELL" or quantity <= 0 or quantity > row.maximum_sell_quantity:
        raise InvalidRequestError(message="Continuing authority permits only bounded SELL reduction")
    if (custody_id, profile_id, account_id, connection_id, provider, environment, product) != (
        row.custody_id, row.live_trading_profile_id, row.paper_account_id, row.exchange_connection_id,
        row.provider, row.environment, row.product,
    ):
        raise InvalidRequestError(message="Continuing authority scope mismatch")
    validate_authority_transition(row.authority_state, "RESERVED")
    row.authority_state = "RESERVED"; row.reserved_at = observed_at
    row.reservation_expires_at = observed_at + RESERVATION_TTL; row.updated_at = observed_at
    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == row.custody_id,
    ).with_for_update().limit(1))
    if custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES:
        raise InvalidRequestError(message="Custody is not available for authority reservation")
    custody.continuing_exit_authority_state = "RESERVED"
    db.add(AuditLog(
        actor="system:autonomous_custody_authority", action="autonomous_position_exit_authority.reserved",
        entity_type="autonomous_position_exit_authority", entity_id=row.authority_id,
        before_state={"authority_state": "ARMED"},
        after_state={"authority_state": "RESERVED", "quantity": format(quantity, "f"),
                     "reservation_expires_at": row.reservation_expires_at.isoformat(),
                     "buy_forbidden": True, "increased_exposure_forbidden": True,
                     "sell_construction_connected": False, "provider_submission_connected": False},
    ))
    await db.flush()
    return row
