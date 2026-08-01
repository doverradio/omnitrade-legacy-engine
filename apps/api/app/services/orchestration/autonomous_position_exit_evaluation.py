from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.position_lifecycle.evaluator import evaluate_position_lifecycle
from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy
from app.services.position_lifecycle.source_adapter import load_position_snapshots

EVALUATION_CADENCE = timedelta(minutes=15)
EVALUABLE_STATES = ("HANDOFF_PENDING", "ACTIVE", "EXIT_PENDING", "BLOCKED")


@dataclass(frozen=True, slots=True)
class CustodyEvaluationPollOutcome:
    discovered: int
    evaluated: int
    blocked: int
    exit_recommended: int
    closed_candidate: int


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def persisted_exit_evaluation(row: AutonomousPositionCustody) -> dict[str, Any]:
    """Return the evaluator's authoritative persisted evidence without recomputing it."""
    metadata = row.audit_metadata if isinstance(row.audit_metadata, dict) else {}
    value = metadata.get("latest_exit_evaluation")
    return value if isinstance(value, dict) else {}


async def discover_due_custodies(
    *, db: AsyncSession, now: datetime, limit: int,
) -> list[AutonomousPositionCustody]:
    """Claim a deterministic bounded batch for this transaction.

    PostgreSQL SKIP LOCKED prevents overlapping workers from evaluating the
    same custody. A crash rolls back the row update and releases the lock, so
    the same row is automatically eligible on the next scheduler cycle.
    """
    return list((await db.scalars(
        select(AutonomousPositionCustody).where(
            AutonomousPositionCustody.custody_state.in_(EVALUABLE_STATES),
            (
                AutonomousPositionCustody.next_exit_evaluation_at.is_(None)
                | (AutonomousPositionCustody.next_exit_evaluation_at <= now)
            ),
        ).order_by(
            AutonomousPositionCustody.next_exit_evaluation_at.asc().nullsfirst(),
            AutonomousPositionCustody.created_at.asc(),
            AutonomousPositionCustody.custody_id.asc(),
        ).limit(limit).with_for_update(skip_locked=True)
    )).all())


async def _evaluate_one(
    *, db: AsyncSession, row: AutonomousPositionCustody, now: datetime,
) -> dict[str, Any]:
    blockers: list[str] = []
    quantity: Decimal | None = None
    try:
        quantity = await compute_signed_owned_quantity(
            db=db, live_trading_profile_id=row.live_trading_profile_id, symbol=row.product,
        )
        if quantity < 0:
            blockers.append("authoritative_quantity_negative")
    except Exception:
        blockers.append("authoritative_quantity_unavailable")

    profile = await db.get(LiveTradingProfile, row.live_trading_profile_id)
    account = await db.get(PaperAccount, row.paper_account_id)
    connection = await db.get(ExchangeConnection, row.exchange_connection_id)
    claim = await db.get(AutonomousExecutionClaim, row.buy_claim_id)
    order = await db.get(LiveCryptoOrder, row.buy_live_order_id)
    reconciliation = await db.get(LiveReconciliationEvent, row.buy_reconciliation_event_id)
    campaign = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == row.runtime_campaign_id).limit(1))
    mandate = await db.get(AutonomousCapitalMandate, row.mandate_id)

    if profile is None or profile.paper_account_id != row.paper_account_id:
        blockers.append("profile_account_lineage_ambiguous")
    if account is None:
        blockers.append("account_unavailable")
    if connection is None or connection.provider != row.provider or connection.environment != row.environment:
        blockers.append("connection_scope_ambiguous")
    if claim is None or claim.live_order_id != row.buy_live_order_id:
        blockers.append("buy_claim_lineage_ambiguous")
    elif (
        claim.profile_id != row.live_trading_profile_id
        or claim.account_id != row.paper_account_id
        or claim.connection_id != row.exchange_connection_id
        or claim.provider != row.provider
        or claim.environment != row.environment
        or claim.product != row.product
    ):
        blockers.append("buy_claim_scope_ambiguous")
    if order is None:
        blockers.append("buy_order_unavailable")
    elif (
        order.exchange_connection_id != row.exchange_connection_id
        or order.provider != row.provider
        or order.environment != row.environment
        or order.product_id != row.product
    ):
        blockers.append("buy_order_scope_ambiguous")
    if (
        reconciliation is None
        or reconciliation.live_crypto_order_id != row.buy_live_order_id
        or reconciliation.reconciliation_status != "filled"
    ):
        blockers.append("authoritative_buy_reconciliation_unavailable")
    if campaign is None:
        blockers.append("entry_campaign_lineage_unavailable")
    if mandate is None:
        blockers.append("entry_mandate_lineage_unavailable")
    if row.active_sell_claim_id is not None or row.active_sell_order_id is not None:
        blockers.append("unresolved_sell_execution_reference")

    evaluation: dict[str, Any] = {
        "custody_id": str(row.custody_id), "custody_state": row.custody_state,
        "evaluated_at": now.isoformat(), "next_evaluation_at": (now + EVALUATION_CADENCE).isoformat(),
        "authoritative_remaining_quantity": _decimal(quantity),
        "campaign_status": None if campaign is None else campaign.status,
        "mandate_status": None if mandate is None else mandate.status,
        "entry_authority_expired": bool(
            campaign is not None and campaign.status not in {"ACTIVE", "READY"}
            or mandate is not None and mandate.status != "ACTIVE"
        ),
        "proof_eligible": row.proof_eligible,
        "disqualification_reason": row.disqualification_reason,
        "continuing_sell_authority": row.continuing_exit_authority_state,
        "automatic_sell_execution": False,
        "price": None, "price_observed_at": None, "price_fresh": False,
        "estimated_current_proceeds": None, "estimated_exit_fee": None,
        "estimated_slippage": None, "cost_basis": None, "paid_costs": None,
        "estimated_net_exit_result": None, "profitable_exit": False,
        "stop_loss_triggered": False, "maximum_hold_exceeded": False,
        "mandatory_safety_exit": False, "dust": False,
        "active_sell_claim_id": None if row.active_sell_claim_id is None else str(row.active_sell_claim_id),
        "active_sell_order_id": None if row.active_sell_order_id is None else str(row.active_sell_order_id),
    }

    if quantity == 0 and not blockers:
        evaluation.update(disposition="CLOSED_CANDIDATE", reason_codes=["authoritative_quantity_zero"])
        return evaluation

    try:
        snapshots = await load_position_snapshots(db=db, account_id=row.paper_account_id, campaign_id=None)
    except Exception:
        snapshots = []
        blockers.append("position_snapshot_unavailable")
    matches = [
        item for item in snapshots
        if item.live_trading_profile_id == row.live_trading_profile_id
        and item.symbol.upper() == row.product.upper()
    ]
    if len(matches) != 1:
        blockers.append("position_snapshot_ambiguous")
    elif matches[0].position_size != quantity:
        blockers.append("position_quantity_ambiguous")
    else:
        snapshot = matches[0]
        policy = resolve_lifecycle_policy(
            asset_class=snapshot.asset_class, symbol=snapshot.symbol,
            venue="venue-neutral", now=now,
        )
        if policy is None:
            blockers.append("exit_policy_unavailable")
        else:
            lifecycle = evaluate_position_lifecycle(snapshot=snapshot, policy=policy, now=now)
            stop_price = policy.stop_loss_price
            if stop_price is None and policy.stop_loss_percent is not None:
                stop_price = snapshot.entry_price * (Decimal("1") - policy.stop_loss_percent)
            stop_triggered = bool(snapshot.current_price is not None and stop_price is not None and snapshot.current_price <= stop_price)
            max_hold = bool(
                policy.max_hold_minutes is not None and snapshot.opened_at is not None
                and now >= snapshot.opened_at + timedelta(minutes=policy.max_hold_minutes)
            )
            price_fresh = bool(snapshot.current_price is not None and not lifecycle.market_data_stale)
            if not price_fresh:
                blockers.append("market_evidence_stale_or_missing")
            proceeds = lifecycle.current_market_value
            fee = None if proceeds is None else proceeds * policy.estimated_exit_fee_rate
            slippage = None if proceeds is None else proceeds * policy.estimated_slippage_rate
            profitable = bool(
                lifecycle.expected_net_realized_pnl_if_sold_now is not None
                and lifecycle.expected_net_realized_pnl_if_sold_now >= policy.minimum_net_profit_to_exit
            )
            evaluation.update(
                price=_decimal(snapshot.current_price),
                price_observed_at=None if snapshot.market_data_timestamp is None else snapshot.market_data_timestamp.isoformat(),
                price_fresh=price_fresh,
                estimated_current_proceeds=_decimal(proceeds), estimated_exit_fee=_decimal(fee),
                estimated_slippage=_decimal(slippage),
                cost_basis=_decimal(quantity * snapshot.entry_price),
                paid_costs=_decimal(snapshot.accumulated_entry_and_carry_costs),
                estimated_net_exit_result=_decimal(lifecycle.expected_net_realized_pnl_if_sold_now),
                profitable_exit=profitable, stop_loss_triggered=stop_triggered,
                maximum_hold_exceeded=max_hold,
                mandatory_safety_exit=stop_triggered or max_hold,
                dust=lifecycle.dust_indicator,
                policy_id=policy.policy_id, policy_version=policy.policy_version,
                minimum_net_profit_to_exit=_decimal(policy.minimum_net_profit_to_exit),
                dust_threshold=_decimal(policy.dust_threshold),
                policy_conflicts=[
                    "small_position_minimum_net_profit_may_require_implausible_gain"
                ] if proceeds is not None and proceeds <= policy.dust_threshold else [],
            )

    reason_codes = sorted(set(blockers))
    if blockers:
        disposition = "BLOCKED"
    elif evaluation["mandatory_safety_exit"] or evaluation["profitable_exit"]:
        disposition = "EXIT_RECOMMENDED"
        if evaluation["stop_loss_triggered"]:
            reason_codes.append("stop_loss_triggered")
        if evaluation["maximum_hold_exceeded"]:
            reason_codes.append("maximum_hold_exceeded")
        if evaluation["profitable_exit"]:
            reason_codes.append("minimum_net_profit_satisfied")
    else:
        disposition = "HOLD"
        reason_codes.append("exit_conditions_not_satisfied")
    evaluation.update(disposition=disposition, reason_codes=sorted(set(reason_codes)))
    return evaluation


async def evaluate_due_custodies(
    *, db: AsyncSession, now: datetime | None = None, limit: int = 25,
) -> CustodyEvaluationPollOutcome:
    observed_at = now or datetime.now(timezone.utc)
    rows = await discover_due_custodies(db=db, now=observed_at, limit=limit)
    counts = {"BLOCKED": 0, "EXIT_RECOMMENDED": 0, "CLOSED_CANDIDATE": 0}
    for row in rows:
        evidence = await _evaluate_one(db=db, row=row, now=observed_at)
        row.latest_exit_evaluation_at = observed_at
        row.next_exit_evaluation_at = observed_at + EVALUATION_CADENCE
        if evidence["authoritative_remaining_quantity"] is not None:
            row.observed_remaining_quantity = Decimal(evidence["authoritative_remaining_quantity"])
        row.audit_metadata = {**(row.audit_metadata or {}), "latest_exit_evaluation": evidence}
        db.add(AuditLog(
            actor="system:autonomous_custody_evaluator",
            action="autonomous_position_custody.exit_evaluated",
            entity_type="autonomous_position_custody", entity_id=row.custody_id,
            before_state={"custody_state": row.custody_state},
            after_state={
                "disposition": evidence["disposition"],
                "reason_codes": evidence["reason_codes"],
                "automatic_sell_execution": False,
            },
        ))
        counts[evidence["disposition"]] = counts.get(evidence["disposition"], 0) + 1
    await db.flush()
    return CustodyEvaluationPollOutcome(
        discovered=len(rows), evaluated=len(rows), blocked=counts["BLOCKED"],
        exit_recommended=counts["EXIT_RECOMMENDED"], closed_candidate=counts["CLOSED_CANDIDATE"],
    )
