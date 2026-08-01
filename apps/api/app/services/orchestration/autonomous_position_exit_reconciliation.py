from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live.position_quantity import compute_signed_owned_quantity

POST_SUBMISSION_STATES = {
    "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED", "UNKNOWN",
    "REJECTED", "CANCELLED", "FILLED",
}


@dataclass(frozen=True, slots=True)
class ExitReconciliationResult:
    order_id: uuid.UUID
    custody_id: uuid.UUID
    status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    gross_proceeds: Decimal
    sell_fees: Decimal
    net_proceeds: Decimal
    realized_net_profit: Decimal | None
    terminal: bool
    proof_sell_verified: bool
    idempotent: bool


def _fail(message: str) -> None:
    raise InvalidRequestError(message=message)


def _sum(rows: list[Any], field: str) -> Decimal:
    return sum((Decimal(str(getattr(row, field))) for row in rows), Decimal("0"))


def _result(order: LiveCryptoOrder, custody: AutonomousPositionCustody, *, idempotent: bool) -> ExitReconciliationResult:
    return ExitReconciliationResult(
        order.live_crypto_order_id, custody.custody_id, order.status,
        Decimal(str(custody.realized_sold_quantity or 0)), Decimal(str(custody.observed_remaining_quantity)),
        Decimal(str(custody.realized_gross_sell_proceeds or 0)), Decimal(str(custody.realized_sell_fees or 0)),
        Decimal(str(custody.realized_net_sell_proceeds or 0)),
        None if custody.realized_net_profit is None else Decimal(str(custody.realized_net_profit)),
        custody.custody_state == "CLOSED", bool(custody.autonomous_proof_sell_verified), idempotent,
    )


async def reconcile_autonomous_exit_order(
    *, db: AsyncSession, order_id: uuid.UUID, now: datetime | None = None,
) -> ExitReconciliationResult:
    observed_at = now or datetime.now(timezone.utc)
    async with db.begin_nested():
        order = await db.scalar(select(LiveCryptoOrder).where(
            LiveCryptoOrder.live_crypto_order_id == order_id,
        ).with_for_update().limit(1))
        if order is None or order.execution_claim_id is None or order.custody_id is None:
            _fail("Autonomous SELL order not found")
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
            _fail("SELL claim, custody, or continuing authority is unavailable")
        if custody.exit_reconciliation_event_id is not None and custody.custody_state == "CLOSED":
            return _result(order, custody, idempotent=True)
        if (order.status not in POST_SUBMISSION_STATES or order.side != "SELL"
                or order.exposure_effect != "REDUCE_ONLY"
                or Decimal(str(order.capital_deployment_amount or 0)) != 0
                or order.provider_submission_connected is not True or order.submitted_at is None
                or claim.live_order_id != order_id or claim.custody_id != custody.custody_id
                or custody.active_sell_order_id != order_id or custody.active_sell_claim_id != claim.claim_id
                or authority.reserved_order_id != order_id or authority.reserved_claim_id != claim.claim_id):
            _fail("Order is not a coherent post-submission autonomous REDUCE_ONLY SELL")
        if (custody.terminal_at is not None or custody.custody_state not in {"EXIT_PENDING", "BLOCKED"}
                or authority.authority_state not in {"RESERVED", "BLOCKED"}
                or authority.consumed_at is not None):
            _fail("Custody or authority is already terminal or inconsistent")
        package = await db.scalar(select(CanonicalPreviewPackage).where(
            CanonicalPreviewPackage.package_id == claim.package_id,
        ).with_for_update().limit(1))
        activation = await db.scalar(select(CanonicalProvingActivation).where(
            CanonicalProvingActivation.activation_id == claim.activation_id,
        ).with_for_update().limit(1))
        if (package is None or activation is None or activation.package_id != package.package_id
                or package.crypto_order_preview_id != order.crypto_order_preview_id
                or package.campaign_id != claim.campaign_id or package.campaign_version != claim.campaign_version
                or package.paper_account_id != claim.account_id or package.live_trading_profile_id != claim.profile_id
                or package.provider != claim.provider or package.environment != claim.environment
                or package.product != claim.product or package.side != "SELL"):
            _fail("Package, activation, campaign, account, profile, or provider scope mismatch")
        scope = (claim.account_id, claim.profile_id, claim.connection_id, claim.provider, claim.environment,
                 claim.product, claim.originating_buy_claim_id, claim.originating_reconciliation_event_id,
                 claim.proof_eligible, claim.disqualification_reason)
        custody_scope = (custody.paper_account_id, custody.live_trading_profile_id,
                         custody.exchange_connection_id, custody.provider, custody.environment, custody.product,
                         custody.buy_claim_id, custody.buy_reconciliation_event_id,
                         custody.proof_eligible, custody.disqualification_reason)
        if scope != custody_scope or order.proof_eligible != custody.proof_eligible or order.disqualification_reason != custody.disqualification_reason:
            _fail("Custody, BUY lineage, or proof classification mismatch")
        normalized = Decimal(str(order.normalized_base_quantity or 0))
        requested = Decimal(str(order.requested_base_quantity or 0))
        maximum = Decimal(str(order.maximum_authorized_base_quantity or 0))
        if normalized <= 0 or normalized > requested or normalized > maximum or normalized > Decimal(str(authority.maximum_sell_quantity)):
            _fail("Submitted SELL quantity is invalid or excessive")
        pre_owned = await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=claim.profile_id, symbol=claim.product,
        )
        if pre_owned < 0 or pre_owned != Decimal(str(custody.observed_remaining_quantity)):
            _fail("Fresh accounting ownership does not match supervised custody")
        evidence = order.safe_provider_response if isinstance(order.safe_provider_response, dict) else {}
        if not evidence.get("live_trading_profile_id") or evidence.get("usd_available_before_submit") is None:
            _fail("Canonical reconciliation profile or pre-submit balance evidence is missing")

        if order.status == "REJECTED" and order.provider_order_id is None:
            claim.claim_status = "CANCELLED"; claim.reconciliation_state = "SELL_NOT_FILLED"
            authority.authority_state = "BLOCKED"; authority.updated_at = observed_at
            custody.custody_state = "BLOCKED"; custody.continuing_exit_authority_state = "BLOCKED"
            custody.active_sell_order_id = None; custody.active_sell_claim_id = None
            custody.autonomous_proof_sell_verified = False; custody.updated_at = observed_at
            db.add(AuditLog(
                actor="system:autonomous_position_exit_reconciliation",
                action="autonomous_position_exit.provider_rejection_governed",
                entity_type="autonomous_position_custody", entity_id=custody.custody_id,
                before_state={"custody_state": "EXIT_PENDING", "remaining_quantity": format(pre_owned, "f")},
                after_state={"custody_state": "BLOCKED", "remaining_quantity": format(pre_owned, "f"),
                             "authority_state": "BLOCKED", "provider_fill_observed": False,
                             "blind_resubmission_allowed": False, "automatic_worker_connected": False},
            ))
            await db.flush()
            return _result(order, custody, idempotent=False)

        canonical = await reconcile_live_order_and_fills(
            db=db, live_crypto_order_id=order_id,
            operator_identity="system:autonomous_position_exit_reconciliation",
        )
        sell_rows = list((await db.scalars(select(LiveAccountingRecord).where(
            LiveAccountingRecord.live_crypto_order_id == order_id,
        ).order_by(LiveAccountingRecord.recorded_at.asc()))).all())
        buy_rows = list((await db.scalars(select(LiveAccountingRecord).where(
            LiveAccountingRecord.live_crypto_order_id == custody.buy_live_order_id,
        ).order_by(LiveAccountingRecord.recorded_at.asc()))).all())
        sell_fills = [row for row in sell_rows if row.record_type in {"fill_accounting", "partial_fill_accounting"}]
        sell_fee_rows = [row for row in sell_rows if row.record_type == "fee_attribution"]
        buy_fills = [row for row in buy_rows if row.record_type in {"fill_accounting", "partial_fill_accounting"}]
        buy_fee_rows = [row for row in buy_rows if row.record_type == "fee_attribution"]
        fill_ids = [row.provider_fill_id for row in sell_fills]
        if len(fill_ids) != len(set(fill_ids)) or any(not value for value in fill_ids):
            _fail("SELL fill identity is missing or duplicated")
        for row in sell_fills:
            if (row.live_trading_profile_id != claim.profile_id or row.provider_order_id != order.provider_order_id
                    or row.side != "sell" or row.symbol != claim.product
                    or Decimal(str(row.filled_quantity)) <= 0 or Decimal(str(row.fill_price)) <= 0
                    or row.provider_fill_timestamp is None):
                _fail("SELL fill identity, side, product, quantity, price, or timestamp mismatch")
        if any(str(row.fee_currency).upper() not in {"USD", "ZUSD"} for row in sell_fee_rows + buy_fee_rows):
            _fail("Non-USD fee requires canonical conversion evidence")
        sold = _sum(sell_fills, "filled_quantity")
        if sold > normalized or sold > requested or sold > Decimal(str(authority.maximum_sell_quantity)):
            _fail("Cumulative SELL fills exceed submitted or authorized quantity")
        previously_accounted_sold = Decimal(str(custody.realized_sold_quantity or 0))
        if sold < previously_accounted_sold:
            _fail("Cumulative provider fill evidence regressed below prior custody accounting")
        newly_accounted_sold = sold - previously_accounted_sold
        gross = _sum(sell_fills, "gross_notional")
        sell_fees = _sum(sell_fee_rows, "fee_amount")
        net = gross - sell_fees
        acquired = _sum(buy_fills, "filled_quantity")
        buy_gross = _sum(buy_fills, "gross_notional")
        buy_fees = _sum(buy_fee_rows, "fee_amount")
        if sold > 0 and (acquired <= 0 or sold > acquired):
            _fail("Originating BUY cost-basis evidence is incomplete or insufficient")
        allocation_ratio = Decimal("0") if acquired == 0 else sold / acquired
        allocated_cost = buy_gross * allocation_ratio
        allocated_buy_fees = buy_fees * allocation_ratio
        realized_profit = None if sold == 0 else net - allocated_cost - allocated_buy_fees
        invested = allocated_cost + allocated_buy_fees
        realized_return = None if realized_profit is None or invested <= 0 else realized_profit / invested
        after_owned = await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=claim.profile_id, symbol=claim.product,
        )
        expected_remaining = pre_owned - newly_accounted_sold
        if after_owned < 0 or after_owned != expected_remaining:
            _fail("Post-reconciliation ownership conflicts with newly accounted SELL fills")
        custody.observed_remaining_quantity = after_owned
        custody.realized_sold_quantity = sold
        custody.realized_gross_sell_proceeds = gross
        custody.realized_sell_fees = sell_fees
        custody.realized_net_sell_proceeds = net
        custody.allocated_buy_cost_basis = allocated_cost
        custody.allocated_buy_fees = allocated_buy_fees
        custody.realized_net_profit = realized_profit
        custody.realized_return = realized_return
        custody.residual_dust_quantity = after_owned
        custody.updated_at = observed_at

        canonical_status = str(canonical.get("reconciliation_status") or order.status)
        accounting_complete = canonical.get("accounting_completion_status") == "complete"
        balance_clear = canonical.get("balance_mismatch_state") in {"ok", "tolerated", "not_required"}
        terminal_full = (
            canonical_status == "FILLED" and accounting_complete and balance_clear
            and sold == normalized and after_owned == 0
        )
        latest_reconciliation = await db.scalar(select(LiveReconciliationEvent).where(
            LiveReconciliationEvent.live_crypto_order_id == order_id,
        ).order_by(LiveReconciliationEvent.sequence_number.desc()).limit(1))
        if terminal_full:
            if latest_reconciliation is None or latest_reconciliation.reconciliation_status != "filled":
                _fail("Terminal provider fill lacks terminal reconciliation evidence")
            order.status = "FILLED"
            claim.claim_status = "COMPLETED"; claim.reconciliation_state = "SELL_RECONCILED"
            claim.completed_at = observed_at; claim.updated_at = observed_at
            authority.authority_state = "CONSUMED"; authority.consumed_at = observed_at; authority.updated_at = observed_at
            custody.custody_state = "CLOSED"; custody.terminal_at = observed_at
            custody.exit_reconciliation_event_id = latest_reconciliation.id
            custody.exit_reconciled_at = observed_at
            custody.active_sell_order_id = None; custody.active_sell_claim_id = None
            custody.continuing_exit_authority_state = "CONSUMED"
            proof_verified = bool(
                custody.proof_eligible and claim.proof_eligible and order.proof_eligible
                and custody.disqualification_reason is None and realized_profit is not None and realized_profit > 0
            )
            custody.autonomous_proof_sell_verified = proof_verified
        elif canonical_status in {"CANCELLED", "REJECTED"}:
            claim.claim_status = "CANCELLED"; claim.reconciliation_state = "SELL_NOT_FILLED"
            authority.authority_state = "BLOCKED"; authority.updated_at = observed_at
            custody.custody_state = "BLOCKED"; custody.continuing_exit_authority_state = "BLOCKED"
            custody.active_sell_order_id = None; custody.active_sell_claim_id = None
            custody.autonomous_proof_sell_verified = False
        else:
            claim.claim_status = "RECONCILIATION_REQUIRED"; claim.reconciliation_state = "SELL_RECONCILIATION_REQUIRED"
            authority.authority_state = "RESERVED"
            custody.custody_state = "EXIT_PENDING"; custody.continuing_exit_authority_state = "RESERVED"
            custody.autonomous_proof_sell_verified = False
        db.add(AuditLog(
            actor="system:autonomous_position_exit_reconciliation",
            action="autonomous_position_exit.reconciled",
            entity_type="autonomous_position_custody", entity_id=custody.custody_id,
            before_state={"custody_state": "EXIT_PENDING", "remaining_quantity": format(pre_owned, "f")},
            after_state={
                "custody_state": custody.custody_state, "order_status": order.status,
                "filled_quantity": format(sold, "f"), "remaining_quantity": format(after_owned, "f"),
                "gross_sell_proceeds": format(gross, "f"), "sell_fees": format(sell_fees, "f"),
                "net_sell_proceeds": format(net, "f"), "allocated_buy_cost_basis": format(allocated_cost, "f"),
                "allocated_buy_fees": format(allocated_buy_fees, "f"),
                "realized_net_profit": None if realized_profit is None else format(realized_profit, "f"),
                "autonomous_proof_sell_verified": custody.autonomous_proof_sell_verified,
                "first_autonomous_profit_complete": False, "automatic_worker_connected": False,
            },
        ))
        await db.flush()
        return _result(order, custody, idempotent=False)


async def inspect_autonomous_exit_reconciliation(*, db: AsyncSession, custody_id: uuid.UUID) -> dict[str, Any]:
    custody = await db.get(AutonomousPositionCustody, custody_id)
    if custody is None:
        return {"found": False, "automatic_worker_connected": False, "first_autonomous_profit_complete": False}
    reconciliation = None if custody.exit_reconciliation_event_id is None else await db.get(
        LiveReconciliationEvent, custody.exit_reconciliation_event_id,
    )
    order_id = custody.active_sell_order_id or (None if reconciliation is None else reconciliation.live_crypto_order_id)
    order = None if order_id is None else await db.get(LiveCryptoOrder, order_id)
    claim_id = custody.active_sell_claim_id or (None if order is None else order.execution_claim_id)
    claim = None if claim_id is None else await db.get(AutonomousExecutionClaim, claim_id)
    authority = None if order is None or order.exit_authority_id is None else await db.get(
        AutonomousPositionExitAuthority, order.exit_authority_id,
    )
    return {
        "found": True, "custody_id": str(custody.custody_id),
        "buy_claim_id": str(custody.buy_claim_id), "buy_reconciliation_id": str(custody.buy_reconciliation_event_id),
        "authority_id": None if authority is None else str(authority.authority_id),
        "evaluation_integrity_hash": None if order is None else order.evaluation_integrity_hash,
        "package_id": None if claim is None else str(claim.package_id),
        "activation_id": None if claim is None else str(claim.activation_id),
        "sell_claim_id": None if claim_id is None else str(claim_id),
        "sell_order_id": None if order_id is None else str(order_id),
        "sell_reconciliation_id": None if custody.exit_reconciliation_event_id is None else str(custody.exit_reconciliation_event_id),
        "provider_order_id": None if order is None else order.provider_order_id,
        "requested_base_quantity": None if order is None else format(Decimal(order.requested_base_quantity), "f"),
        "normalized_base_quantity": None if order is None else format(Decimal(order.normalized_base_quantity), "f"),
        "authorized_base_quantity": None if order is None else format(Decimal(order.maximum_authorized_base_quantity), "f"),
        "filled_quantity": format(Decimal(str(custody.realized_sold_quantity or 0)), "f"),
        "remaining_quantity": format(Decimal(str(custody.observed_remaining_quantity)), "f"),
        "gross_proceeds": format(Decimal(str(custody.realized_gross_sell_proceeds or 0)), "f"),
        "sell_fees": format(Decimal(str(custody.realized_sell_fees or 0)), "f"),
        "net_proceeds": format(Decimal(str(custody.realized_net_sell_proceeds or 0)), "f"),
        "cost_basis": format(Decimal(str(custody.allocated_buy_cost_basis or 0)), "f"),
        "buy_fees": format(Decimal(str(custody.allocated_buy_fees or 0)), "f"),
        "realized_net_profit": None if custody.realized_net_profit is None else format(Decimal(str(custody.realized_net_profit)), "f"),
        "order_state": None if order is None else order.status,
        "claim_state": None if claim is None else claim.claim_status,
        "authority_state": None if authority is None else authority.authority_state,
        "reconciliation_state": None if reconciliation is None else reconciliation.reconciliation_status,
        "custody_state": custody.custody_state, "proof_eligible": custody.proof_eligible,
        "disqualification_reason": custody.disqualification_reason,
        "custody_closed": custody.custody_state == "CLOSED",
        "sell_reconciliation_complete": custody.exit_reconciliation_event_id is not None,
        "autonomous_proof_sell_verified": custody.autonomous_proof_sell_verified,
        "automatic_worker_connected": False, "first_autonomous_profit_complete": False,
    }
