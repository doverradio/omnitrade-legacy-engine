from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.capital_campaign import CapitalCampaign
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.risk_kill_switch import RiskKillSwitch
from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

_OPEN_ORDER_STATES = {"PENDING_CONFIRMATION", "VALIDATING", "SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED", "UNKNOWN"}
_TERMINAL_CLAIMS = {"COMPLETED", "CANCELLED"}


@dataclass(frozen=True)
class AutonomousClaimOutcome:
    claim: AutonomousExecutionClaim | None
    created: bool
    reason_code: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _owner() -> str:
    return f"orchestration:{os.getpid()}"


async def claim_activated_buy_package(
    *, db: AsyncSession, package_id: UUID, claim_owner: str | None = None, now: datetime | None = None,
) -> AutonomousClaimOutcome:
    observed_at = now or _utcnow()
    package = await db.scalar(
        select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).with_for_update().limit(1)
    )
    if package is None:
        return AutonomousClaimOutcome(None, False, "package_missing")
    existing = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package_id).limit(1)
    )
    if existing is not None:
        return AutonomousClaimOutcome(existing, False, "already_claimed")
    if package.package_state != "ACTIVATED" or package.side != "BUY" or package.preview_expires_at <= observed_at:
        return AutonomousClaimOutcome(None, False, "package_not_eligible")
    if package.superseded_at is not None or package.authorization_source != "MANDATE":
        return AutonomousClaimOutcome(None, False, "package_authority_invalid")
    if package.mandate_id is None or package.mandate_version_id is None or package.mandate_evaluation_id is None:
        return AutonomousClaimOutcome(None, False, "mandate_identity_incomplete")

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
        return AutonomousClaimOutcome(None, False, "configured_scope_mismatch")

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

    open_order = await db.scalar(
        select(LiveCryptoOrder.live_crypto_order_id).where(
            LiveCryptoOrder.provider == package.provider, LiveCryptoOrder.environment == package.environment,
            LiveCryptoOrder.product_id == package.product, LiveCryptoOrder.status.in_(_OPEN_ORDER_STATES),
        ).limit(1)
    )
    if open_order is not None:
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
        ), Decimal("0"))).where(LiveAccountingRecord.capital_campaign_id == runtime.id)
    )
    if Decimal(str(net_quantity or 0)) > 0:
        return AutonomousClaimOutcome(None, False, "campaign_position_already_open")

    owner = claim_owner or _owner()
    statement = insert(AutonomousExecutionClaim).values(
        package_id=package.package_id, activation_id=activation.activation_id,
        campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
        account_id=package.paper_account_id, profile_id=package.live_trading_profile_id,
        connection_id=connection_id, provider=package.provider, environment=package.environment,
        product=package.product, side="BUY", claim_status="CLAIMED", claimed_at=observed_at,
        claim_owner=owner, recover_after=observed_at + timedelta(minutes=2), attempt_count=1,
    ).on_conflict_do_nothing().returning(AutonomousExecutionClaim.claim_id)
    inserted_id = await db.scalar(statement)
    claim = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package.package_id).with_for_update().limit(1)
    )
    if claim is None:
        return AutonomousClaimOutcome(None, False, "claim_concurrency_conflict")
    created = inserted_id is not None
    if created:
        db.add(AuditLog(
            actor=owner, action="autonomous_execution_claim.created", entity_type="autonomous_execution_claim",
            entity_id=claim.claim_id, before_state=None,
            after_state={"package_id": str(package.package_id), "activation_id": str(activation.activation_id), "claim_status": "CLAIMED"},
        ))
        await db.flush()
    return AutonomousClaimOutcome(claim, created, "claimed" if created else "already_claimed")


async def mark_submission_safety_disabled(*, db: AsyncSession, claim: AutonomousExecutionClaim) -> None:
    if claim.claim_status in _TERMINAL_CLAIMS:
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
) -> None:
    if claim.claim_status in _TERMINAL_CLAIMS:
        return
    before = claim.claim_status
    claim.claim_status = "FAILED_PRE_PROVIDER"
    claim.last_error_code = reason_code
    claim.recover_after = None
    claim.updated_at = _utcnow()
    db.add(AuditLog(
        actor=claim.claim_owner, action="autonomous_execution_claim.failed_pre_provider",
        entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
        before_state={"claim_status": before},
        after_state={"claim_status": claim.claim_status, "reason_code": reason_code, "provider_call_made": False},
    ))
    await db.flush()
