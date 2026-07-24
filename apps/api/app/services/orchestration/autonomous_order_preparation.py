from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.schemas.capital_campaign_domain import CommissionedEntryExecutionRequest, CommissionedReadinessRequest
from app.services.capital_campaign_domain.activated_commissioned_entry import execute_activated_commissioned_entry
from app.services.risk.risk_context import resolve_effective_risk_policy


@dataclass(frozen=True)
class AutonomousOrderPreparationResult:
    claim: AutonomousExecutionClaim
    order: LiveCryptoOrder
    replayed: bool


async def execute_prepared_autonomous_claim(
    *, db: AsyncSession, prepared: AutonomousOrderPreparationResult,
):
    """Build the execution request exclusively from the claimed persisted rows."""
    claim, order = prepared.claim, prepared.order
    package = await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == claim.package_id).limit(1))
    activation = await db.scalar(select(CanonicalProvingActivation).where(CanonicalProvingActivation.activation_id == claim.activation_id).limit(1))
    preview = None if package is None else await db.scalar(
        select(CryptoOrderPreview).where(CryptoOrderPreview.crypto_order_preview_id == package.crypto_order_preview_id).limit(1)
    )
    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == claim.account_id).limit(1))
    asset = await db.scalar(select(Asset).where(Asset.symbol == claim.product.split("-")[0], Asset.exchange == claim.provider).limit(1))
    if package is None or activation is None or preview is None or account is None or asset is None:
        _fail("commissioned_execution_request_evidence_unavailable")
    if preview.estimated_average_price is None or preview.estimated_base_size is None:
        _fail("commissioned_execution_market_evidence_unavailable")
    policy = await resolve_effective_risk_policy(db=db, paper_account_id=claim.account_id)
    readiness = CommissionedReadinessRequest.model_construct(
        campaign_id=claim.campaign_id, version=claim.campaign_version,
        provider=claim.provider, environment=claim.environment, instrument=claim.product,
        requested_quote_amount=package.risk_approved_amount, quote_currency="USD",
        idempotency_key=f"autonomous-entry-readiness:{claim.claim_id}",
        live_trading_profile_id=claim.profile_id, account_id=claim.account_id,
        mandate_id=claim.mandate_id, mandate_version_id=claim.mandate_version_id,
        authorization_expires_at=activation.expires_at,
    )
    request = CommissionedEntryExecutionRequest.model_construct(
        campaign_id=claim.campaign_id, version=claim.campaign_version, actor=claim.claim_owner,
        idempotency_key=f"autonomous-entry:{claim.claim_id}", readiness_request=readiness,
        expected_preview_identity_hash=package.input_fingerprint,
        live_crypto_order_id=order.live_crypto_order_id, confirmation_challenge_id=None,
        confirmation_phrase=None, submit_idempotency_token=f"autonomous-submit:{claim.claim_id}",
        risk_signal_id=package.crypto_order_preview_id, paper_account_id=claim.account_id,
        asset_id=asset.id, requested_base_quantity=preview.estimated_base_size,
        reference_price=preview.estimated_average_price, account_equity=account.current_cash_balance,
        max_position_size_pct=policy.max_position_size_pct, min_order_notional=asset.min_order_notional,
        qty_step_size=asset.qty_step_size, supports_fractional=asset.supports_fractional,
    )
    return await execute_activated_commissioned_entry(
        db=db, package_id=claim.package_id, request=request,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fail(code: str) -> None:
    raise InvalidRequestError(message="Autonomous order preparation failed closed", details={"blocker": code})


async def prepare_autonomous_claimed_buy(
    *, db: AsyncSession, claim_id: UUID, now: datetime | None = None,
) -> AutonomousOrderPreparationResult:
    observed_at = now or _utcnow()
    claim = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.claim_id == claim_id).with_for_update().limit(1)
    )
    if claim is None or claim.claim_status in {"COMPLETED", "CANCELLED", "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED"}:
        _fail("claim_not_preparable")
    package = await db.scalar(
        select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == claim.package_id).with_for_update().limit(1)
    )
    activation = await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.activation_id == claim.activation_id).with_for_update().limit(1)
    )
    if package is None or activation is None or activation.package_id != package.package_id:
        _fail("package_activation_mismatch")
    if package.package_state != "ACTIVATED" or package.side != "BUY" or package.preview_expires_at <= observed_at:
        _fail("package_not_actionable")
    if activation.activation_state != "ACTIVE" or activation.activated_at > observed_at or activation.expires_at <= observed_at:
        _fail("activation_not_effective")
    if (
        package.campaign_id, package.campaign_version, package.mandate_id, package.mandate_version_id,
        package.paper_account_id, package.live_trading_profile_id, package.provider, package.environment, package.product,
    ) != (
        claim.campaign_id, claim.campaign_version, claim.mandate_id, claim.mandate_version_id,
        claim.account_id, claim.profile_id, claim.provider, claim.environment, claim.product,
    ):
        _fail("claim_package_identity_mismatch")
    if (
        activation.campaign_id, activation.campaign_version, activation.paper_account_id,
        activation.live_trading_profile_id, activation.provider, activation.environment, activation.product,
    ) != (
        claim.campaign_id, claim.campaign_version, claim.account_id,
        claim.profile_id, claim.provider, claim.environment, claim.product,
    ):
        _fail("claim_activation_identity_mismatch")
    risk = await db.scalar(select(RiskEvent).where(RiskEvent.id == package.risk_event_id).limit(1))
    if risk is None or risk.paper_account_id != claim.account_id:
        _fail("authoritative_risk_evidence_missing")
    kill_switch = await db.scalar(
        select(RiskKillSwitch.id).where(RiskKillSwitch.engaged.is_(True)).where(
            (RiskKillSwitch.scope == "global")
            | and_(RiskKillSwitch.scope == "account", RiskKillSwitch.paper_account_id == claim.account_id)
        ).limit(1)
    )
    if kill_switch is not None:
        _fail("kill_switch_engaged")
    latest_reconciliation = (
        select(
            LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
            func.max(LiveReconciliationEvent.sequence_number).label("max_seq"),
        )
        .where(LiveReconciliationEvent.provider_name == claim.provider)
        .where(LiveReconciliationEvent.live_crypto_order_id.is_not(None))
        .group_by(LiveReconciliationEvent.live_crypto_order_id)
        .subquery()
    )
    unresolved = await db.scalar(
        select(LiveReconciliationEvent.id)
        .join(
            latest_reconciliation,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest_reconciliation.c.order_id,
                LiveReconciliationEvent.sequence_number == latest_reconciliation.c.max_seq,
            ),
        )
        .where(LiveReconciliationEvent.reconciliation_status.in_([
            "open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch",
        ]))
        .limit(1)
    )
    if unresolved is not None:
        _fail("reconciliation_obligation_exists")
    open_quantity = await db.scalar(
        select(func.coalesce(func.sum(case(
            (LiveAccountingRecord.side == "buy", LiveAccountingRecord.filled_quantity),
            else_=-LiveAccountingRecord.filled_quantity,
        )), Decimal("0"))).where(
            LiveAccountingRecord.live_trading_profile_id == claim.profile_id,
            LiveAccountingRecord.symbol == claim.product,
        )
    )
    if Decimal(str(open_quantity or 0)) > 0:
        _fail("owned_position_exists")

    order = await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == package.dry_run_live_crypto_order_id).with_for_update().limit(1)
    )
    if order is None or order.crypto_order_preview_id != package.crypto_order_preview_id:
        _fail("canonical_dry_run_order_missing")
    if order.exchange_connection_id != claim.connection_id:
        _fail("exchange_connection_mismatch")
    if (order.provider, order.environment, order.product_id, order.side) != (
        claim.provider, claim.environment, claim.product, "BUY",
    ):
        _fail("order_scope_mismatch")
    if claim.live_order_id is not None and claim.live_order_id != order.live_crypto_order_id:
        _fail("claim_order_identity_mismatch")
    evidence = dict(order.safe_provider_response or {})
    existing_claim_id = evidence.get("autonomous_execution_claim_id")
    if existing_claim_id not in {None, str(claim.claim_id)}:
        _fail("order_owned_by_different_claim")
    replayed = claim.live_order_id == order.live_crypto_order_id and existing_claim_id == str(claim.claim_id)
    if not replayed:
        if order.status != "DRY_RUN_READY":
            _fail("canonical_order_state_conflict")
        evidence.update({
            "autonomous_execution_claim_id": str(claim.claim_id),
            "canonical_preview_package_id": str(package.package_id),
            "authority_source": "MANDATE",
            "prepared_by": claim.claim_owner,
            "autonomous_prepared": True,
            "provider_call_made": False,
        })
        order.status = "PENDING_CONFIRMATION"
        order.safe_provider_response = evidence
        order.updated_at = observed_at
        claim.live_order_id = order.live_crypto_order_id
        claim.claim_status = "EXECUTION_STARTED"
        claim.last_error_code = None
        claim.updated_at = observed_at
        db.add(AuditLog(
            actor=claim.claim_owner, action="autonomous_execution_claim.order_prepared",
            entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
            before_state={"live_order_id": None, "order_status": "DRY_RUN_READY"},
            after_state={"live_order_id": str(order.live_crypto_order_id), "order_status": order.status, "provider_call_made": False},
        ))
        await db.flush()
    return AutonomousOrderPreparationResult(claim=claim, order=order, replayed=replayed)
