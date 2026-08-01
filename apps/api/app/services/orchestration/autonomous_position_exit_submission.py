from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.registry import get_exchange_provider, require_provider_capabilities
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.live_crypto_orders import _load_decrypted_credentials, _load_exchange_connection
from app.services.orchestration.autonomous_position_exit_authority import NONTERMINAL_CUSTODY_STATES, _digest, _evaluation
from app.services.orchestration.autonomous_position_exit_order import normalize_provider_quantity


@dataclass(frozen=True, slots=True)
class ExitSubmissionResult:
    order_id: uuid.UUID
    status: str
    provider_order_id: str | None
    provider_call_made: bool
    recovered: bool


def _fail(message: str) -> None:
    raise InvalidRequestError(message=message)


async def _commit(db: AsyncSession) -> None:
    if hasattr(db, "commit"):
        await db.commit()


def _audit(*, db: AsyncSession, order_id: uuid.UUID, action: str,
           before: str, after: dict[str, Any]) -> None:
    db.add(AuditLog(
        actor="system:autonomous_position_exit_submission",
        action=action,
        entity_type="live_crypto_order",
        entity_id=order_id,
        before_state={"status": before},
        after_state=after,
    ))


async def _recover_only(*, db: AsyncSession, order: LiveCryptoOrder,
                        claim: AutonomousExecutionClaim, connection: Any,
                        provider: Any, credentials: dict[str, str], now: datetime) -> ExitSubmissionResult:
    try:
        recovered = await provider.lookup_order(
            credentials=credentials,
            environment=order.environment,
            provider_order_id=order.provider_order_id,
            client_order_id=order.client_order_id,
            product_id=order.product_id,
        )
    except Exception as exc:
        order.status = "RECONCILIATION_REQUIRED"
        claim.claim_status = "RECOVERY_REQUIRED"
        order.failure_code = "provider_recovery_ambiguous"
        order.failure_reason = exc.__class__.__name__
        order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": True,
                                        "recovery_attempted": True, "recovery_found": False}
        await db.flush(); await _commit(db)
        return ExitSubmissionResult(order.live_crypto_order_id, order.status, order.provider_order_id, True, False)
    if recovered is None:
        order.status = "RECONCILIATION_REQUIRED"
        claim.claim_status = "RECOVERY_REQUIRED"
        order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": True,
                                        "recovery_attempted": True, "recovery_found": False}
        await db.flush(); await _commit(db)
        return ExitSubmissionResult(order.live_crypto_order_id, order.status, order.provider_order_id, True, False)
    if (recovered.client_order_id != order.client_order_id or recovered.product_id != order.product_id
            or recovered.side != "SELL" or not recovered.provider_order_id):
        _fail("Recovered provider order identity does not match the canonical SELL order")
    before = order.status
    order.provider_order_id = recovered.provider_order_id
    order.provider_status = recovered.status
    order.status = "ACKNOWLEDGED"
    order.acknowledged_at = recovered.acknowledged_at or now
    order.failure_code = None; order.failure_reason = None
    order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": True,
                                    "recovery_attempted": True, "recovery_found": True}
    claim.claim_status = "SUBMISSION_PENDING"; claim.last_error_code = None; claim.updated_at = now
    _audit(db=db, order_id=order.live_crypto_order_id, action="AUTONOMOUS_EXIT_PROVIDER_RECOVERED",
           before=before, after={"status": order.status, "provider_order_id": order.provider_order_id,
                                 "client_order_id": order.client_order_id, "sell_reconciliation_created": False})
    await db.flush(); await _commit(db)
    return ExitSubmissionResult(order.live_crypto_order_id, order.status, order.provider_order_id, True, True)


async def submit_autonomous_exit_order(
    *, db: AsyncSession, order_id: uuid.UUID, now: datetime | None = None,
    provider_override: Any | None = None, credentials_override: dict[str, str] | None = None,
) -> ExitSubmissionResult:
    if not get_settings().autonomous_position_exit_submission_enabled:
        raise PermissionError("autonomous position exit submission is disabled")
    observed_at = now or datetime.now(timezone.utc)
    order = await db.scalar(select(LiveCryptoOrder).where(
        LiveCryptoOrder.live_crypto_order_id == order_id,
    ).with_for_update().limit(1))
    if order is None or order.execution_claim_id is None:
        _fail("Constructed autonomous SELL order not found")
    claim = await db.scalar(select(AutonomousExecutionClaim).where(
        AutonomousExecutionClaim.claim_id == order.execution_claim_id,
    ).with_for_update().limit(1))
    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == order.custody_id,
    ).with_for_update().limit(1))
    authority = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == order.exit_authority_id,
    ).with_for_update().limit(1))
    if claim is None or custody is None or authority is None:
        _fail("Order claim, custody, or authority is unavailable")

    connection = await _load_exchange_connection(db=db, exchange_connection_id=order.exchange_connection_id)
    pre_submit_quote_balance = next((
        Decimal(str(item.get("available", item.get("balance", "0"))))
        for item in (getattr(connection, "balances", None) or [])
        if str(item.get("currency") or item.get("asset") or "").upper() in {"USD", "ZUSD"}
    ), None)
    if pre_submit_quote_balance is None or pre_submit_quote_balance < 0:
        _fail("Fresh pre-submit USD balance evidence is unavailable")
    credentials = credentials_override if credentials_override is not None else _load_decrypted_credentials(connection)
    provider = provider_override or get_exchange_provider(order.provider, environment=order.environment)

    if order.status == "ACKNOWLEDGED" and order.provider_order_id:
        return ExitSubmissionResult(order_id, order.status, order.provider_order_id, False, False)
    if order.status in {"SUBMISSION_PENDING", "RECONCILIATION_REQUIRED", "UNKNOWN"}:
        return await _recover_only(db=db, order=order, claim=claim, connection=connection,
                                   provider=provider, credentials=credentials, now=observed_at)
    if (order.status != "PENDING_CONFIRMATION" or order.provider_order_id is not None
            or order.submitted_at is not None or order.provider_submission_connected
            or claim.claim_status != "EXECUTION_STARTED" or claim.live_order_id != order_id
            or claim.expires_at is None or observed_at >= claim.expires_at
            or claim.reconciliation_state is not None or claim.provider_submission_connected
            or order.side != "SELL" or order.exposure_effect != "REDUCE_ONLY"
            or Decimal(str(order.capital_deployment_amount or 0)) != 0):
        _fail("Order or claim is not an unexpired disconnected REDUCE_ONLY SELL")
    if (custody.custody_state not in NONTERMINAL_CUSTODY_STATES or custody.terminal_at is not None
            or custody.active_sell_order_id != order_id or custody.active_sell_claim_id != claim.claim_id):
        _fail("Custody is terminal, unsupervised, or not bound to the order")
    if (authority.authority_state != "RESERVED" or authority.revoked_at is not None
            or authority.consumed_at is not None or observed_at >= authority.expires_at
            or authority.reserved_order_id != order_id or authority.reserved_claim_id != claim.claim_id
            or authority.reserved_activation_id != claim.activation_id):
        _fail("Continuing authority is invalid, expired, revoked, consumed, or mismatched")

    package = await db.scalar(select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.package_id == claim.package_id,
    ).with_for_update().limit(1))
    activation = await db.scalar(select(CanonicalProvingActivation).where(
        CanonicalProvingActivation.activation_id == claim.activation_id,
    ).with_for_update().limit(1))
    if (package is None or package.package_state != "ACTIVATED" or package.superseded_at is not None
            or activation is None or activation.activation_state != "ACTIVE"
            or activation.package_id != package.package_id or observed_at >= activation.expires_at):
        _fail("Canonical package or activation is stale, superseded, or mismatched")
    evaluation = _evaluation(custody)
    if (evaluation.get("disposition") != "EXIT_RECOMMENDED" or evaluation.get("price_fresh") is not True
            or _digest(evaluation) != order.evaluation_integrity_hash
            or order.evaluation_integrity_hash != claim.evaluation_integrity_hash):
        _fail("Exit evaluation is no longer fresh and EXIT_RECOMMENDED")

    requested = Decimal(str(order.requested_base_quantity or 0))
    normalized = Decimal(str(order.normalized_base_quantity or 0))
    maximum = Decimal(str(order.maximum_authorized_base_quantity or 0))
    current = await compute_signed_owned_quantity(db=db, live_trading_profile_id=claim.profile_id,
                                                  symbol=claim.product)
    if (requested <= 0 or normalized <= 0 or normalized > requested or requested != current
            or current != Decimal(str(custody.observed_remaining_quantity))
            or requested != Decimal(str(claim.claimed_base_quantity))
            or requested != Decimal(str(package.proposed_base_quantity))
            or normalized > maximum or normalized > Decimal(str(authority.maximum_sell_quantity))):
        _fail("Current ownership or canonical SELL quantity is changed, ambiguous, or excessive")
    scope = (claim.custody_id, claim.account_id, claim.profile_id, claim.connection_id, claim.provider,
             claim.environment, claim.product, claim.originating_buy_claim_id,
             claim.originating_reconciliation_event_id, claim.proof_eligible, claim.disqualification_reason)
    custody_scope = (custody.custody_id, custody.paper_account_id, custody.live_trading_profile_id,
                     custody.exchange_connection_id, custody.provider, custody.environment, custody.product,
                     custody.buy_claim_id, custody.buy_reconciliation_event_id,
                     custody.proof_eligible, custody.disqualification_reason)
    if scope != custody_scope or authority.custody_id != custody.custody_id or authority.proof_eligible != claim.proof_eligible:
        _fail("Order, claim, custody, authority, or proof scope mismatch")
    if (order.exchange_connection_id != claim.connection_id or order.provider != claim.provider
            or order.environment != claim.environment or order.product_id != claim.product
            or order.originating_buy_claim_id != claim.originating_buy_claim_id
            or order.originating_reconciliation_event_id != claim.originating_reconciliation_event_id
            or order.proof_eligible != claim.proof_eligible
            or order.disqualification_reason != claim.disqualification_reason):
        _fail("Canonical order lineage or proof classification mismatch")
    preview = await db.get(CryptoOrderPreview, claim.preview_id)
    if (preview is None or preview.crypto_order_preview_id != package.crypto_order_preview_id
            or preview.side != "SELL" or Decimal(str(preview.estimated_base_size or 0)) != normalized):
        _fail("Preview SELL quantity or identity mismatch")
    asset = await db.scalar(select(Asset).where(
        Asset.symbol == claim.product.split("-")[0], Asset.exchange == claim.provider, Asset.is_active.is_(True),
    ).limit(1))
    if asset is None or asset.min_order_notional is None:
        _fail("Provider product metadata unavailable")
    freshly_normalized = normalize_provider_quantity(
        quantity=requested, step=None if asset.qty_step_size is None else Decimal(str(asset.qty_step_size)),
        supports_fractional=bool(asset.supports_fractional),
    )
    if freshly_normalized != normalized or Decimal(str(order.expected_quote_proceeds)) < Decimal(str(asset.min_order_notional)):
        _fail("Provider precision or minimum-notional evidence changed")
    latest_reconciliations = select(
        LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
        func.max(LiveReconciliationEvent.sequence_number).label("sequence_number"),
    ).where(
        LiveReconciliationEvent.live_trading_profile_id == claim.profile_id,
    ).group_by(LiveReconciliationEvent.live_crypto_order_id).subquery()
    competing_reconciliation = await db.scalar(select(LiveReconciliationEvent.id).join(
        latest_reconciliations,
        (latest_reconciliations.c.order_id == LiveReconciliationEvent.live_crypto_order_id)
        & (latest_reconciliations.c.sequence_number == LiveReconciliationEvent.sequence_number),
    ).where(
        LiveReconciliationEvent.live_trading_profile_id == claim.profile_id,
        LiveReconciliationEvent.id != claim.originating_reconciliation_event_id,
        LiveReconciliationEvent.reconciliation_status.in_(("open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch")),
    ).limit(1))
    if competing_reconciliation is not None:
        _fail("Competing unresolved reconciliation exists")

    require_provider_capabilities(provider=order.provider, operation="submit_live_order",
                                  required=("create_order", "stable_client_order_id"), environment=order.environment)
    before = order.status
    order.status = "SUBMISSION_PENDING"; order.submitted_at = observed_at
    order.provider_submission_connected = True; claim.provider_submission_connected = True
    claim.claim_status = "SUBMISSION_PENDING"; claim.updated_at = observed_at
    order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": False,
                                    "submission_gate": "AUTONOMOUS_POSITION_EXIT_SUBMISSION_ENABLED",
                                    "live_trading_profile_id": str(claim.profile_id),
                                    "usd_available_before_submit": format(pre_submit_quote_balance, "f")}
    _audit(db=db, order_id=order_id, action="AUTONOMOUS_EXIT_SUBMISSION_STARTED", before=before,
           after={"status": order.status, "client_order_id": order.client_order_id,
                  "normalized_base_quantity": format(normalized, "f"), "capital_deployment_amount": "0",
                  "provider_call_made": False})
    await db.flush(); await _commit(db)

    request = ExchangeOrderSubmissionRequest(
        product_id=order.product_id, side="SELL", order_type=order.order_type,
        quote_size=None, base_size=normalized, client_order_id=order.client_order_id,
        idempotency_key=order.client_order_id,
        raw_payload={"client_order_id": order.client_order_id, "product_id": order.product_id,
                     "side": "SELL", "order_configuration": {"market_market_ioc": {
                         "base_size": format(normalized, "f"), "rfq_disabled": True}}},
    )
    try:
        submission = await provider.submit_order(credentials=credentials, environment=order.environment, request=request)
    except Exception as exc:
        order.status = "RECONCILIATION_REQUIRED"; claim.claim_status = "RECOVERY_REQUIRED"
        order.failure_code = "provider_outcome_unknown"; order.failure_reason = exc.__class__.__name__
        order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": True,
                                        "provider_outcome": "ambiguous", "error_class": exc.__class__.__name__}
        _audit(db=db, order_id=order_id, action="AUTONOMOUS_EXIT_PROVIDER_OUTCOME_UNKNOWN",
               before="SUBMISSION_PENDING", after={"status": order.status, "provider_call_made": True})
        await db.flush(); await _commit(db)
        return ExitSubmissionResult(order_id, order.status, None, True, False)

    order.safe_provider_response = {**(order.safe_provider_response or {}), "provider_call_made": True,
                                    "provider_outcome": submission.classification}
    if submission.classification == "success" and submission.order is not None and submission.order.provider_order_id:
        if submission.order.client_order_id != order.client_order_id:
            order.status = "RECONCILIATION_REQUIRED"; claim.claim_status = "RECOVERY_REQUIRED"
            order.failure_code = "provider_identity_mismatch"
        else:
            order.provider_order_id = submission.order.provider_order_id
            order.provider_status = submission.order.status
            order.status = "ACKNOWLEDGED"; order.acknowledged_at = submission.order.acknowledged_at or observed_at
            order.failure_code = None; order.failure_reason = None
        action = "AUTONOMOUS_EXIT_PROVIDER_ACKNOWLEDGED"
    elif submission.classification == "rejected":
        order.status = "REJECTED"; claim.claim_status = "CANCELLED"
        order.failure_code = "provider_rejected" if submission.rejection is None else submission.rejection.code
        order.failure_reason = None if submission.rejection is None else submission.rejection.message
        action = "AUTONOMOUS_EXIT_PROVIDER_REJECTED"
    else:
        order.status = "RECONCILIATION_REQUIRED"; claim.claim_status = "RECOVERY_REQUIRED"
        order.failure_code = "provider_outcome_unknown"
        order.failure_reason = None if submission.ambiguous is None else submission.ambiguous.reason
        action = "AUTONOMOUS_EXIT_PROVIDER_OUTCOME_UNKNOWN"
    order.updated_at = observed_at; claim.updated_at = observed_at
    _audit(db=db, order_id=order_id, action=action, before="SUBMISSION_PENDING",
           after={"status": order.status, "provider_order_id": order.provider_order_id,
                  "provider_call_made": True, "sell_reconciliation_created": False,
                  "custody_closed": False, "autonomous_proof_sell_ready": False})
    await db.flush(); await _commit(db)
    return ExitSubmissionResult(order_id, order.status, order.provider_order_id, True, False)


async def inspect_autonomous_exit_submission(*, db: AsyncSession, order_id: uuid.UUID) -> dict[str, Any]:
    order = await db.get(LiveCryptoOrder, order_id)
    if order is None:
        return {"found": False, "submission_gate_enabled": get_settings().autonomous_position_exit_submission_enabled}
    return {
        "found": True, "order_id": str(order_id), "claim_id": str(order.execution_claim_id),
        "custody_id": str(order.custody_id), "authority_id": str(order.exit_authority_id),
        "activation_id": str(order.activation_id), "preview_id": str(order.crypto_order_preview_id),
        "risk_event_id": None if order.risk_event_id is None else str(order.risk_event_id),
        "client_order_id": order.client_order_id, "provider": order.provider,
        "requested_base_quantity": format(Decimal(order.requested_base_quantity), "f"),
        "normalized_base_quantity": format(Decimal(order.normalized_base_quantity), "f"),
        "capital_deployment_amount": "0", "order_state": order.status,
        "submission_gate_enabled": get_settings().autonomous_position_exit_submission_enabled,
        "provider_call_made": bool((order.safe_provider_response or {}).get("provider_call_made", False)),
        "provider_order_id": order.provider_order_id, "proof_eligible": order.proof_eligible,
        "disqualification_reason": order.disqualification_reason,
        "sell_reconciliation_created": False, "custody_closed": False,
        "autonomous_proof_sell_ready": False,
    }
