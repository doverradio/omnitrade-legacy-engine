from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.exchange_connection import ExchangeConnection
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.orchestration.autonomous_position_exit_authority import NONTERMINAL_CUSTODY_STATES, _digest, _evaluation

NONTERMINAL_CLAIMS = ("CLAIMED", "EXECUTION_STARTED", "SUBMISSION_PENDING", "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED")
OPEN_ORDERS = ("PENDING_CONFIRMATION", "VALIDATING", "SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED", "UNKNOWN")
CLAIM_TTL = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class ExitActivationClaimResult:
    authority_id: uuid.UUID
    activation_id: uuid.UUID
    claim_id: uuid.UUID
    package_id: uuid.UUID
    quantity: Decimal
    expected_quote_proceeds: Decimal
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ExitActivationPollResult:
    discovered: int
    activated: int
    failed: int


def _fail(message: str) -> None:
    raise InvalidRequestError(message=message)


async def _activate_locked(*, db: AsyncSession, authority_id: uuid.UUID, now: datetime) -> ExitActivationClaimResult:
    authority = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == authority_id,
    ).with_for_update().limit(1))
    if authority is None:
        _fail("Continuing exit authority not found")
    if authority.reserved_activation_id is not None or authority.reserved_claim_id is not None:
        if authority.reserved_activation_id is None or authority.reserved_claim_id is None:
            _fail("Continuing exit authority has incomplete activation binding")
        claim = await db.get(AutonomousExecutionClaim, authority.reserved_claim_id)
        activation = await db.get(CanonicalProvingActivation, authority.reserved_activation_id)
        if claim is None or activation is None or claim.activation_id != activation.activation_id or claim.exit_authority_id != authority.authority_id:
            _fail("Continuing exit authority activation replay is inconsistent")
        custody = await db.get(AutonomousPositionCustody, authority.custody_id)
        current_quantity = Decimal("0") if custody is None else await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product,
        )
        if (authority.authority_state != "RESERVED" or authority.revoked_at is not None
                or authority.consumed_at is not None or now >= authority.expires_at
                or claim.claim_status not in NONTERMINAL_CLAIMS or claim.expires_at is None or now >= claim.expires_at
                or custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES
                or custody.terminal_at is not None or current_quantity != Decimal(claim.claimed_base_quantity)):
            _fail("Existing exit claim is expired, revoked, terminal, or quantity-invalid")
        return ExitActivationClaimResult(authority.authority_id, activation.activation_id, claim.claim_id,
                                         claim.package_id, Decimal(claim.claimed_base_quantity),
                                         Decimal(claim.expected_quote_proceeds), True)
    if authority.authority_state != "RESERVED" or authority.reserved_decision_id is None or authority.reserved_package_id is None:
        _fail("Continuing exit authority is not completely RESERVED")
    if now >= authority.expires_at or authority.reservation_expires_at is None or now >= authority.reservation_expires_at:
        _fail("Continuing exit authority reservation is expired")
    if authority.side != "SELL" or authority.exposure_effect != "REDUCE_ONLY" or not authority.buy_forbidden or not authority.increased_exposure_forbidden:
        _fail("Continuing exit authority is not reduce-only SELL authority")

    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == authority.custody_id,
    ).with_for_update().limit(1))
    package = await db.scalar(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.package_id == authority.reserved_package_id,
    ).with_for_update().limit(1))
    if custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES or custody.terminal_at is not None:
        _fail("Custody is terminal or unavailable")
    if package is None or package.package_state != "READY" or package.superseded_at is not None or package.preview_expires_at <= now:
        _fail("Canonical SELL package is stale, superseded, or not READY")
    evaluation = _evaluation(custody)
    if evaluation.get("disposition") != "EXIT_RECOMMENDED" or evaluation.get("price_fresh") is not True or _digest(evaluation) != authority.evaluation_integrity_hash:
        _fail("Exit evaluation is not fresh and EXIT_RECOMMENDED")
    evaluated_at = datetime.fromisoformat(str(evaluation.get("evaluated_at")))
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    if evaluated_at > now or custody.latest_exit_evaluation_at != evaluated_at:
        _fail("Exit evaluation identity or timestamp changed")

    quantity = await compute_signed_owned_quantity(db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product)
    proposed = Decimal(str(package.proposed_base_quantity or 0))
    maximum = Decimal(str(package.maximum_authorized_base_quantity or 0))
    proceeds = Decimal(str(package.expected_quote_proceeds or 0))
    deployed = Decimal(str(package.capital_deployment_amount or 0))
    if quantity <= 0 or quantity != custody.observed_remaining_quantity or proposed != quantity or proposed > maximum or proposed > authority.maximum_sell_quantity:
        _fail("Current owned SELL quantity is changed, ambiguous, or excessive")
    if proceeds <= 0 or deployed != 0 or package.side != "SELL":
        _fail("Canonical package does not represent a zero-capital SELL reduction")

    decision = await db.get(DecisionRecord, authority.reserved_decision_id)
    preview = await db.get(CryptoOrderPreview, package.crypto_order_preview_id)
    risk_event = await db.get(RiskEvent, package.risk_event_id)
    profile = await db.get(LiveTradingProfile, custody.live_trading_profile_id)
    account = await db.get(PaperAccount, custody.paper_account_id)
    connection = await db.get(ExchangeConnection, custody.exchange_connection_id)
    buy_claim = await db.get(AutonomousExecutionClaim, custody.buy_claim_id)
    reconciliation = await db.get(LiveReconciliationEvent, custody.buy_reconciliation_event_id)
    if decision is None or preview is None or risk_event is None or package.decision_record_id != decision.decision_id:
        _fail("Decision or preview identity is unavailable")
    if (profile is None or profile.paper_account_id != custody.paper_account_id or account is None
            or connection is None or connection.provider != custody.provider
            or connection.environment != custody.environment
            or buy_claim is None or buy_claim.claim_id != custody.buy_claim_id
            or buy_claim.profile_id != custody.live_trading_profile_id
            or buy_claim.account_id != custody.paper_account_id
            or buy_claim.connection_id != custody.exchange_connection_id
            or buy_claim.provider != custody.provider or buy_claim.environment != custody.environment
            or buy_claim.product != custody.product
            or reconciliation is None or reconciliation.id != custody.buy_reconciliation_event_id):
        _fail("Current profile, account, connection, or BUY lineage mismatch")
    details = decision.execution_details if isinstance(decision.execution_details, dict) else {}
    construction = details.get("construction_time_economics") if isinstance(details.get("construction_time_economics"), dict) else {}
    exact_scope = (
        str(custody.custody_id), str(authority.authority_id), str(custody.live_trading_profile_id),
        str(custody.paper_account_id), str(custody.exchange_connection_id), custody.provider,
        custody.environment, custody.product, "SELL", "REDUCE_ONLY",
    )
    evidence_scope = (
        details.get("custody_id"), details.get("exit_authority_id"), details.get("live_trading_profile_id"),
        details.get("paper_account_id"), details.get("exchange_connection_id"), details.get("provider"),
        details.get("environment"), details.get("product"), details.get("side"), details.get("exposure_effect"),
    )
    authority_scope = (
        authority.custody_id, authority.live_trading_profile_id, authority.paper_account_id,
        authority.exchange_connection_id, authority.provider, authority.environment, authority.product,
        authority.originating_buy_claim_id, authority.originating_reconciliation_event_id,
    )
    custody_scope = (
        custody.custody_id, custody.live_trading_profile_id, custody.paper_account_id,
        custody.exchange_connection_id, custody.provider, custody.environment, custody.product,
        custody.buy_claim_id, custody.buy_reconciliation_event_id,
    )
    if exact_scope != evidence_scope or authority_scope != custody_scope:
        _fail("Custody, authority, decision, or package scope mismatch")
    if (preview.side != "SELL" or preview.status != "PREVIEW_READY" or preview.risk_verdict != "approved_for_preview"
            or preview.crypto_order_preview_id != package.crypto_order_preview_id
            or preview.decision_record_id != decision.decision_id
            or Decimal(str(preview.base_size or 0)) != quantity
            or Decimal(str(preview.estimated_quote_size or 0)) != proceeds
            or str(construction.get("estimated_gross_quote_proceeds")) != format(proceeds, "f")):
        _fail("Preview, economics, or order-level risk evidence mismatch")
    if (authority.proof_eligible != custody.proof_eligible
            or (authority.classification == "PROOF_ELIGIBLE_AUTONOMOUS") != authority.proof_eligible):
        _fail("Proof classification mismatch")

    mandate = await db.get(AutonomousCapitalMandate, custody.mandate_id)
    mandate_version = await db.get(AutonomousCapitalMandateVersion, custody.mandate_version_id)
    # The entry mandate supplies immutable lineage, not fresh SELL authority.
    # A protective exit must remain possible after entry authority expires;
    # the RESERVED continuing exit authority is the current SELL authority.
    if (mandate is None or mandate.mandate_id != custody.mandate_id
            or mandate_version is None or mandate_version.mandate_version_id != custody.mandate_version_id
            or mandate_version.mandate_id != mandate.mandate_id):
        _fail("Entry mandate lineage does not match custody")

    existing_activation = await db.scalar(select(CanonicalProvingActivation.activation_id).where(
        CanonicalProvingActivation.live_trading_profile_id == custody.live_trading_profile_id,
        CanonicalProvingActivation.product == custody.product,
        CanonicalProvingActivation.activation_state == "ACTIVE",
        CanonicalProvingActivation.expires_at > now,
    ).limit(1))
    existing_claim = await db.scalar(select(AutonomousExecutionClaim.claim_id).where(
        AutonomousExecutionClaim.profile_id == custody.live_trading_profile_id,
        AutonomousExecutionClaim.product == custody.product,
        AutonomousExecutionClaim.claim_status.in_(NONTERMINAL_CLAIMS),
    ).limit(1))
    existing_order = await db.scalar(select(LiveCryptoOrder.live_crypto_order_id).where(
        LiveCryptoOrder.exchange_connection_id == custody.exchange_connection_id,
        LiveCryptoOrder.product_id == custody.product, LiveCryptoOrder.side == "SELL",
        LiveCryptoOrder.status.in_(OPEN_ORDERS),
    ).limit(1))
    latest_reconciliations = select(
        LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
        func.max(LiveReconciliationEvent.sequence_number).label("sequence_number"),
    ).where(
        LiveReconciliationEvent.live_trading_profile_id == custody.live_trading_profile_id,
    ).group_by(LiveReconciliationEvent.live_crypto_order_id).subquery()
    existing_reconciliation = await db.scalar(select(LiveReconciliationEvent.id).join(
        latest_reconciliations,
        (latest_reconciliations.c.order_id == LiveReconciliationEvent.live_crypto_order_id)
        & (latest_reconciliations.c.sequence_number == LiveReconciliationEvent.sequence_number),
    ).where(
        LiveReconciliationEvent.live_trading_profile_id == custody.live_trading_profile_id,
        LiveReconciliationEvent.reconciliation_status.in_(("open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch")),
    ).limit(1))
    if any((existing_activation, existing_claim, existing_order, existing_reconciliation)):
        _fail("Unresolved SELL activation, claim, order, submission, or reconciliation exists")

    expiration = min(authority.expires_at, package.preview_expires_at)
    activation = CanonicalProvingActivation(
        activation_id=uuid.uuid4(), package_id=package.package_id, approval_event_id=None,
        authority_source="CONTINUING_EXIT", mandate_evaluation_id=None,
        authority_audit_correlation_id=preview.audit_correlation_id, dry_run_live_crypto_order_id=None,
        campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        paper_account_id=custody.paper_account_id, live_trading_profile_id=custody.live_trading_profile_id,
        provider=custody.provider, environment=custody.environment, product=custody.product, side="SELL",
        max_order_amount=proceeds, max_deployed_capital=Decimal("0"),
        maximum_authorized_base_quantity=maximum, no_leverage=True,
        activated_at=now, expires_at=expiration, activation_state="ACTIVE",
    )
    claim = AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=activation.activation_id,
        campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        mandate_id=custody.mandate_id, mandate_version_id=custody.mandate_version_id,
        account_id=custody.paper_account_id, profile_id=custody.live_trading_profile_id,
        connection_id=custody.exchange_connection_id, provider=custody.provider,
        environment=custody.environment, product=custody.product, side="SELL",
        claim_version=1, idempotency_key=f"autonomous-position-exit-claim:{authority.authority_id}:v{authority.authority_version}",
        custody_id=custody.custody_id, evaluation_integrity_hash=authority.evaluation_integrity_hash,
        exit_authority_id=authority.authority_id, exit_authority_version=authority.authority_version,
        originating_buy_claim_id=custody.buy_claim_id,
        originating_reconciliation_event_id=custody.buy_reconciliation_event_id,
        exposure_effect="REDUCE_ONLY", claimed_base_quantity=quantity,
        maximum_authorized_base_quantity=maximum, expected_quote_proceeds=proceeds,
        capital_deployment_amount=Decimal("0"), preview_id=preview.crypto_order_preview_id,
        risk_event_id=package.risk_event_id, audit_correlation_id=preview.audit_correlation_id,
        proof_eligible=custody.proof_eligible, disqualification_reason=custody.disqualification_reason,
        expires_at=expiration, authority_evidence={
            "policy": authority.policy_evidence, "risk": authority.risk_evidence,
            "evaluation": evaluation, "decision_id": str(decision.decision_id),
            "originating_buy_package_id": str(custody.buy_package_id),
            "automatic_proof_sell_ready": False, "exchange_order_constructed": False,
            "provider_submission_connected": False, "kraken_contacted": False,
        }, provider_submission_connected=False, claim_status="CLAIMED", claimed_at=now,
        claim_owner="system:autonomous_position_exit_activation", recover_after=now + CLAIM_TTL,
        attempt_count=1,
    )
    # Persist the FK parent first. Both rows remain inside the caller-owned
    # savepoint, but PostgreSQL must see the activation before the claim's
    # immediate activation_id foreign key is checked.
    db.add(activation)
    await db.flush()
    db.add(claim)
    await db.flush()
    package.package_state = "ACTIVATED"
    custody.active_sell_claim_id = claim.claim_id
    authority.reserved_activation_id = activation.activation_id
    authority.reserved_claim_id = claim.claim_id
    authority.last_activation_failure_at = None
    authority.last_activation_failure_code = None
    authority.last_activation_exception_class = None
    authority.last_activation_failure_retryable = None
    authority.updated_at = custody.updated_at = now
    db.add(AuditLog(
        actor="system:autonomous_position_exit_activation",
        action="autonomous_position_exit.activation_claim_created",
        entity_type="autonomous_position_exit_authority", entity_id=authority.authority_id,
        before_state={"authority_state": "RESERVED", "package_state": "READY"},
        after_state={
            "custody_id": str(custody.custody_id), "evaluation_integrity_hash": authority.evaluation_integrity_hash,
            "authority_id": str(authority.authority_id), "decision_id": str(decision.decision_id),
            "package_id": str(package.package_id), "activation_id": str(activation.activation_id),
            "claim_id": str(claim.claim_id), "side": "SELL", "exposure_effect": "REDUCE_ONLY",
            "claimed_base_quantity": format(quantity, "f"), "maximum_authorized_base_quantity": format(maximum, "f"),
            "expected_quote_proceeds": format(proceeds, "f"), "capital_deployment_amount": "0",
            "proof_eligible": custody.proof_eligible, "disqualification_reason": custody.disqualification_reason,
            "claim_status": "CLAIMED", "claim_expires_at": expiration.isoformat(),
            "exchange_order_constructed": False, "provider_submission_connected": False,
            "kraken_contacted": False, "autonomous_proof_sell_ready": False,
        },
    ))
    await db.flush()
    return ExitActivationClaimResult(authority.authority_id, activation.activation_id, claim.claim_id,
                                     package.package_id, quantity, proceeds, False)


async def activate_exit_package_and_claim(*, db: AsyncSession, authority_id: uuid.UUID,
                                          now: datetime | None = None) -> ExitActivationClaimResult:
    """Atomically activate reserved exit paperwork and persist its claim.

    The caller owns the outer commit. The savepoint prevents any activation,
    claim, authority binding, custody link, package transition, or audit from
    surviving a failure in another member of this unit.
    """
    observed_at = now or datetime.now(timezone.utc)
    async with db.begin_nested():
        return await _activate_locked(db=db, authority_id=authority_id, now=observed_at)


def _safe_failure(exc: Exception) -> tuple[str, bool]:
    message = getattr(exc, "message", "")
    permanent = {
        "Continuing exit authority not found", "Continuing exit authority is not completely RESERVED",
        "Continuing exit authority is not reduce-only SELL authority", "Custody is terminal or unavailable",
        "Proof classification mismatch", "Custody, authority, decision, or package scope mismatch",
        "Entry mandate lineage does not match custody",
    }
    if message:
        return message.lower().replace(" ", "_"), message not in permanent
    return "activation_internal_error", True


async def activate_due_exit_claims(*, db: AsyncSession, now: datetime | None = None,
                                   limit: int = 10) -> ExitActivationPollResult:
    observed_at = now or datetime.now(timezone.utc)
    ids = list((await db.scalars(select(AutonomousPositionExitAuthority.authority_id).where(
        AutonomousPositionExitAuthority.authority_state == "RESERVED",
        AutonomousPositionExitAuthority.reserved_activation_id.is_(None),
        AutonomousPositionExitAuthority.reserved_claim_id.is_(None),
    ).order_by(AutonomousPositionExitAuthority.reserved_at.asc()).limit(limit).with_for_update(skip_locked=True))).all())
    activated = failed = 0
    for authority_id in ids:
        try:
            await activate_exit_package_and_claim(db=db, authority_id=authority_id, now=observed_at)
            activated += 1
        except Exception as exc:
            failed += 1
            code, retryable = _safe_failure(exc)
            row = await db.get(AutonomousPositionExitAuthority, authority_id)
            if row is not None:
                row.last_activation_failure_at = observed_at
                row.last_activation_failure_code = code
                row.last_activation_exception_class = type(exc).__name__
                row.last_activation_failure_retryable = retryable
                db.add(AuditLog(
                    actor="system:autonomous_position_exit_activation",
                    action="autonomous_position_exit.activation_claim_failed",
                    entity_type="autonomous_position_exit_authority", entity_id=authority_id,
                    before_state={"authority_state": row.authority_state},
                    after_state={"failure_code": code, "retryable": retryable,
                                 "exchange_order_constructed": False,
                                 "provider_submission_connected": False},
                ))
                await db.flush()
    return ExitActivationPollResult(len(ids), activated, failed)


async def inspect_exit_activation(*, db: AsyncSession, authority_id: uuid.UUID) -> dict[str, Any]:
    authority = await db.get(AutonomousPositionExitAuthority, authority_id)
    if authority is None:
        return {"found": False, "blockers": ["authority_not_found"], "retryable": False}
    custody = await db.get(AutonomousPositionCustody, authority.custody_id)
    claim = None if authority.reserved_claim_id is None else await db.get(AutonomousExecutionClaim, authority.reserved_claim_id)
    return {
        "found": True, "custody_id": str(authority.custody_id),
        "evaluation_integrity_hash": authority.evaluation_integrity_hash,
        "authority_id": str(authority.authority_id), "authority_state": authority.authority_state,
        "decision_id": None if authority.reserved_decision_id is None else str(authority.reserved_decision_id),
        "package_id": None if authority.reserved_package_id is None else str(authority.reserved_package_id),
        "activation_id": None if authority.reserved_activation_id is None else str(authority.reserved_activation_id),
        "claim_id": None if authority.reserved_claim_id is None else str(authority.reserved_claim_id),
        "claimed_base_quantity": None if claim is None else format(Decimal(claim.claimed_base_quantity), "f"),
        "expected_quote_proceeds": None if claim is None else format(Decimal(claim.expected_quote_proceeds), "f"),
        "capital_deployment_amount": None if claim is None else format(Decimal(claim.capital_deployment_amount), "f"),
        "claim_state": None if claim is None else claim.claim_status,
        "claim_expires_at": None if claim is None else claim.expires_at.isoformat(),
        "proof_eligible": authority.proof_eligible,
        "disqualification_reason": None if custody is None else custody.disqualification_reason,
        "exchange_order_constructed": False, "provider_submission_connected": False,
        "kraken_contacted": False, "autonomous_proof_sell_ready": False,
        "blockers": [], "retryable": True,
    }
