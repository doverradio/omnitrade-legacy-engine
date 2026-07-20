from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.strategy import Strategy
from app.services.mandates.lifecycle import _find_audit_by_idempotency
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_IDENTITY

_ACTIVATION_AUDIT_ACTION = "capital_campaign_definition.aggregator_activation_migration"
_CONTINUITY_BLOCKING_STATES = ("READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED")


@dataclass(frozen=True, slots=True)
class AggregatorActivationCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class AggregatorActivationReadiness:
    ready: bool
    blockers: list[str]
    checks: list[AggregatorActivationCheck]
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AggregatorActivationResult:
    changed: bool
    idempotent: bool
    audit_created: bool
    before: dict[str, Any]
    after: dict[str, Any]
    readiness: AggregatorActivationReadiness


def _is_reinvestment_disabled(value: Any) -> bool:
    if value is None:
        return True
    try:
        return Decimal(str(value)) == Decimal("0")
    except Exception:
        return False


async def _load_definition_for_campaign(
    *, db: AsyncSession, campaign_id: UUID, campaign_version: int
) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == campaign_version)
        .limit(1)
    )


async def _snapshot_campaign_aggregator_activation_state(
    *, db: AsyncSession, campaign_id: UUID, campaign_version: int
) -> dict[str, Any]:
    """Read-only. Never writes. Reports whether this campaign definition's
    strategy-continuity identity is already pinned to the canonical aggregate
    identity, whether compounding is already disabled, and whether any
    CanonicalPreviewPackage in a continuity-checked state was issued under a
    different (pre-aggregator) strategy identity."""
    definition = await _load_definition_for_campaign(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    if definition is None:
        return {"definition_found": False}

    metadata = dict(definition.metadata_evidence or {})
    compounding = dict(definition.compounding_policy or {})
    current_pin = metadata.get("selected_strategy_identity")
    current_reinvestment = compounding.get("reinvestment_percentage")
    already_pinned = current_pin == AGGREGATE_STRATEGY_IDENTITY
    already_disabled = _is_reinvestment_disabled(current_reinvestment)

    active_packages = list(
        (
            await db.execute(
                select(CanonicalPreviewPackage)
                .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                .where(CanonicalPreviewPackage.package_state.in_(_CONTINUITY_BLOCKING_STATES))
                .order_by(desc(CanonicalPreviewPackage.updated_at))
            )
        )
        .scalars()
        .all()
    )

    package_snapshots: list[dict[str, Any]] = []
    continuity_conflict_risk = False
    for package in active_packages:
        strategy = await db.scalar(select(Strategy).where(Strategy.id == package.strategy_id).limit(1))
        slug = strategy.slug if strategy is not None else None
        historical_identity = f"{slug}@{package.strategy_version}" if slug else None
        mismatched = historical_identity != AGGREGATE_STRATEGY_IDENTITY
        # Once pinned, the continuity check short-circuits on the metadata
        # pin and never reaches the package lookup at all -- so a mismatched
        # historical package is only a live conflict risk while unpinned.
        if not already_pinned:
            continuity_conflict_risk = continuity_conflict_risk or mismatched
        package_snapshots.append(
            {
                "package_id": str(package.package_id),
                "package_state": package.package_state,
                "historical_strategy_identity": historical_identity,
                "matches_aggregate_identity": not mismatched,
            }
        )

    return {
        "definition_found": True,
        "definition_id": definition.id,
        "current_selected_strategy_identity": current_pin,
        "current_reinvestment_percentage": None if current_reinvestment is None else str(current_reinvestment),
        "already_pinned_to_aggregate_identity": already_pinned,
        "already_compounding_disabled": already_disabled,
        "active_packages_in_continuity_states": package_snapshots,
        "continuity_conflict_risk_if_deployed_now": continuity_conflict_risk,
    }


async def inspect_campaign_aggregator_activation(
    *, db: AsyncSession, campaign_id: UUID, campaign_version: int
) -> AggregatorActivationReadiness:
    """Read-only. Never writes to the database."""
    snapshot = await _snapshot_campaign_aggregator_activation_state(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    checks: list[AggregatorActivationCheck] = []
    blockers: list[str] = []

    if not snapshot.get("definition_found"):
        checks.append(
            AggregatorActivationCheck(
                code="campaign_definition_exists",
                passed=False,
                detail="no capital_campaign_definitions row for this campaign_id/version",
            )
        )
        blockers.append("campaign_definition_not_found")
        return AggregatorActivationReadiness(ready=False, blockers=blockers, checks=checks, snapshot=snapshot)

    checks.append(AggregatorActivationCheck(code="campaign_definition_exists", passed=True, detail="definition row found"))

    already_applied = bool(snapshot["already_pinned_to_aggregate_identity"] and snapshot["already_compounding_disabled"])
    checks.append(
        AggregatorActivationCheck(
            code="not_already_applied",
            passed=not already_applied,
            detail="migration already applied" if already_applied else "migration not yet applied",
        )
    )
    # Informational, not a blocker: the migration itself is what resolves the
    # continuity conflict, so its presence before execution is expected.
    checks.append(
        AggregatorActivationCheck(
            code="continuity_conflict_identified",
            passed=True,
            detail=f"continuity_conflict_risk_if_deployed_now={snapshot['continuity_conflict_risk_if_deployed_now']}",
        )
    )

    ready = not already_applied
    if already_applied:
        blockers.append("already_applied")
    return AggregatorActivationReadiness(ready=ready, blockers=blockers, checks=checks, snapshot=snapshot)


async def execute_campaign_aggregator_activation(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    campaign_version: int,
    actor: str,
    reason: str,
    idempotency_key: str,
    confirm: bool,
) -> AggregatorActivationResult:
    """Pins capital_campaign_definitions.metadata_evidence.selected_strategy_identity
    to the canonical multi-strategy aggregate identity and sets
    compounding_policy.reinvestment_percentage to "0", ahead of deploying the
    governed Strategy Decision Aggregator. Never touches any
    CanonicalPreviewPackage row -- an existing ACTIVATED package (which may be
    governing a real open position) is left exactly as-is. Idempotent: a
    repeated call with the same idempotency_key returns the original result
    without writing again; a repeated call whose target state already matches
    the desired outcome (by any caller) is also a no-op."""
    readiness = await inspect_campaign_aggregator_activation(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    if not confirm:
        raise PermissionError("confirm=true is required")

    existing_audit = await _find_audit_by_idempotency(
        db=db,
        entity_type="capital_campaign_definition",
        action=_ACTIVATION_AUDIT_ACTION,
        idempotency_key=idempotency_key,
    )
    if existing_audit is not None:
        return AggregatorActivationResult(
            changed=False,
            idempotent=True,
            audit_created=False,
            before=existing_audit.before_state or {},
            after=existing_audit.after_state or {},
            readiness=readiness,
        )

    if not readiness.snapshot.get("definition_found"):
        raise LookupError("campaign definition not found for this campaign_id/version")

    if readiness.snapshot["already_pinned_to_aggregate_identity"] and readiness.snapshot["already_compounding_disabled"]:
        before = {
            "selected_strategy_identity": readiness.snapshot["current_selected_strategy_identity"],
            "reinvestment_percentage": readiness.snapshot["current_reinvestment_percentage"],
        }
        return AggregatorActivationResult(changed=False, idempotent=True, audit_created=False, before=before, after=before, readiness=readiness)

    definition = await _load_definition_for_campaign(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    if definition is None:
        raise LookupError("campaign definition not found for this campaign_id/version")

    before_metadata = dict(definition.metadata_evidence or {})
    before_compounding = dict(definition.compounding_policy or {})
    before = {
        "selected_strategy_identity": before_metadata.get("selected_strategy_identity"),
        "reinvestment_percentage": None if before_compounding.get("reinvestment_percentage") is None else str(before_compounding.get("reinvestment_percentage")),
    }

    new_metadata = dict(before_metadata)
    new_metadata["selected_strategy_identity"] = AGGREGATE_STRATEGY_IDENTITY
    new_compounding = dict(before_compounding)
    new_compounding["reinvestment_percentage"] = "0"
    definition.metadata_evidence = new_metadata
    definition.compounding_policy = new_compounding
    definition.updated_at = datetime.now(timezone.utc)

    after = {
        "selected_strategy_identity": AGGREGATE_STRATEGY_IDENTITY,
        "reinvestment_percentage": "0",
        "reason": reason,
        "idempotency_key": idempotency_key,
    }
    db.add(
        AuditLog(
            actor=actor,
            action=_ACTIVATION_AUDIT_ACTION,
            entity_type="capital_campaign_definition",
            entity_id=campaign_id,
            before_state={**before, "campaign_version": campaign_version},
            after_state=after,
        )
    )
    await db.commit()

    post_snapshot = await _snapshot_campaign_aggregator_activation_state(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    post_readiness = AggregatorActivationReadiness(ready=False, blockers=["already_applied"], checks=readiness.checks, snapshot=post_snapshot)

    return AggregatorActivationResult(changed=True, idempotent=False, audit_created=True, before=before, after=after, readiness=post_readiness)


async def fetch_campaign_aggregator_activation_audit(
    *, db: AsyncSession, campaign_id: UUID, limit: int = 20
) -> list[dict[str, Any]]:
    """Read-only. Never writes to the database."""
    records = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "capital_campaign_definition")
                .where(AuditLog.action == _ACTIVATION_AUDIT_ACTION)
                .where(AuditLog.entity_id == campaign_id)
                .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "audit_id": record.id,
            "actor": record.actor,
            "created_at": record.created_at.isoformat(),
            "before_state": record.before_state,
            "after_state": record.after_state,
        }
        for record in records
    ]
