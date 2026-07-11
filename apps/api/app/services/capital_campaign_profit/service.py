from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_profit_cycle import CapitalCampaignProfitCycle
from app.models.capital_campaign_profit_policy import CapitalCampaignProfitPolicy
from app.schemas.capital_campaign_profit import (
    CapitalCampaignProfitCycleResponse,
    CapitalCampaignProfitPolicyResponse,
    CapitalCampaignProfitPolicyUpsertRequest,
)


def _d(value: Decimal | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _sanitize_nonnegative(name: str, value: Decimal) -> Decimal:
    if value < 0:
        raise InvalidRequestError(message=f"{name} cannot be negative", details={name: format(value, "f")})
    return value


def _to_policy_response(policy: CapitalCampaignProfitPolicy) -> CapitalCampaignProfitPolicyResponse:
    return CapitalCampaignProfitPolicyResponse(
        policy_id=policy.policy_id,
        policy_uuid=policy.policy_uuid,
        capital_campaign_id=policy.capital_campaign_id,
        policy_type=policy.policy_type,
        profit_target_amount=policy.profit_target_amount,
        profit_target_percent=policy.profit_target_percent,
        compound_percent=policy.compound_percent,
        withdraw_percent=policy.withdraw_percent,
        protected_principal_amount=policy.protected_principal_amount,
        minimum_realized_profit=policy.minimum_realized_profit,
        maximum_campaign_capital=policy.maximum_campaign_capital,
        minimum_cash_reserve=policy.minimum_cash_reserve,
        fee_reserve_percent=policy.fee_reserve_percent,
        tax_reserve_percent=policy.tax_reserve_percent,
        cooldown_hours=policy.cooldown_hours,
        require_operator_approval=policy.require_operator_approval,
        is_active=policy.is_active,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _to_cycle_response(cycle: CapitalCampaignProfitCycle) -> CapitalCampaignProfitCycleResponse:
    return CapitalCampaignProfitCycleResponse(
        cycle_id=cycle.cycle_id,
        cycle_uuid=cycle.cycle_uuid,
        capital_campaign_id=cycle.capital_campaign_id,
        profit_policy_id=cycle.profit_policy_id,
        cycle_number=cycle.cycle_number,
        opening_capital=cycle.opening_capital,
        opening_equity=cycle.opening_equity,
        realized_profit=cycle.realized_profit,
        unrealized_profit=cycle.unrealized_profit,
        fees=cycle.fees,
        eligible_profit=cycle.eligible_profit,
        compound_amount=cycle.compound_amount,
        withdrawal_amount=cycle.withdrawal_amount,
        reserve_amount=cycle.reserve_amount,
        closing_campaign_capital=cycle.closing_campaign_capital,
        target_reached=cycle.target_reached,
        status=cycle.status,
        settlement_state=cycle.settlement_state,
        calculation_snapshot=cycle.calculation_snapshot,
        calculated_at=cycle.calculated_at,
        approved_at=cycle.approved_at,
        completed_at=cycle.completed_at,
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
    )


async def _record_event(
    *,
    db: AsyncSession,
    action: str,
    campaign_uuid: uuid.UUID,
    actor: str,
    before_state: dict | None,
    after_state: dict | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="capital_campaign",
            entity_id=campaign_uuid,
            before_state=before_state,
            after_state=after_state,
        )
    )


async def _get_campaign_by_uuid(db: AsyncSession, campaign_uuid: uuid.UUID) -> CapitalCampaign:
    campaign = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_uuid).limit(1))
    if campaign is None:
        raise NotFoundError(message="Capital campaign not found", details={"campaign_uuid": str(campaign_uuid)})
    return campaign


async def get_active_profit_policy(*, db: AsyncSession, campaign_uuid: uuid.UUID) -> CapitalCampaignProfitPolicyResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    policy = await db.scalar(
        select(CapitalCampaignProfitPolicy)
        .where(CapitalCampaignProfitPolicy.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitPolicy.is_active.is_(True))
        .order_by(CapitalCampaignProfitPolicy.updated_at.desc(), CapitalCampaignProfitPolicy.policy_id.desc())
        .limit(1)
    )
    if policy is None:
        raise NotFoundError(message="Active profit policy not found", details={"campaign_uuid": str(campaign_uuid)})
    return _to_policy_response(policy)


async def upsert_profit_policy(
    *,
    db: AsyncSession,
    campaign_uuid: uuid.UUID,
    request: CapitalCampaignProfitPolicyUpsertRequest,
    actor: str = "operator",
) -> CapitalCampaignProfitPolicyResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)

    compound_percent = _sanitize_nonnegative("compound_percent", _d(request.compound_percent))
    withdraw_percent = _sanitize_nonnegative("withdraw_percent", _d(request.withdraw_percent))
    if compound_percent > Decimal("100") or withdraw_percent > Decimal("100"):
        raise InvalidRequestError(message="percent fields must be <= 100", details={})
    if compound_percent + withdraw_percent > Decimal("100"):
        raise InvalidRequestError(message="compound_percent + withdraw_percent must be <= 100", details={})

    minimum_realized_profit = _sanitize_nonnegative("minimum_realized_profit", _d(request.minimum_realized_profit))
    minimum_cash_reserve = _sanitize_nonnegative("minimum_cash_reserve", _d(request.minimum_cash_reserve))
    fee_reserve_percent = _sanitize_nonnegative("fee_reserve_percent", _d(request.fee_reserve_percent))
    tax_reserve_percent = _sanitize_nonnegative("tax_reserve_percent", _d(request.tax_reserve_percent))

    if request.profit_target_amount is not None and request.profit_target_amount <= 0:
        raise InvalidRequestError(message="profit_target_amount must be > 0", details={})
    if request.profit_target_percent is not None and request.profit_target_percent <= 0:
        raise InvalidRequestError(message="profit_target_percent must be > 0", details={})
    if request.maximum_campaign_capital is not None and request.protected_principal_amount is not None:
        if request.maximum_campaign_capital <= request.protected_principal_amount:
            raise InvalidRequestError(
                message="maximum_campaign_capital must exceed protected_principal_amount",
                details={},
            )

    existing_active = await db.scalar(
        select(CapitalCampaignProfitPolicy)
        .where(CapitalCampaignProfitPolicy.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitPolicy.is_active.is_(True))
        .limit(1)
    )

    if existing_active is None:
        policy = CapitalCampaignProfitPolicy(
            capital_campaign_id=campaign.id,
            policy_type=request.policy_type,
            profit_target_amount=request.profit_target_amount,
            profit_target_percent=request.profit_target_percent,
            compound_percent=compound_percent,
            withdraw_percent=withdraw_percent,
            protected_principal_amount=request.protected_principal_amount,
            minimum_realized_profit=minimum_realized_profit,
            maximum_campaign_capital=request.maximum_campaign_capital,
            minimum_cash_reserve=minimum_cash_reserve,
            fee_reserve_percent=fee_reserve_percent,
            tax_reserve_percent=tax_reserve_percent,
            cooldown_hours=request.cooldown_hours,
            require_operator_approval=request.require_operator_approval,
            is_active=True,
        )
        db.add(policy)
        action = "PROFIT_POLICY_CREATED"
        before_state = None
    else:
        before_state = {
            "policy_uuid": str(existing_active.policy_uuid),
            "policy_type": existing_active.policy_type,
            "is_active": existing_active.is_active,
        }
        existing_active.policy_type = request.policy_type
        existing_active.profit_target_amount = request.profit_target_amount
        existing_active.profit_target_percent = request.profit_target_percent
        existing_active.compound_percent = compound_percent
        existing_active.withdraw_percent = withdraw_percent
        existing_active.protected_principal_amount = request.protected_principal_amount
        existing_active.minimum_realized_profit = minimum_realized_profit
        existing_active.maximum_campaign_capital = request.maximum_campaign_capital
        existing_active.minimum_cash_reserve = minimum_cash_reserve
        existing_active.fee_reserve_percent = fee_reserve_percent
        existing_active.tax_reserve_percent = tax_reserve_percent
        existing_active.cooldown_hours = request.cooldown_hours
        existing_active.require_operator_approval = request.require_operator_approval
        existing_active.is_active = request.is_active
        existing_active.updated_at = datetime.now(timezone.utc)
        policy = existing_active
        action = "PROFIT_POLICY_UPDATED"

    await db.flush()
    await db.refresh(policy)

    await _record_event(
        db=db,
        action=action,
        campaign_uuid=campaign.uuid,
        actor=actor,
        before_state=before_state,
        after_state={"policy_uuid": str(policy.policy_uuid), "policy_type": policy.policy_type, "is_active": policy.is_active},
    )

    await db.commit()
    return _to_policy_response(policy)


def _calculate_target_state(policy: CapitalCampaignProfitPolicy, campaign: CapitalCampaign) -> tuple[bool, Decimal | None]:
    target_amount_reached = False
    target_percent_reached = False
    progress_candidates: list[Decimal] = []

    realized_profit = _d(campaign.realized_profit)
    if policy.profit_target_amount is not None:
        target_amount_reached = realized_profit >= _d(policy.profit_target_amount)
        progress_candidates.append((realized_profit / _d(policy.profit_target_amount)) * Decimal("100"))

    if policy.profit_target_percent is not None and _d(campaign.starting_capital) > 0:
        realized_return_percent = (realized_profit / _d(campaign.starting_capital)) * Decimal("100")
        target_percent_reached = realized_return_percent >= _d(policy.profit_target_percent)
        progress_candidates.append((realized_return_percent / _d(policy.profit_target_percent)) * Decimal("100"))

    if policy.profit_target_amount is None and policy.profit_target_percent is None:
        return True, None

    return target_amount_reached or target_percent_reached, (max(progress_candidates) if progress_candidates else Decimal("0"))


async def _allocated_profit_to_date(db: AsyncSession, campaign_id: int) -> Decimal:
    allocated = await db.scalar(
        select(func.coalesce(func.sum(CapitalCampaignProfitCycle.compound_amount + CapitalCampaignProfitCycle.withdrawal_amount + CapitalCampaignProfitCycle.reserve_amount), Decimal("0")))
        .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign_id)
        .where(CapitalCampaignProfitCycle.status.in_(["APPROVED", "COMPLETED"]))
    )
    return _d(allocated)


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _recommendation_status(policy: CapitalCampaignProfitPolicy, target_reached: bool, settlement_state: str) -> str:
    if not target_reached:
        return "BELOW_TARGET"
    if settlement_state == "SETTLEMENT_UNKNOWN":
        return "REVIEW_REQUIRED"
    if policy.require_operator_approval:
        return "REVIEW_REQUIRED"
    if policy.policy_type in {"FULL_COMPOUND", "PARTIAL_COMPOUND", "PROTECTED_PRINCIPAL"}:
        return "COMPOUNDING_RECOMMENDED"
    if policy.policy_type in {"WITHDRAW_PROFIT", "WITHDRAW_AND_COMPOUND"}:
        return "WITHDRAWAL_RECOMMENDED"
    if policy.policy_type == "HOLD_PROFIT":
        return "TARGET_REACHED"
    return "REVIEW_REQUIRED"


async def evaluate_profit_cycle(
    *,
    db: AsyncSession,
    campaign_uuid: uuid.UUID,
    actor: str = "system",
    force_new_cycle: bool = False,
) -> CapitalCampaignProfitCycleResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    policy = await db.scalar(
        select(CapitalCampaignProfitPolicy)
        .where(CapitalCampaignProfitPolicy.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitPolicy.is_active.is_(True))
        .limit(1)
    )
    if policy is None:
        raise NotFoundError(message="Active profit policy not found", details={"campaign_uuid": str(campaign_uuid)})

    now = datetime.now(timezone.utc)

    if policy.cooldown_hours > 0:
        latest_cycle = await db.scalar(
            select(CapitalCampaignProfitCycle)
            .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
            .order_by(CapitalCampaignProfitCycle.calculated_at.desc())
            .limit(1)
        )
        if latest_cycle is not None and latest_cycle.calculated_at + timedelta(hours=policy.cooldown_hours) > now and not force_new_cycle:
            return _to_cycle_response(latest_cycle)

    previously_allocated_profit = await _allocated_profit_to_date(db, campaign.id)
    realized_profit = _d(campaign.realized_profit)
    unrealized_profit = _d(campaign.unrealized_profit)
    fees = _d(campaign.fees)

    fee_reserve = (realized_profit * _d(policy.fee_reserve_percent) / Decimal("100"))
    tax_reserve = (realized_profit * _d(policy.tax_reserve_percent) / Decimal("100"))

    eligible_profit = realized_profit - fees - fee_reserve - tax_reserve - previously_allocated_profit
    if eligible_profit < 0:
        eligible_profit = Decimal("0")
    if eligible_profit < _d(policy.minimum_realized_profit):
        eligible_profit = Decimal("0")

    eligible_profit_after_cash_reserve = eligible_profit - _d(policy.minimum_cash_reserve)
    if eligible_profit_after_cash_reserve < 0:
        eligible_profit_after_cash_reserve = Decimal("0")

    # Settlement evidence is not yet durable for campaign-level withdrawals in v1.
    settlement_state = "SETTLEMENT_UNKNOWN"

    target_reached, target_progress_percent = _calculate_target_state(policy, campaign)

    compound_amount = Decimal("0")
    withdrawal_amount = Decimal("0")

    allocatable_profit = eligible_profit_after_cash_reserve

    if policy.policy_type == "FULL_COMPOUND":
        compound_amount = allocatable_profit
    elif policy.policy_type == "PARTIAL_COMPOUND":
        compound_amount = allocatable_profit * _d(policy.compound_percent) / Decimal("100")
        withdrawal_amount = allocatable_profit - compound_amount
    elif policy.policy_type == "WITHDRAW_PROFIT":
        target_withdraw = allocatable_profit * _d(policy.withdraw_percent) / Decimal("100")
        withdrawal_amount = target_withdraw if _d(policy.withdraw_percent) > 0 else allocatable_profit
    elif policy.policy_type == "WITHDRAW_AND_COMPOUND":
        compound_amount = allocatable_profit * _d(policy.compound_percent) / Decimal("100")
        withdrawal_amount = allocatable_profit * _d(policy.withdraw_percent) / Decimal("100")
        remainder = allocatable_profit - compound_amount - withdrawal_amount
        if remainder > 0:
            withdrawal_amount += remainder
    elif policy.policy_type == "PROTECTED_PRINCIPAL":
        protected_principal = _d(policy.protected_principal_amount)
        available_above_principal = _d(campaign.current_equity) - protected_principal
        if available_above_principal < 0:
            available_above_principal = Decimal("0")
        compound_amount = min(allocatable_profit, available_above_principal)
        withdrawal_amount = allocatable_profit - compound_amount
    elif policy.policy_type == "HOLD_PROFIT":
        pass

    if not target_reached:
        compound_amount = Decimal("0")
        withdrawal_amount = Decimal("0")

    if policy.maximum_campaign_capital is not None:
        max_compound_allowed = _d(policy.maximum_campaign_capital) - _d(campaign.starting_capital)
        if max_compound_allowed < 0:
            max_compound_allowed = Decimal("0")
        if compound_amount > max_compound_allowed:
            overflow = compound_amount - max_compound_allowed
            compound_amount = max_compound_allowed
            withdrawal_amount += overflow

    reserve_amount = eligible_profit - compound_amount - withdrawal_amount
    if reserve_amount < 0:
        reserve_amount = Decimal("0")

    closing_campaign_capital = _d(campaign.starting_capital) + compound_amount

    status = _recommendation_status(policy, target_reached, settlement_state)

    evidence_payload = {
        "campaign_uuid": str(campaign.uuid),
        "policy_uuid": str(policy.policy_uuid),
        "campaign_capital": format(_d(campaign.starting_capital), "f"),
        "campaign_equity": format(_d(campaign.current_equity), "f"),
        "realized_profit": format(realized_profit, "f"),
        "unrealized_profit": format(unrealized_profit, "f"),
        "fees": format(fees, "f"),
        "fee_reserve_percent": format(_d(policy.fee_reserve_percent), "f"),
        "tax_reserve_percent": format(_d(policy.tax_reserve_percent), "f"),
        "previously_allocated_profit": format(previously_allocated_profit, "f"),
        "minimum_cash_reserve": format(_d(policy.minimum_cash_reserve), "f"),
        "target_reached": target_reached,
        "target_progress_percent": None if target_progress_percent is None else format(target_progress_percent, "f"),
    }
    fingerprint = _fingerprint(evidence_payload)

    existing_same = await db.scalar(
        select(CapitalCampaignProfitCycle)
        .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitCycle.calculation_fingerprint == fingerprint)
        .where(CapitalCampaignProfitCycle.status.in_(["BELOW_TARGET", "TARGET_REACHED", "REVIEW_REQUIRED", "COMPOUNDING_RECOMMENDED", "WITHDRAWAL_RECOMMENDED", "APPROVED", "COMPLETED"]))
        .order_by(CapitalCampaignProfitCycle.created_at.desc())
        .limit(1)
    )
    if existing_same is not None and not force_new_cycle:
        return _to_cycle_response(existing_same)

    next_cycle_number = (_d(await db.scalar(select(func.max(CapitalCampaignProfitCycle.cycle_number)).where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id))) + Decimal("1"))

    snapshot = {
        **evidence_payload,
        "eligible_profit": format(eligible_profit, "f"),
        "eligible_profit_after_cash_reserve": format(eligible_profit_after_cash_reserve, "f"),
        "compound_amount": format(compound_amount, "f"),
        "withdrawal_amount": format(withdrawal_amount, "f"),
        "reserve_amount": format(reserve_amount, "f"),
        "closing_campaign_capital": format(closing_campaign_capital, "f"),
        "settlement_state": settlement_state,
        "explanation": "This is an accounting recommendation only. No funds will move.",
    }

    cycle = CapitalCampaignProfitCycle(
        capital_campaign_id=campaign.id,
        profit_policy_id=policy.policy_id,
        cycle_number=int(next_cycle_number),
        opening_capital=_d(campaign.starting_capital),
        opening_equity=_d(campaign.current_equity),
        realized_profit=realized_profit,
        unrealized_profit=unrealized_profit,
        fees=fees,
        eligible_profit=eligible_profit,
        compound_amount=compound_amount,
        withdrawal_amount=withdrawal_amount,
        reserve_amount=reserve_amount,
        closing_campaign_capital=closing_campaign_capital,
        target_reached=target_reached,
        status=status,
        settlement_state=settlement_state,
        calculation_snapshot=snapshot,
        calculation_fingerprint=fingerprint,
        calculated_at=now,
    )
    db.add(cycle)
    await db.flush()
    await db.refresh(cycle)

    await _record_event(
        db=db,
        action="PROFIT_CYCLE_EVALUATED",
        campaign_uuid=campaign.uuid,
        actor=actor,
        before_state=None,
        after_state={"cycle_uuid": str(cycle.cycle_uuid), "status": cycle.status},
    )
    if target_reached:
        await _record_event(
            db=db,
            action="PROFIT_TARGET_REACHED",
            campaign_uuid=campaign.uuid,
            actor=actor,
            before_state=None,
            after_state={"cycle_uuid": str(cycle.cycle_uuid), "status": cycle.status},
        )
    if cycle.compound_amount > 0:
        await _record_event(
            db=db,
            action="COMPOUNDING_RECOMMENDED",
            campaign_uuid=campaign.uuid,
            actor=actor,
            before_state=None,
            after_state={"cycle_uuid": str(cycle.cycle_uuid), "compound_amount": format(cycle.compound_amount, "f")},
        )
    if cycle.withdrawal_amount > 0:
        await _record_event(
            db=db,
            action="WITHDRAWAL_RECOMMENDED",
            campaign_uuid=campaign.uuid,
            actor=actor,
            before_state=None,
            after_state={"cycle_uuid": str(cycle.cycle_uuid), "withdrawal_amount": format(cycle.withdrawal_amount, "f")},
        )

    await db.commit()
    return _to_cycle_response(cycle)


async def list_profit_cycles(*, db: AsyncSession, campaign_uuid: uuid.UUID) -> list[CapitalCampaignProfitCycleResponse]:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    cycles = (
        (
            await db.execute(
                select(CapitalCampaignProfitCycle)
                .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
                .order_by(CapitalCampaignProfitCycle.cycle_number.desc(), CapitalCampaignProfitCycle.cycle_id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_cycle_response(item) for item in cycles]


async def get_profit_cycle(*, db: AsyncSession, campaign_uuid: uuid.UUID, cycle_uuid: uuid.UUID) -> CapitalCampaignProfitCycleResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    cycle = await db.scalar(
        select(CapitalCampaignProfitCycle)
        .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitCycle.cycle_uuid == cycle_uuid)
        .limit(1)
    )
    if cycle is None:
        raise NotFoundError(message="Profit cycle not found", details={"cycle_uuid": str(cycle_uuid)})
    return _to_cycle_response(cycle)


async def approve_profit_cycle(
    *,
    db: AsyncSession,
    campaign_uuid: uuid.UUID,
    cycle_uuid: uuid.UUID,
    actor: str,
) -> CapitalCampaignProfitCycleResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    cycle = await db.scalar(
        select(CapitalCampaignProfitCycle)
        .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitCycle.cycle_uuid == cycle_uuid)
        .limit(1)
    )
    if cycle is None:
        raise NotFoundError(message="Profit cycle not found", details={"cycle_uuid": str(cycle_uuid)})
    if cycle.status not in {"REVIEW_REQUIRED", "TARGET_REACHED", "COMPOUNDING_RECOMMENDED", "WITHDRAWAL_RECOMMENDED"}:
        raise ConflictError(message="Profit cycle cannot be approved from current status", details={"status": cycle.status})

    cycle.status = "APPROVED"
    cycle.approved_at = datetime.now(timezone.utc)
    cycle.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await _record_event(
        db=db,
        action="PROFIT_CYCLE_APPROVED",
        campaign_uuid=campaign.uuid,
        actor=actor,
        before_state=None,
        after_state={"cycle_uuid": str(cycle.cycle_uuid), "status": cycle.status},
    )

    await db.commit()
    await db.refresh(cycle)
    return _to_cycle_response(cycle)


async def reject_profit_cycle(
    *,
    db: AsyncSession,
    campaign_uuid: uuid.UUID,
    cycle_uuid: uuid.UUID,
    actor: str,
    reason: str | None,
) -> CapitalCampaignProfitCycleResponse:
    campaign = await _get_campaign_by_uuid(db, campaign_uuid)
    cycle = await db.scalar(
        select(CapitalCampaignProfitCycle)
        .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
        .where(CapitalCampaignProfitCycle.cycle_uuid == cycle_uuid)
        .limit(1)
    )
    if cycle is None:
        raise NotFoundError(message="Profit cycle not found", details={"cycle_uuid": str(cycle_uuid)})
    if cycle.status in {"CANCELLED", "COMPLETED"}:
        raise ConflictError(message="Profit cycle already terminal", details={"status": cycle.status})

    cycle.status = "CANCELLED"
    cycle.updated_at = datetime.now(timezone.utc)
    snapshot = dict(cycle.calculation_snapshot or {})
    if reason:
        snapshot["rejection_reason"] = reason
    cycle.calculation_snapshot = snapshot

    await db.flush()

    await _record_event(
        db=db,
        action="PROFIT_CYCLE_REJECTED",
        campaign_uuid=campaign.uuid,
        actor=actor,
        before_state=None,
        after_state={"cycle_uuid": str(cycle.cycle_uuid), "status": cycle.status, "reason": reason},
    )

    await db.commit()
    await db.refresh(cycle)
    return _to_cycle_response(cycle)
