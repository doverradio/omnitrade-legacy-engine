from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.orchestration.autonomous_position_exit_authority import NONTERMINAL_CUSTODY_STATES, _digest, _evaluation

OPEN_ORDER_STATES = ("PENDING_CONFIRMATION", "VALIDATING", "SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED", "UNKNOWN")


@dataclass(frozen=True, slots=True)
class ExitOrderResult:
    claim_id: uuid.UUID
    order_id: uuid.UUID
    requested_base_quantity: Decimal
    normalized_base_quantity: Decimal
    expected_quote_proceeds: Decimal
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ExitOrderPollResult:
    discovered: int
    constructed: int
    failed: int


def _fail(message: str) -> None:
    raise InvalidRequestError(message=message)


def normalize_provider_quantity(*, quantity: Decimal, step: Decimal | None,
                                supports_fractional: bool) -> Decimal:
    if quantity <= 0:
        _fail("Claimed SELL quantity is not positive")
    if not supports_fractional:
        step = Decimal("1") if step is None else step
    if step is None or step <= 0:
        _fail("Provider quantity precision is unavailable")
    normalized = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if normalized <= 0:
        _fail("Provider-normalized SELL quantity is dust or zero")
    if normalized > quantity:
        _fail("Provider normalization increased SELL quantity")
    return normalized


async def _construct_locked(*, db: AsyncSession, claim_id: uuid.UUID, now: datetime) -> ExitOrderResult:
    claim = await db.scalar(select(AutonomousExecutionClaim).where(
        AutonomousExecutionClaim.claim_id == claim_id,
    ).with_for_update().limit(1))
    if claim is None:
        _fail("Autonomous SELL execution claim not found")
    if claim.live_order_id is not None:
        order = await db.get(LiveCryptoOrder, claim.live_order_id)
        custody = None if claim.custody_id is None else await db.get(AutonomousPositionCustody, claim.custody_id)
        authority = None if claim.exit_authority_id is None else await db.get(AutonomousPositionExitAuthority, claim.exit_authority_id)
        current = Decimal("0") if custody is None else await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product,
        )
        if (order is None or order.execution_claim_id != claim.claim_id or claim.claim_status != "EXECUTION_STARTED"
                or claim.expires_at is None or now >= claim.expires_at
                or order.status != "PENDING_CONFIRMATION" or order.provider_order_id is not None
                or order.submitted_at is not None or order.provider_submission_connected
                or order.construction_expires_at is None or now >= order.construction_expires_at
                or custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES
                or custody.active_sell_order_id != order.live_crypto_order_id
                or authority is None or authority.authority_state != "RESERVED" or authority.revoked_at is not None
                or authority.reserved_order_id != order.live_crypto_order_id
                or current != Decimal(claim.claimed_base_quantity)):
            _fail("Existing constructed SELL order is expired, revoked, terminal, submitted, or quantity-invalid")
        return ExitOrderResult(claim.claim_id, order.live_crypto_order_id,
                               Decimal(order.requested_base_quantity), Decimal(order.normalized_base_quantity),
                               Decimal(order.expected_quote_proceeds), True)
    if (claim.claim_status != "CLAIMED" or claim.expires_at is None or now >= claim.expires_at
            or claim.reconciliation_state is not None or claim.side != "SELL"
            or claim.exposure_effect != "REDUCE_ONLY" or Decimal(str(claim.capital_deployment_amount or 0)) != 0
            or claim.provider_submission_connected):
        _fail("Claim is not an unexpired provider-disconnected REDUCE_ONLY SELL")

    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == claim.custody_id,
    ).with_for_update().limit(1))
    authority = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == claim.exit_authority_id,
    ).with_for_update().limit(1))
    package = await db.scalar(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.package_id == claim.package_id,
    ).with_for_update().limit(1))
    activation = await db.scalar(select(CanonicalProvingActivation).where(
        CanonicalProvingActivation.activation_id == claim.activation_id,
    ).with_for_update().limit(1))
    if (custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES
            or custody.terminal_at is not None or custody.active_sell_order_id is not None):
        _fail("Custody is terminal or unavailable")
    if (authority is None or authority.authority_state != "RESERVED" or authority.revoked_at is not None
            or authority.consumed_at is not None or now >= authority.expires_at
            or authority.reserved_claim_id != claim.claim_id or authority.reserved_activation_id != claim.activation_id):
        _fail("Continuing authority is not valid and bound to the claim")
    if (package is None or package.package_state != "ACTIVATED" or package.superseded_at is not None
            or activation is None or activation.activation_state != "ACTIVE" or now >= activation.expires_at
            or activation.package_id != package.package_id):
        _fail("Canonical package or activation is stale, superseded, or mismatched")

    evaluation = _evaluation(custody)
    if (evaluation.get("disposition") != "EXIT_RECOMMENDED" or evaluation.get("price_fresh") is not True
            or _digest(evaluation) != claim.evaluation_integrity_hash):
        _fail("Exit evaluation is not fresh and EXIT_RECOMMENDED")
    current = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=claim.profile_id, symbol=claim.product,
    )
    requested = Decimal(str(claim.claimed_base_quantity or 0))
    maximum = Decimal(str(claim.maximum_authorized_base_quantity or 0))
    if (current <= 0 or current != custody.observed_remaining_quantity or requested != current
            or requested > maximum or requested > authority.maximum_sell_quantity
            or requested != Decimal(str(package.proposed_base_quantity or 0))):
        _fail("Current owned SELL quantity is changed, ambiguous, or excessive")
    scope = (claim.custody_id, claim.account_id, claim.profile_id, claim.connection_id,
             claim.provider, claim.environment, claim.product, claim.originating_buy_claim_id,
             claim.originating_reconciliation_event_id, claim.proof_eligible)
    custody_scope = (custody.custody_id, custody.paper_account_id, custody.live_trading_profile_id,
                     custody.exchange_connection_id, custody.provider, custody.environment, custody.product,
                     custody.buy_claim_id, custody.buy_reconciliation_event_id, custody.proof_eligible)
    if scope != custody_scope or claim.disqualification_reason != custody.disqualification_reason:
        _fail("Claim and custody scope or proof classification mismatch")
    if (authority.custody_id != custody.custody_id or authority.proof_eligible != claim.proof_eligible
            or authority.side != "SELL" or authority.exposure_effect != "REDUCE_ONLY"):
        _fail("Claim and continuing-authority scope mismatch")

    preview = await db.get(CryptoOrderPreview, claim.preview_id)
    if (preview is None or preview.crypto_order_preview_id != package.crypto_order_preview_id
            or preview.side != "SELL" or preview.status != "PREVIEW_READY"
            or preview.risk_verdict != "approved_for_preview"
            or Decimal(str(preview.base_size or 0)) != requested):
        _fail("Canonical preview or risk evidence mismatch")
    asset = await db.scalar(select(Asset).where(
        Asset.symbol == claim.product.split("-")[0], Asset.exchange == claim.provider,
        Asset.is_active.is_(True),
    ).limit(1))
    if asset is None or asset.min_order_notional is None:
        _fail("Provider product metadata is unavailable")
    normalized = normalize_provider_quantity(
        quantity=requested,
        step=None if asset.qty_step_size is None else Decimal(str(asset.qty_step_size)),
        supports_fractional=bool(asset.supports_fractional),
    )
    claim_proceeds = Decimal(str(claim.expected_quote_proceeds or 0))
    expected_proceeds = claim_proceeds * normalized / requested
    if claim_proceeds <= 0 or expected_proceeds < Decimal(str(asset.min_order_notional)):
        _fail("Provider-normalized SELL quantity is below minimum order notional")

    unresolved_order = await db.scalar(select(LiveCryptoOrder.live_crypto_order_id).where(
        LiveCryptoOrder.custody_id == custody.custody_id,
        LiveCryptoOrder.status.in_(OPEN_ORDER_STATES),
    ).limit(1))
    unresolved_reconciliation = await db.scalar(select(LiveReconciliationEvent.id).where(
        LiveReconciliationEvent.live_trading_profile_id == claim.profile_id,
        LiveReconciliationEvent.reconciliation_status.in_(("open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch")),
    ).limit(1))
    if unresolved_order is not None or unresolved_reconciliation is not None:
        _fail("Unresolved SELL order, submission, or reconciliation exists")

    order_id = uuid.uuid4()
    client_order_id = f"autonomous-exit:{claim.claim_id}:v{claim.claim_version}"
    order = LiveCryptoOrder(
        live_crypto_order_id=order_id, crypto_order_preview_id=package.crypto_order_preview_id,
        exchange_connection_id=claim.connection_id, provider=claim.provider, environment=claim.environment,
        product_id=claim.product, side="SELL", order_type="market",
        requested_quote_size=expected_proceeds, client_order_id=client_order_id,
        status="PENDING_CONFIRMATION", risk_event_id=claim.risk_event_id,
        decision_record_id=package.decision_record_id, provider_order_id=None,
        provider_status=None, submitted_at=None, audit_correlation_id=claim.audit_correlation_id,
        execution_claim_id=claim.claim_id, claim_version=claim.claim_version,
        custody_id=claim.custody_id, evaluation_integrity_hash=claim.evaluation_integrity_hash,
        exit_authority_id=claim.exit_authority_id, exit_authority_version=claim.exit_authority_version,
        activation_id=claim.activation_id, originating_buy_claim_id=claim.originating_buy_claim_id,
        originating_reconciliation_event_id=claim.originating_reconciliation_event_id,
        exposure_effect="REDUCE_ONLY", requested_base_quantity=requested,
        normalized_base_quantity=normalized, maximum_authorized_base_quantity=maximum,
        expected_quote_proceeds=expected_proceeds, capital_deployment_amount=Decimal("0"),
        proof_eligible=claim.proof_eligible, disqualification_reason=claim.disqualification_reason,
        construction_expires_at=claim.expires_at, provider_submission_connected=False,
        safe_provider_response={
            "autonomous_execution_claim_id": str(claim.claim_id),
            "canonical_preview_package_id": str(package.package_id),
            "custody_id": str(custody.custody_id), "exit_authority_id": str(authority.authority_id),
            "requested_base_quantity": format(requested, "f"),
            "provider_normalized_base_quantity": format(normalized, "f"),
            "provider_call_made": False, "provider_submission_connected": False,
            "provider_order_id": None, "sell_reconciliation_created": False,
            "custody_closed": False, "autonomous_proof_sell_ready": False,
        }, created_at=now, updated_at=now,
    )
    db.add(order)
    await db.flush()
    claim.live_order_id = order_id; claim.claim_status = "EXECUTION_STARTED"
    claim.recover_after = now; claim.updated_at = now
    custody.active_sell_order_id = order_id; custody.updated_at = now
    authority.reserved_order_id = order_id; authority.updated_at = now
    authority.last_order_failure_at = None; authority.last_order_failure_code = None
    authority.last_order_exception_class = None; authority.last_order_failure_retryable = None
    db.add(AuditLog(
        actor="system:autonomous_position_exit_order",
        action="autonomous_position_exit.order_constructed",
        entity_type="autonomous_execution_claim", entity_id=claim.claim_id,
        before_state={"claim_status": "CLAIMED", "live_order_id": None},
        after_state={
            "order_id": str(order_id), "order_status": "PENDING_CONFIRMATION", "side": "SELL",
            "exposure_effect": "REDUCE_ONLY", "requested_base_quantity": format(requested, "f"),
            "normalized_base_quantity": format(normalized, "f"),
            "expected_quote_proceeds": format(expected_proceeds, "f"), "capital_deployment_amount": "0",
            "proof_eligible": claim.proof_eligible, "disqualification_reason": claim.disqualification_reason,
            "provider_submission_connected": False, "provider_order_id": None,
            "kraken_contacted": False, "sell_reconciliation_created": False,
            "custody_closed": False, "autonomous_proof_sell_ready": False,
        },
    ))
    await db.flush()
    return ExitOrderResult(claim.claim_id, order_id, requested, normalized, expected_proceeds, False)


async def construct_autonomous_exit_order(*, db: AsyncSession, claim_id: uuid.UUID,
                                          now: datetime | None = None) -> ExitOrderResult:
    observed_at = now or datetime.now(timezone.utc)
    async with db.begin_nested():
        return await _construct_locked(db=db, claim_id=claim_id, now=observed_at)


def _safe_failure(exc: Exception) -> tuple[str, bool]:
    message = str(getattr(exc, "message", "") or "order_construction_internal_error")
    permanent_tokens = ("scope", "proof classification", "not found", "reduce_only")
    return message.lower().replace(" ", "_"), not any(token in message.lower() for token in permanent_tokens)


async def construct_due_exit_orders(*, db: AsyncSession, now: datetime | None = None,
                                    limit: int = 10) -> ExitOrderPollResult:
    observed_at = now or datetime.now(timezone.utc)
    ids = list((await db.scalars(select(AutonomousExecutionClaim.claim_id).where(
        AutonomousExecutionClaim.custody_id.is_not(None),
        AutonomousExecutionClaim.claim_status == "CLAIMED",
        AutonomousExecutionClaim.live_order_id.is_(None),
        AutonomousExecutionClaim.provider_submission_connected.is_(False),
    ).order_by(AutonomousExecutionClaim.claimed_at.asc()).limit(limit).with_for_update(skip_locked=True))).all())
    constructed = failed = 0
    for claim_id in ids:
        try:
            await construct_autonomous_exit_order(db=db, claim_id=claim_id, now=observed_at)
            constructed += 1
        except Exception as exc:
            failed += 1
            claim = await db.get(AutonomousExecutionClaim, claim_id)
            authority = None if claim is None or claim.exit_authority_id is None else await db.get(AutonomousPositionExitAuthority, claim.exit_authority_id)
            if authority is not None:
                code, retryable = _safe_failure(exc)
                authority.last_order_failure_at = observed_at; authority.last_order_failure_code = code
                authority.last_order_exception_class = type(exc).__name__
                authority.last_order_failure_retryable = retryable
                db.add(AuditLog(
                    actor="system:autonomous_position_exit_order",
                    action="autonomous_position_exit.order_construction_failed",
                    entity_type="autonomous_execution_claim", entity_id=claim_id,
                    before_state={"claim_status": claim.claim_status},
                    after_state={"failure_code": code, "retryable": retryable,
                                 "provider_call_made": False, "provider_submission_connected": False},
                ))
                await db.flush()
    return ExitOrderPollResult(len(ids), constructed, failed)


async def inspect_autonomous_exit_order(*, db: AsyncSession, claim_id: uuid.UUID) -> dict[str, Any]:
    claim = await db.get(AutonomousExecutionClaim, claim_id)
    order = None if claim is None or claim.live_order_id is None else await db.get(LiveCryptoOrder, claim.live_order_id)
    if claim is None:
        return {"found": False, "blockers": ["claim_not_found"], "retryable": False}
    return {
        "found": True, "custody_id": None if claim.custody_id is None else str(claim.custody_id),
        "evaluation_integrity_hash": claim.evaluation_integrity_hash,
        "authority_id": None if claim.exit_authority_id is None else str(claim.exit_authority_id),
        "package_id": str(claim.package_id), "activation_id": str(claim.activation_id),
        "claim_id": str(claim.claim_id), "order_id": None if order is None else str(order.live_crypto_order_id),
        "requested_base_quantity": None if order is None else format(Decimal(order.requested_base_quantity), "f"),
        "normalized_base_quantity": None if order is None else format(Decimal(order.normalized_base_quantity), "f"),
        "expected_quote_proceeds": None if order is None else format(Decimal(order.expected_quote_proceeds), "f"),
        "capital_deployment_amount": None if order is None else "0", "claim_state": claim.claim_status,
        "order_state": None if order is None else order.status,
        "client_order_id": None if order is None else order.client_order_id,
        "expires_at": None if order is None else order.construction_expires_at.isoformat(),
        "proof_eligible": claim.proof_eligible, "disqualification_reason": claim.disqualification_reason,
        "exchange_order_constructed": order is not None, "provider_submission_connected": False,
        "provider_order_id": None, "kraken_contacted": False, "sell_reconciliation_created": False,
        "custody_closed": False, "autonomous_proof_sell_ready": False,
        "blockers": [], "retryable": order is None,
    }
