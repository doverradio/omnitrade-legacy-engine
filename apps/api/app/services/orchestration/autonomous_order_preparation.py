from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.schemas.capital_campaign_domain import CommissionedEntryExecutionRequest, CommissionedReadinessRequest
from app.services.capital_campaign_domain.activated_commissioned_entry import execute_activated_commissioned_entry
from app.services.capital_campaign_domain.commissioned_readiness_preview import generate_commissioned_campaign_preview
from app.services.risk.risk_context import resolve_effective_risk_policy


@dataclass(frozen=True)
class AutonomousOrderPreparationResult:
    claim: AutonomousExecutionClaim
    order: LiveCryptoOrder
    replayed: bool


def _balance(connection: ExchangeConnection) -> Decimal:
    for row in connection.balances or []:
        if str(row.get("currency") or row.get("asset") or "").upper() in {"USD", "ZUSD"}:
            return Decimal(str(row.get("available") or row.get("balance") or "0"))
    return Decimal("0")


def _identity_binding(*, claim, package, activation, order, preview) -> dict[str, object]:
    return {
        "package_id": str(package.package_id), "activation_id": str(activation.activation_id),
        "claim_id": str(claim.claim_id), "live_order_id": str(order.live_crypto_order_id),
        "campaign_id": str(claim.campaign_id), "campaign_version": claim.campaign_version,
        "mandate_id": str(claim.mandate_id), "mandate_version_id": str(claim.mandate_version_id),
        "account_id": str(claim.account_id), "profile_id": str(claim.profile_id),
        "connection_id": str(claim.connection_id), "provider": claim.provider,
        "environment": claim.environment, "product": claim.product,
        "side": order.side, "quantity": str(preview.estimated_base_size), "order_type": order.order_type,
        "crypto_order_preview_id": str(package.crypto_order_preview_id),
    }


async def _canonical_preview_identity(
    *, db: AsyncSession, claim: AutonomousExecutionClaim, package: CanonicalPreviewPackage,
    activation: CanonicalProvingActivation, preview: CryptoOrderPreview,
):
    definition = await db.scalar(select(CapitalCampaignDefinition).where(
        CapitalCampaignDefinition.campaign_id == claim.campaign_id,
        CapitalCampaignDefinition.version == claim.campaign_version,
    ).limit(1))
    connection = await db.scalar(select(ExchangeConnection).where(ExchangeConnection.exchange_connection_id == claim.connection_id).limit(1))
    mandate = await db.scalar(select(AutonomousCapitalMandate).where(AutonomousCapitalMandate.mandate_id == claim.mandate_id).limit(1))
    mandate_version = await db.scalar(select(AutonomousCapitalMandateVersion).where(
        AutonomousCapitalMandateVersion.mandate_version_id == claim.mandate_version_id,
    ).limit(1))
    if definition is None or connection is None or mandate is None or mandate_version is None:
        _fail("canonical_preview_identity_evidence_missing")
    observed_at = connection.last_verified_at or preview.created_at
    balance_at = connection.last_successful_sync_at or observed_at
    heartbeat_at = connection.last_heartbeat_at or observed_at
    price = preview.estimated_average_price or preview.best_ask or preview.best_bid
    if observed_at is None or balance_at is None or heartbeat_at is None or preview.created_at is None or price is None:
        _fail("canonical_preview_identity_evidence_missing")
    fee = Decimal(str(preview.estimated_fee or "0.01"))
    slippage = Decimal(str(preview.estimated_slippage or "0.01"))
    price_max_age = get_settings().live_crypto_price_max_age_seconds
    request = CommissionedReadinessRequest(
        campaign_id=claim.campaign_id, version=claim.campaign_version,
        provider=claim.provider, environment=claim.environment, instrument=claim.product,
        requested_quote_amount=package.risk_approved_amount, quote_currency="USD",
        idempotency_key=f"autonomous-entry-readiness:{claim.claim_id}",
        live_trading_profile_id=claim.profile_id, account_id=claim.account_id,
        mandate_id=claim.mandate_id, mandate_version_id=claim.mandate_version_id,
        expected_mandate_version_number=mandate_version.version_number,
        expected_risk_policy_id=definition.risk_policy_id,
        expected_risk_policy_version=definition.risk_policy_version,
        approval_checkpoint_type="bounded_proving_entry",
        authorization_expires_at=activation.expires_at,
        provider_capability_evidence={"supported": bool(connection.credentials_valid), "observed_at": observed_at.isoformat(), "source": "exchange_connection"},
        connectivity_evidence={"reachable": str(connection.status or "").lower() == "connected", "observed_at": heartbeat_at.isoformat(), "source": "exchange_connection"},
        balance_evidence={"available_quote_balance": str(_balance(connection)), "observed_at": balance_at.isoformat(), "source": "exchange_connection"},
        market_data_evidence={"observed_at": preview.created_at.isoformat(), "max_age_seconds": price_max_age, "source": "canonical_preview"},
        price_evidence={"reference_price": str(price), "observed_at": preview.created_at.isoformat(), "max_age_seconds": price_max_age, "source": "canonical_preview"},
        minimum_order_evidence={
            "minimum_quote_amount": "5",
            "minimum_base_quantity": str(mandate_version.entry_policy.get("minimum_base_quantity") or "0.00000001"),
            "observed_at": observed_at.isoformat(), "source": "mandate_version",
        },
        fee_slippage_evidence={
            "estimated_entry_fee": str(fee), "estimated_future_exit_fee": str(fee),
            "estimated_slippage": str(slippage), "source": "canonical_preview",
        },
        runtime_readiness_evidence={"ready": True, "observed_at": observed_at.isoformat(), "source": "canonical_preview_commission_command"},
        reconciliation_evidence={}, manual_review_evidence={"required": False},
    )
    commissioned_preview = await generate_commissioned_campaign_preview(db=db, request=request)
    return request, commissioned_preview


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
    readiness, commissioned_preview = await _canonical_preview_identity(
        db=db, claim=claim, package=package, activation=activation, preview=preview,
    )
    evidence = order.safe_provider_response if isinstance(order.safe_provider_response, dict) else {}
    persisted_hash = str(evidence.get("commissioned_preview_identity_hash") or "").strip()
    if not persisted_hash:
        _fail("canonical_preview_identity_missing")
    if persisted_hash != commissioned_preview.preview_identity_hash:
        _fail("canonical_preview_identity_mismatch")
    if persisted_hash == package.input_fingerprint:
        _fail("input_fingerprint_substitution_rejected")
    if evidence.get("commissioned_preview_identity_binding") != _identity_binding(
        claim=claim, package=package, activation=activation, order=order, preview=preview,
    ):
        _fail("canonical_preview_identity_binding_mismatch")
    policy = await resolve_effective_risk_policy(db=db, paper_account_id=claim.account_id)
    request = CommissionedEntryExecutionRequest.model_construct(
        campaign_id=claim.campaign_id, version=claim.campaign_version, actor=claim.claim_owner,
        idempotency_key=f"autonomous-entry:{claim.claim_id}", readiness_request=readiness,
        expected_preview_identity_hash=persisted_hash,
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
    preview = await db.scalar(
        select(CryptoOrderPreview).where(CryptoOrderPreview.crypto_order_preview_id == package.crypto_order_preview_id).limit(1)
    )
    if preview is None:
        _fail("canonical_preview_identity_evidence_missing")
    _readiness, commissioned_preview = await _canonical_preview_identity(
        db=db, claim=claim, package=package, activation=activation, preview=preview,
    )
    canonical_hash = commissioned_preview.preview_identity_hash
    if not canonical_hash or canonical_hash == package.input_fingerprint:
        _fail("input_fingerprint_substitution_rejected")
    persisted_hash = str(evidence.get("commissioned_preview_identity_hash") or "").strip()
    if persisted_hash and persisted_hash != canonical_hash:
        _fail("canonical_preview_identity_mismatch")
    binding = _identity_binding(claim=claim, package=package, activation=activation, order=order, preview=preview)
    persisted_binding = evidence.get("commissioned_preview_identity_binding")
    if persisted_binding is not None and persisted_binding != binding:
        _fail("canonical_preview_identity_binding_mismatch")
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
            "commissioned_preview_identity_hash": canonical_hash,
            "commissioned_preview_identity_binding": binding,
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
    elif persisted_hash != canonical_hash:
        _fail("canonical_preview_identity_missing")
    return AutonomousOrderPreparationResult(claim=claim, order=order, replayed=replayed)
