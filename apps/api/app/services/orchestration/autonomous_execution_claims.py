from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError
from app.core.redaction import redact_message_for_diagnostics
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.capital_campaign import CapitalCampaign
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.risk_kill_switch import RiskKillSwitch
from app.services.controlled_proof.service import _ACTIVE_STATES as _CONTROLLED_PROOF_ACTIVE_STATES
from app.services.live.position_quantity import QUANTITY_BEARING_RECORD_TYPES
from app.services.orchestration.autonomous_order_preparation import (
    execute_prepared_autonomous_claim,
    prepare_autonomous_claimed_order,
)
from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

logger = logging.getLogger(__name__)

_OPEN_ORDER_STATES = {"PENDING_CONFIRMATION", "VALIDATING", "SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED", "UNKNOWN"}

# The set of claim_status values under which a claim still holds its
# (campaign_id, campaign_version) execution scope -- i.e. its eventual
# provider-submission outcome is not yet fully resolved, so a second,
# concurrent claim for the same scope must not be allowed to exist. Must
# stay in sync with the partial unique index uq_aec_active_campaign_scope
# (see AutonomousExecutionClaim / migration 20260727_0053). Derived from
# the actual, currently-reachable state machine (see module docstring-level
# transitions in claim_activated_package/mark_submission_safety_disabled/
# mark_pre_provider_blocked/advance_claimed_execution/
# release_execution_claim_scope_if_order_resolved): CLAIMED and
# EXECUTION_STARTED precede any provider call; SUBMISSION_PENDING and
# RECONCILIATION_REQUIRED follow a provider call whose outcome (including
# whether an order now exists) is not yet certain; RECOVERY_REQUIRED (name
# implies active, in-progress recovery, not yet resolved either way) is
# treated the same way, conservatively, since its semantics are not yet
# defined in code. BLOCKED is deliberately NOT here -- see
# _CLAIM_SCOPE_RELEASED_STATES below.
_CLAIM_SCOPE_NONTERMINAL_STATES = {
    "CLAIMED", "EXECUTION_STARTED", "SUBMISSION_PENDING", "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED",
}
# The complement: a claim in one of these states has either never made (and
# will never make, per mark_submission_safety_disabled/mark_pre_provider_
# blocked's own provider_call_made=false logging) a provider call, or has a
# fully, authoritatively resolved outcome -- it no longer needs to occupy
# the scope, so a later, legitimate sequential Controlled Proof (or
# ordinary automatic package) may claim the same campaign/version again.
# BLOCKED is included here (not in the nonterminal set above): unlike
# RECOVERY_REQUIRED, its name describes a permanent, non-recoverable
# pre-provider stop -- the same shape as FAILED_PRE_PROVIDER/
# SAFETY_DISABLED, not an in-progress state -- so it must not reserve the
# campaign scope forever either, once it is ever actually emitted.
_CLAIM_SCOPE_RELEASED_STATES = {
    "SAFETY_DISABLED", "FAILED_PRE_PROVIDER", "COMPLETED", "CANCELLED", "BUY_RECONCILED", "POSITION_OPENED", "BLOCKED",
}

_AUTHORITY_MODE_CONFIGURED_AUTOMATIC = "CONFIGURED_AUTOMATIC_SCOPE"
_AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED = "CONTROLLED_PROOF_DERIVED_SCOPE"


@dataclass(frozen=True)
class AutonomousClaimOutcome:
    claim: AutonomousExecutionClaim | None
    created: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class ResolvedAutonomousExecutionScope:
    """The one authoritative scope claim_activated_package claims an
    execution under -- resolved exactly once, from exactly one source:
    either the operator's globally-configured selector settings (ordinary
    automation, unchanged from before this existed), or a specific
    Controlled Proof's own persisted, already-activated package (authority_
    mode records which). A configured global selector -- including a
    statically pinned package/campaign/mandate value -- can never redirect
    or reject a genuinely Controlled-Proof-linked package: that package's
    scope is derived exclusively from its own linked ControlledProofRun."""

    authority_mode: str
    package_id: UUID
    campaign_id: UUID
    campaign_version: int
    mandate_id: UUID
    mandate_version_id: UUID
    mandate_evaluation_id: UUID | None
    controlled_proof_id: UUID | None
    product: str
    provider: str
    environment: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _owner() -> str:
    return f"orchestration:{os.getpid()}"


async def _resolve_controlled_proof_execution_scope(
    *, db: AsyncSession, package: CanonicalPreviewPackage, proof: ControlledProofRun,
) -> tuple[ResolvedAutonomousExecutionScope | None, str | None]:
    """Given a package already known to be linked to `proof` (via the
    proof's own `package_id`), verify every Controlled Proof execution-claim
    invariant fresh against the DB and either return a scope derived
    exclusively from the package's own persisted fields, or a precise
    fail-closed reason. Never falls back to the legacy configured-selector
    check -- once a package is genuinely linked to a Controlled Proof, its
    own invariants are authoritative; a coincidentally-matching (or
    mismatching) global selector is irrelevant."""

    def _blocked(reason: str) -> tuple[None, str]:
        logger.info(
            "autonomous_execution_scope_blocked authority_mode=%s package_id=%s controlled_proof_id=%s reason=%s",
            _AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED, package.package_id, proof.proof_id, reason,
        )
        return None, reason

    if proof.status not in _CONTROLLED_PROOF_ACTIVE_STATES:
        return _blocked("controlled_proof_not_active")
    # Postgres's TIMESTAMPTZ always round-trips timezone-aware; sqlite (used
    # only by this module's own tests) has no native tz-aware type and can
    # hand back a naive value after a flush-triggered reload -- normalize.
    expires_at = proof.expires_at if proof.expires_at.tzinfo is not None else proof.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _utcnow():
        return _blocked("controlled_proof_expired")
    if package.campaign_id != proof.campaign_id or package.campaign_version != proof.campaign_version:
        return _blocked("campaign_version_mismatch")
    if package.product != proof.product_id:
        return _blocked("proof_package_product_mismatch")
    if package.provider != proof.provider:
        return _blocked("proof_package_provider_mismatch")
    if package.environment != proof.environment:
        return _blocked("proof_package_environment_mismatch")
    if package.side not in {"BUY", "SELL"}:
        return _blocked("invalid_package_side")
    if package.package_state != "ACTIVATED":
        return _blocked("package_not_activated")
    if package.authorization_source != "MANDATE":
        return _blocked("package_authority_invalid")
    if package.mandate_id is None or package.mandate_version_id is None or package.mandate_evaluation_id is None:
        return _blocked("missing_mandate_identity")
    if package.dry_run_live_crypto_order_id is None:
        return _blocked("dry_run_evidence_missing")
    if package.decision_record_id is None or package.risk_event_id is None:
        return _blocked("evidence_incomplete")

    scope = ResolvedAutonomousExecutionScope(
        authority_mode=_AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED,
        package_id=package.package_id, campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
        mandate_evaluation_id=package.mandate_evaluation_id, controlled_proof_id=proof.proof_id,
        product=package.product, provider=package.provider, environment=package.environment,
    )
    logger.info(
        "autonomous_execution_scope_resolved authority_mode=%s controlled_proof_id=%s package_id=%s campaign_id=%s "
        "campaign_version=%s mandate_id=%s mandate_version_id=%s mandate_evaluation_id=%s product=%s provider=%s environment=%s",
        scope.authority_mode, scope.controlled_proof_id, scope.package_id, scope.campaign_id, scope.campaign_version,
        scope.mandate_id, scope.mandate_version_id, scope.mandate_evaluation_id, scope.product, scope.provider, scope.environment,
    )
    return scope, None


async def _resolve_autonomous_execution_scope(
    *, db: AsyncSession, package: CanonicalPreviewPackage,
) -> tuple[ResolvedAutonomousExecutionScope | None, str | None]:
    """Resolves execution-claim scope for `package` from exactly one
    source. A package genuinely linked to a Controlled Proof (via that
    proof's own `package_id`) always resolves -- or fails closed -- through
    the Controlled-Proof-derived path, never the legacy configured-selector
    path. Every other package (ordinary automation) resolves through the
    existing, byte-for-byte-unchanged configured-selector check."""
    logger.info("autonomous_execution_scope_resolution_started package_id=%s", package.package_id)
    proof = await db.scalar(
        select(ControlledProofRun).where(or_(
            ControlledProofRun.package_id == package.package_id,
            ControlledProofRun.sell_package_id == package.package_id,
        )).with_for_update().limit(1)
    )
    if proof is not None:
        return await _resolve_controlled_proof_execution_scope(db=db, package=package, proof=proof)

    settings = get_settings()
    configured = (
        settings.automatic_mandate_package_activation_campaign_id,
        settings.automatic_mandate_package_activation_campaign_version,
        settings.automatic_mandate_package_activation_mandate_id,
        settings.automatic_mandate_package_activation_mandate_version_id,
    )
    if any(value is None for value in configured) or configured != (
        package.campaign_id, package.campaign_version, package.mandate_id, package.mandate_version_id,
    ):
        logger.info(
            "autonomous_execution_scope_blocked authority_mode=%s package_id=%s controlled_proof_id=None reason=configured_scope_mismatch",
            _AUTHORITY_MODE_CONFIGURED_AUTOMATIC, package.package_id,
        )
        return None, "configured_scope_mismatch"

    scope = ResolvedAutonomousExecutionScope(
        authority_mode=_AUTHORITY_MODE_CONFIGURED_AUTOMATIC,
        package_id=package.package_id, campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
        mandate_evaluation_id=package.mandate_evaluation_id, controlled_proof_id=None,
        product=package.product, provider=package.provider, environment=package.environment,
    )
    logger.info(
        "autonomous_execution_scope_resolved authority_mode=%s controlled_proof_id=None package_id=%s campaign_id=%s "
        "campaign_version=%s mandate_id=%s mandate_version_id=%s mandate_evaluation_id=%s product=%s provider=%s environment=%s",
        scope.authority_mode, scope.package_id, scope.campaign_id, scope.campaign_version, scope.mandate_id,
        scope.mandate_version_id, scope.mandate_evaluation_id, scope.product, scope.provider, scope.environment,
    )
    return scope, None


async def claim_activated_package(
    *, db: AsyncSession, package_id: UUID, claim_owner: str | None = None, now: datetime | None = None,
) -> AutonomousClaimOutcome:
    observed_at = now or _utcnow()
    logger.info("autonomous_execution_claim_resolution_started package_id=%s", package_id)
    package = await db.scalar(
        select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).with_for_update().limit(1)
    )
    if package is None:
        return AutonomousClaimOutcome(None, False, "package_missing")
    existing = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package_id).limit(1)
    )
    if existing is not None:
        logger.info(
            "autonomous_execution_claim_reused claim_id=%s package_id=%s claim_status=%s claim_owner=%s",
            existing.claim_id, package_id, existing.claim_status, existing.claim_owner,
        )
        if existing.claim_status in _CLAIM_SCOPE_NONTERMINAL_STATES:
            logger.info(
                "autonomous_execution_claim_recovery_required claim_id=%s package_id=%s claim_status=%s",
                existing.claim_id, package_id, existing.claim_status,
            )
        return AutonomousClaimOutcome(existing, False, "already_claimed")
    if package.package_state != "ACTIVATED" or package.side not in {"BUY", "SELL"} or package.preview_expires_at <= observed_at:
        return AutonomousClaimOutcome(None, False, "package_not_eligible")
    if package.superseded_at is not None or package.authorization_source != "MANDATE":
        return AutonomousClaimOutcome(None, False, "package_authority_invalid")
    if package.mandate_id is None or package.mandate_version_id is None or package.mandate_evaluation_id is None:
        return AutonomousClaimOutcome(None, False, "mandate_identity_incomplete")

    resolved_scope, blocker = await _resolve_autonomous_execution_scope(db=db, package=package)
    if resolved_scope is None:
        return AutonomousClaimOutcome(None, False, blocker or "configured_scope_mismatch")

    activation = await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package_id).with_for_update().limit(1)
    )
    if activation is None or activation.package_id != package.package_id:
        return AutonomousClaimOutcome(None, False, "activation_missing_or_mismatched")
    if activation.activation_state != "ACTIVE" or activation.activated_at > observed_at or activation.expires_at <= observed_at:
        return AutonomousClaimOutcome(None, False, "activation_not_effective")
    if (activation.campaign_id, activation.campaign_version, activation.paper_account_id, activation.live_trading_profile_id, activation.provider, activation.environment, activation.product) != (
        package.campaign_id, package.campaign_version, package.paper_account_id, package.live_trading_profile_id, package.provider, package.environment, package.product,
    ):
        return AutonomousClaimOutcome(None, False, "activation_scope_mismatch")

    connection_raw = package.market_evidence_identity.get("exchange_connection_id") if isinstance(package.market_evidence_identity, dict) else None
    try:
        connection_id = UUID(str(connection_raw))
    except (TypeError, ValueError):
        return AutonomousClaimOutcome(None, False, "connection_identity_missing")

    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == package.runtime_campaign_id).limit(1))
    mandate = await db.scalar(select(AutonomousCapitalMandate).where(AutonomousCapitalMandate.mandate_id == package.mandate_id).limit(1))
    version = await db.scalar(select(AutonomousCapitalMandateVersion).where(AutonomousCapitalMandateVersion.mandate_version_id == package.mandate_version_id).limit(1))
    if runtime is None or runtime.status not in {"READY", "RUNNING"} or runtime.definition_version != package.campaign_version:
        return AutonomousClaimOutcome(None, False, "campaign_not_active")
    if mandate is None or mandate.status != "ACTIVE" or mandate.expires_at is not None and mandate.expires_at <= observed_at:
        return AutonomousClaimOutcome(None, False, "mandate_not_active")
    if version is None or not version.is_active or not version.is_authorized or version.mandate_id != package.mandate_id:
        return AutonomousClaimOutcome(None, False, "mandate_version_not_active")

    kill_switch = await db.scalar(
        select(RiskKillSwitch.id).where(RiskKillSwitch.engaged.is_(True)).where(
            (RiskKillSwitch.scope == "global") | and_(RiskKillSwitch.scope == "account", RiskKillSwitch.paper_account_id == package.paper_account_id)
        ).limit(1)
    )
    if kill_switch is not None:
        return AutonomousClaimOutcome(None, False, "kill_switch_engaged")

    await _recover_failed_pre_provider_order_for_scope(db=db, package=package)
    open_order = await db.scalar(
        select(LiveCryptoOrder.live_crypto_order_id).where(
            LiveCryptoOrder.provider == package.provider, LiveCryptoOrder.environment == package.environment,
            LiveCryptoOrder.product_id == package.product, LiveCryptoOrder.status.in_(_OPEN_ORDER_STATES),
        ).limit(1)
    )
    if open_order is not None:
        logger.info(
            "autonomous_execution_claim_blocked package_id=%s reason=unresolved_order_exists blocking_live_order_id=%s",
            package.package_id, open_order,
        )
        return AutonomousClaimOutcome(None, False, "unresolved_order_exists")
    unresolved = await db.scalar(claim_blocking_reconciliation_statement(
        provider=package.provider,
        environment=package.environment,
        product=package.product,
    ))
    if unresolved is not None:
        return AutonomousClaimOutcome(None, False, "unresolved_reconciliation_exists")
    net_quantity = await db.scalar(
        select(func.coalesce(func.sum(
            case((LiveAccountingRecord.side == "buy", LiveAccountingRecord.filled_quantity), else_=-LiveAccountingRecord.filled_quantity)
        ), Decimal("0"))).where(
            LiveAccountingRecord.capital_campaign_id == runtime.id,
            LiveAccountingRecord.record_type.in_(QUANTITY_BEARING_RECORD_TYPES),
        )
    )
    owned_quantity = Decimal(str(net_quantity or 0))
    if package.side == "BUY" and owned_quantity > 0:
        return AutonomousClaimOutcome(None, False, "campaign_position_already_open")
    if package.side == "SELL" and owned_quantity <= 0:
        return AutonomousClaimOutcome(None, False, "campaign_position_not_open")

    owner = claim_owner or _owner()
    statement = insert(AutonomousExecutionClaim).values(
        package_id=package.package_id, activation_id=activation.activation_id,
        campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
        account_id=package.paper_account_id, profile_id=package.live_trading_profile_id,
        connection_id=connection_id, provider=package.provider, environment=package.environment,
        product=package.product, side=package.side, claim_status="CLAIMED", claimed_at=observed_at,
        claim_owner=owner, recover_after=observed_at + timedelta(minutes=2), attempt_count=1,
    ).on_conflict_do_nothing().returning(AutonomousExecutionClaim.claim_id)
    inserted_id = await db.scalar(statement)
    claim = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package.package_id).with_for_update().limit(1)
    )
    if claim is None:
        # Not a same-package race -- if another worker had just inserted
        # THIS package's own claim between our existence check above and
        # this INSERT, the by-package_id SELECT just above would have found
        # it. Reaching here instead means the INSERT was rejected by the
        # active-campaign-scope partial unique index
        # (uq_aec_active_campaign_scope): a different, still-nonterminal
        # claim already owns this (campaign_id, campaign_version).
        conflicting = await db.scalar(
            select(AutonomousExecutionClaim).where(
                AutonomousExecutionClaim.campaign_id == package.campaign_id,
                AutonomousExecutionClaim.campaign_version == package.campaign_version,
                AutonomousExecutionClaim.claim_status.in_(_CLAIM_SCOPE_NONTERMINAL_STATES),
            ).limit(1)
        )
        if conflicting is not None:
            logger.warning(
                "autonomous_execution_claim_blocked package_id=%s campaign_id=%s campaign_version=%s reason=active_campaign_execution_claim_exists "
                "conflicting_claim_id=%s conflicting_package_id=%s conflicting_claim_owner=%s conflicting_claim_status=%s",
                package.package_id, package.campaign_id, package.campaign_version,
                conflicting.claim_id, conflicting.package_id, conflicting.claim_owner, conflicting.claim_status,
            )
            return AutonomousClaimOutcome(None, False, "active_campaign_execution_claim_exists")
        logger.warning(
            "autonomous_execution_claim_blocked package_id=%s campaign_id=%s campaign_version=%s reason=claim_concurrency_conflict",
            package.package_id, package.campaign_id, package.campaign_version,
        )
        return AutonomousClaimOutcome(None, False, "claim_concurrency_conflict")
    created = inserted_id is not None
    if created:
        db.add(AuditLog(
            actor=owner, action="autonomous_execution_claim.created", entity_type="autonomous_execution_claim",
            entity_id=claim.claim_id, before_state=None,
            after_state={
                "package_id": str(package.package_id), "activation_id": str(activation.activation_id), "claim_status": "CLAIMED",
                "authority_mode": resolved_scope.authority_mode,
                "controlled_proof_id": None if resolved_scope.controlled_proof_id is None else str(resolved_scope.controlled_proof_id),
            },
        ))
        await db.flush()
        logger.info(
            "autonomous_execution_claimed claim_id=%s package_id=%s campaign_id=%s campaign_version=%s "
            "authority_mode=%s controlled_proof_id=%s provider=%s environment=%s product=%s",
            claim.claim_id, package.package_id, package.campaign_id, package.campaign_version,
            resolved_scope.authority_mode, resolved_scope.controlled_proof_id, package.provider, package.environment, package.product,
        )
    return AutonomousClaimOutcome(claim, created, "claimed" if created else "already_claimed")


# Backward-compatible public name for existing callers.  It now claims the
# activated canonical package's persisted side rather than imposing BUY.
claim_activated_buy_package = claim_activated_package


async def mark_submission_safety_disabled(*, db: AsyncSession, claim: AutonomousExecutionClaim) -> None:
    # Guards against _CLAIM_SCOPE_RELEASED_STATES in full (not just COMPLETED/
    # CANCELLED): a claim that already reached BUY_RECONCILED, SAFETY_DISABLED,
    # FAILED_PRE_PROVIDER, POSITION_OPENED, or BLOCKED must never be
    # overwritten by a later, stale call -- see advance_claimed_execution's
    # own top-level guard for why such a call can still occur.
    if claim.claim_status in _CLAIM_SCOPE_RELEASED_STATES:
        return
    before = claim.claim_status
    claim.claim_status = "SAFETY_DISABLED"
    claim.last_error_code = "live_submission_disabled"
    claim.recover_after = None
    claim.updated_at = _utcnow()
    db.add(AuditLog(
        actor=claim.claim_owner, action="autonomous_execution_claim.safety_disabled",
        entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
        before_state={"claim_status": before}, after_state={"claim_status": claim.claim_status, "reason_code": claim.last_error_code},
    ))
    await db.flush()


async def mark_pre_provider_blocked(
    *, db: AsyncSession, claim: AutonomousExecutionClaim, reason_code: str,
    safe_failure_evidence: dict[str, object] | None = None,
) -> None:
    # See mark_submission_safety_disabled's identical guard comment above.
    if claim.claim_status in _CLAIM_SCOPE_RELEASED_STATES:
        return
    before = claim.claim_status
    claim.claim_status = "FAILED_PRE_PROVIDER"
    claim.last_error_code = reason_code
    claim.recover_after = None
    claim.updated_at = _utcnow()
    live_order_transition: dict[str, object] | None = None
    live_order_id = getattr(claim, "live_order_id", None)
    if live_order_id is not None:
        live_order = await db.scalar(
            select(LiveCryptoOrder)
            .where(LiveCryptoOrder.live_crypto_order_id == live_order_id)
            .with_for_update()
            .limit(1)
        )
        if (
            live_order is not None
            and live_order.status in {"PENDING_CONFIRMATION", "VALIDATING"}
            and live_order.provider_order_id is None
            and live_order.submitted_at is None
            and isinstance(live_order.safe_provider_response, dict)
            and live_order.safe_provider_response.get("provider_call_made") is False
        ):
            previous_order_status = live_order.status
            live_order.status = "CANCELLED"
            live_order.cancelled_at = claim.updated_at
            live_order.failure_code = "failed_pre_provider"
            live_order.failure_reason = reason_code
            live_order.safe_provider_response = {
                **(live_order.safe_provider_response if isinstance(live_order.safe_provider_response, dict) else {}),
                "provider_call_made": False,
                "pre_provider_terminal_reason": reason_code,
            }
            live_order_transition = {
                "live_order_id": str(live_order.live_crypto_order_id),
                "before_status": previous_order_status,
                "after_status": live_order.status,
            }
    after_state: dict[str, object] = {
        "claim_status": claim.claim_status,
        "reason_code": reason_code,
        "provider_call_made": False,
    }
    if safe_failure_evidence:
        after_state["safe_failure_evidence"] = safe_failure_evidence
    if live_order_transition:
        after_state["live_order_transition"] = live_order_transition
    db.add(AuditLog(
        actor=claim.claim_owner, action="autonomous_execution_claim.failed_pre_provider",
        entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
        before_state={"claim_status": before},
        after_state=after_state,
    ))
    await db.flush()


async def _recover_failed_pre_provider_order_for_scope(
    *, db: AsyncSession, package: CanonicalPreviewPackage,
) -> UUID | None:
    """Terminalize one historical provider-never-called prepared order.

    FAILED_PRE_PROVIDER releases its execution claim scope, so its linked
    local order must also leave an open pre-submission state. This recovery
    handles rows written before that transition was enforced atomically.
    Provider-visible or submission-started orders are deliberately excluded.
    """
    order = await db.scalar(
        select(LiveCryptoOrder)
        .join(
            AutonomousExecutionClaim,
            AutonomousExecutionClaim.live_order_id == LiveCryptoOrder.live_crypto_order_id,
        )
        .where(
            AutonomousExecutionClaim.claim_status == "FAILED_PRE_PROVIDER",
            LiveCryptoOrder.provider == package.provider,
            LiveCryptoOrder.environment == package.environment,
            LiveCryptoOrder.product_id == package.product,
            LiveCryptoOrder.status.in_({"PENDING_CONFIRMATION", "VALIDATING"}),
            LiveCryptoOrder.provider_order_id.is_(None),
            LiveCryptoOrder.submitted_at.is_(None),
            LiveCryptoOrder.safe_provider_response["provider_call_made"].as_boolean().is_(False),
        )
        .with_for_update()
        .limit(1)
    )
    if order is None:
        return None
    if (
        order.status not in {"PENDING_CONFIRMATION", "VALIDATING"}
        or order.provider_order_id is not None
        or order.submitted_at is not None
        or not isinstance(order.safe_provider_response, dict)
        or order.safe_provider_response.get("provider_call_made") is not False
    ):
        return None
    before = order.status
    observed_at = _utcnow()
    order.status = "CANCELLED"
    order.cancelled_at = observed_at
    order.failure_code = "failed_pre_provider"
    order.failure_reason = "historical_failed_pre_provider_recovered"
    order.safe_provider_response = {
        **(order.safe_provider_response if isinstance(order.safe_provider_response, dict) else {}),
        "provider_call_made": False,
        "pre_provider_terminal_reason": "historical_failed_pre_provider_recovered",
    }
    db.add(AuditLog(
        actor="system:orchestration",
        action="live_crypto_order.failed_pre_provider_recovered",
        entity_type="live_crypto_order",
        entity_id=order.live_crypto_order_id,
        before_state={"status": before},
        after_state={
            "status": order.status,
            "failure_code": order.failure_code,
            "provider_call_made": False,
        },
    ))
    await db.flush()
    logger.info(
        "autonomous_execution_failed_pre_provider_order_recovered live_order_id=%s provider=%s environment=%s "
        "product=%s previous_status=%s new_status=CANCELLED provider_call_made=false",
        order.live_crypto_order_id, order.provider, order.environment, order.product_id, before,
    )
    return order.live_crypto_order_id


async def _controlled_proof_id_for_failure_diagnostics(
    *, db: AsyncSession, package_id: UUID,
) -> UUID | None:
    """Best-effort diagnostic linkage only; never affects authorization."""
    try:
        return await db.scalar(
            select(ControlledProofRun.proof_id)
            .where(
                or_(
                    ControlledProofRun.package_id == package_id,
                    ControlledProofRun.sell_package_id == package_id,
                )
            )
            .limit(1)
        )
    except Exception:
        # The original exception remains the authoritative failure. A broken
        # transaction must not let optional diagnostic enrichment replace it.
        return None


# A live order's status, once reconciliation has authoritatively resolved
# it, maps onto the one claim-lifecycle status that correctly reflects that
# outcome and releases the claim's campaign/version execution scope. Only
# genuinely final, authoritative provider outcomes are listed here --
# ACKNOWLEDGED/SUBMITTED/PARTIALLY_FILLED/UNKNOWN/RECONCILIATION_REQUIRED
# all mean the outcome is still unresolved, so the claim (and its scope)
# must stay exactly where it is.
_ORDER_STATUS_TO_RELEASED_CLAIM_STATUS = {
    "FILLED": "FILLED",
    "CANCELLED": "CANCELLED",
    "REJECTED": "CANCELLED",
    "EXPIRED": "CANCELLED",
}


async def release_execution_claim_scope_if_order_resolved(
    *, db: AsyncSession, live_crypto_order_id: UUID, order_status: str, now: datetime | None = None,
) -> None:
    """Called after a live order's status has just been set to an
    authoritative, reconciliation-confirmed terminal outcome (never a
    merely-observed, still-ambiguous one). Before this existed, nothing in
    the codebase ever advanced a claim past SUBMISSION_PENDING /
    RECONCILIATION_REQUIRED -- so even a genuinely, successfully completed
    BUY would keep its (campaign_id, campaign_version) execution scope
    reserved forever, permanently blocking every later, legitimate
    sequential Controlled Proof exactly like the original
    claim_concurrency_conflict defect. No-op when the order status is not
    itself an authoritative terminal outcome, when no claim references this
    order, or when that claim has already left the nonterminal set
    (idempotent -- safe to call on every reconciliation pass)."""
    resolved_outcome = _ORDER_STATUS_TO_RELEASED_CLAIM_STATUS.get(order_status)
    if resolved_outcome is None:
        return
    claim = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.live_order_id == live_crypto_order_id).with_for_update().limit(1)
    )
    if claim is None or claim.claim_status not in _CLAIM_SCOPE_NONTERMINAL_STATES:
        return
    released_status = (
        "BUY_RECONCILED" if resolved_outcome == "FILLED" and claim.side == "BUY"
        else "COMPLETED" if resolved_outcome == "FILLED"
        else resolved_outcome
    )
    observed_at = now or _utcnow()
    before = claim.claim_status
    claim.claim_status = released_status
    claim.completed_at = observed_at
    claim.updated_at = observed_at
    activation = await db.get(CanonicalProvingActivation, claim.activation_id)
    if activation is not None and activation.activation_state == "ACTIVE":
        activation.activation_state = "COMPLETED"
        activation.updated_at = observed_at
        db.add(AuditLog(
            actor="system:reconciliation", action="canonical_proving_activation.completed",
            entity_type="canonical_proving_activation", entity_id=activation.activation_id,
            before_state={"activation_state": "ACTIVE"},
            after_state={
                "activation_state": "COMPLETED", "claim_status": released_status,
                "order_status": order_status, "live_crypto_order_id": str(live_crypto_order_id),
            },
        ))
    db.add(AuditLog(
        actor="system:reconciliation", action="autonomous_execution_claim.scope_released",
        entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
        before_state={"claim_status": before},
        after_state={"claim_status": released_status, "order_status": order_status, "live_crypto_order_id": str(live_crypto_order_id)},
    ))
    await db.flush()
    logger.info(
        "autonomous_execution_claim_scope_released claim_id=%s live_crypto_order_id=%s campaign_id=%s campaign_version=%s "
        "previous_claim_status=%s new_claim_status=%s order_status=%s",
        claim.claim_id, live_crypto_order_id, claim.campaign_id, claim.campaign_version, before, released_status, order_status,
    )


async def advance_claimed_execution(*, db: AsyncSession, claim: AutonomousExecutionClaim) -> None:
    """Given an already-CLAIMED (or EXECUTION_STARTED) claim, attempt to
    prepare and (if live submission is enabled) execute it -- terminalizing
    on every failure path, expected or not, so a claim this function
    touches can never be left sitting in CLAIMED. Shared by the normal
    per-cycle activation path
    (continuous_pipeline_worker._attempt_automatic_ready_package_creation)
    and sweep_stale_autonomous_execution_claims below -- the only two
    callers of prepare_autonomous_claimed_order -- so this failure-handling
    is defined exactly once.

    Guarded as a true no-op for any claim that has already left
    _CLAIM_SCOPE_NONTERMINAL_STATES. Without this, continuous_pipeline_
    worker's per-cycle path calls this on every cycle for as long as the
    package's own package_state stays "ACTIVATED" -- which nothing ever
    changes, even after the claim itself reaches BUY_RECONCILED -- so a
    claim already resolved (successfully filled, or dead-ended pre-
    provider) would otherwise be re-prepared every cycle. That re-
    preparation would typically fail on activation_not_effective (the
    short-lived activation window has long since expired by then), which
    mark_pre_provider_blocked would (before its own guard above) have
    silently overwritten a genuinely successful BUY_RECONCILED claim's
    status back to FAILED_PRE_PROVIDER -- corrupting the record of a real,
    profitable BUY. This guard stops that before prepare_autonomous_
    claimed_buy is even called."""
    if claim.claim_status not in _CLAIM_SCOPE_NONTERMINAL_STATES:
        return
    try:
        prepared = await prepare_autonomous_claimed_order(db=db, claim_id=claim.claim_id)
    except InvalidRequestError as exc:
        reason_code = str((exc.details or {}).get("blocker") or "autonomous_order_preparation_failed")
        await mark_pre_provider_blocked(db=db, claim=claim, reason_code=reason_code)
        logger.info(
            "autonomous_execution_failed_pre_provider claim_id=%s package_id=%s reason=%s provider_call_made=false",
            claim.claim_id, claim.package_id, reason_code,
        )
        return
    except Exception:
        # A defect in preparation (or anything it calls) must never leave
        # the claim silently stuck in CLAIMED forever -- the exact failure
        # mode that left claim 854e9b17-f608-400a-b6fe-58647b730cf0 with no
        # lifecycle event beyond "created". Terminalize honestly instead of
        # only logging.
        await mark_pre_provider_blocked(db=db, claim=claim, reason_code="unexpected_preparation_failure")
        logger.exception(
            "autonomous_execution_failed_pre_provider claim_id=%s package_id=%s reason=unexpected_preparation_failure provider_call_made=false",
            claim.claim_id, claim.package_id,
        )
        return

    if not get_settings().live_crypto_order_submission_enabled:
        await mark_submission_safety_disabled(db=db, claim=prepared.claim)
        logger.info(
            "autonomous_execution_safety_disabled claim_id=%s package_id=%s live_order_id=%s campaign_id=%s campaign_version=%s reason=live_submission_disabled provider_call_made=false provider_call_reachable=false recoverable=true",
            prepared.claim.claim_id, prepared.claim.package_id, prepared.order.live_crypto_order_id,
            prepared.claim.campaign_id, prepared.claim.campaign_version,
        )
        return

    try:
        execution = await execute_prepared_autonomous_claim(db=db, prepared=prepared)
        prepared.claim.claim_status = (
            "RECONCILIATION_REQUIRED"
            if execution.current_state == "RECONCILIATION_REQUIRED"
            else "SUBMISSION_PENDING"
        )
        prepared.claim.reconciliation_state = execution.current_state
        prepared.claim.updated_at = _utcnow()
        await db.flush()
    except Exception as exc:
        exception_type = type(exc).__name__
        exception_message = redact_message_for_diagnostics(str(exc), settings=get_settings())
        failing_stage = str(getattr(exc, "omnitrade_failing_stage", "commissioned_execution"))
        safe_reason_code = exception_type
        if isinstance(exc, InvalidRequestError):
            blocker = str((exc.details or {}).get("blocker") or "").strip()
            if blocker:
                safe_reason_code = redact_message_for_diagnostics(blocker, settings=get_settings())
        controlled_proof_id = await _controlled_proof_id_for_failure_diagnostics(
            db=db, package_id=prepared.claim.package_id,
        )
        safe_failure_evidence: dict[str, object] = {
            "exception_type": exception_type,
            "exception_message": exception_message,
            "safe_reason_code": safe_reason_code,
            "failing_stage": failing_stage,
            "provider_call_made": False,
        }
        await mark_pre_provider_blocked(
            db=db,
            claim=prepared.claim,
            reason_code="commissioned_execution_request_evidence_unavailable",
            safe_failure_evidence=safe_failure_evidence,
        )
        # Use the original traceback with a redacted exception object. Plain
        # logger.exception would append str(exc) again, bypassing redaction.
        safe_traceback_exception = RuntimeError(exception_message)
        logger.error(
            "event=commissioned_execution_pre_provider_exception claim_id=%s package_id=%s controlled_proof_id=%s "
            "live_order_id=%s campaign_id=%s campaign_version=%s product=%s side=%s provider=%s environment=%s "
            "exception_type=%s exception_message=%r failing_stage=%s provider_call_made=false",
            prepared.claim.claim_id,
            prepared.claim.package_id,
            controlled_proof_id,
            prepared.order.live_crypto_order_id,
            prepared.claim.campaign_id,
            prepared.claim.campaign_version,
            prepared.claim.product,
            prepared.claim.side,
            prepared.claim.provider,
            prepared.claim.environment,
            exception_type,
            exception_message,
            failing_stage,
            exc_info=(type(safe_traceback_exception), safe_traceback_exception, exc.__traceback__),
        )
        logger.info(
            "autonomous_execution_failed_pre_provider claim_id=%s package_id=%s live_order_id=%s "
            "reason=commissioned_execution_request_evidence_unavailable provider_call_made=false",
            prepared.claim.claim_id,
            prepared.claim.package_id,
            prepared.order.live_crypto_order_id,
        )


async def sweep_stale_autonomous_execution_claims(*, db: AsyncSession, now: datetime | None = None) -> int:
    """Recovery pass, deliberately independent of any cycle's decision
    composition. prepare_autonomous_claimed_order re-derives everything it
    needs from the claim_id alone (package/activation/risk/kill-switch/
    position state), so a CLAIMED or EXECUTION_STARTED claim can safely be
    retried here at any time -- this is the only mechanism that revisits a
    claim once the cycle that originally created it stops recurring (e.g. a
    Controlled-Proof-forced entry, whose HOLD-override never re-fires once
    the proof already has a linked entry) -- or after a worker crash mid-
    preparation. EXECUTION_STARTED is included alongside CLAIMED: without
    it, a crash between prepare_autonomous_claimed_order's own EXECUTION_
    STARTED transition and advance_claimed_execution's subsequent submission
    call would orphan the claim forever (recover_after is set once, at
    CLAIMED-insert time, and never advanced by the EXECUTION_STARTED
    transition, so it is still meaningful here) -- permanently reserving
    its campaign scope, exactly the class of defect this whole fix removes.
    Re-preparing an EXECUTION_STARTED claim is safe: prepare_autonomous_
    claimed_buy recognizes its own already-set live_order_id and returns a
    replayed result rather than re-transitioning anything. SUBMISSION_
    PENDING and RECONCILIATION_REQUIRED are deliberately excluded -- a
    provider call has already been made (or may have been); the correct
    recovery there is reconciliation against the real order, never a blind
    re-preparation attempt (prepare_autonomous_claimed_order itself refuses to
    re-prepare a RECONCILIATION_REQUIRED claim; sweeping it here would
    misclassify it as failed_pre_provider with provider_call_made=false,
    which would be false). Scoped by the same recover_after/claim_status
    columns and index (ix_aec_status_recovery) that were already added for
    exactly this purpose but never read anywhere until now. Returns the
    number of claims swept."""
    observed_at = now or _utcnow()
    stale = (await db.scalars(
        select(AutonomousExecutionClaim).where(
            AutonomousExecutionClaim.claim_status.in_(("CLAIMED", "EXECUTION_STARTED")),
            AutonomousExecutionClaim.recover_after.is_not(None),
            AutonomousExecutionClaim.recover_after <= observed_at,
        )
    )).all()
    for claim in stale:
        try:
            await advance_claimed_execution(db=db, claim=claim)
        except Exception:
            # advance_claimed_execution already terminalizes every failure
            # path it knows about; this is a last-resort backstop so one
            # claim's unforeseen failure can never abort the sweep of the
            # rest, or resurrect the "stuck forever" failure mode one layer
            # up from where it was already closed.
            logger.exception("autonomous_execution_claim_sweep_failed claim_id=%s", claim.claim_id)
    return len(stale)
