from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from enum import Enum


class RiskDecisionAction(str, Enum):
    APPROVE = "approve"
    RESIZE = "resize"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RiskEvaluationRequest:
    signal_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    account_equity: Decimal | None = None
    max_position_size_pct: Decimal | None = None
    min_order_notional: Decimal | None = None
    qty_step_size: Decimal | None = None
    supports_fractional: bool | None = None
    actor: str = "system"
    ai_confidence: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskEvaluationStep:
    step: str
    status: str
    reason_code: str | None = None


@dataclass(slots=True)
class RiskEvaluationContext:
    global_kill_switch_engaged: bool = False
    account_trading_paused: bool = False
    asset_in_no_trade_zone: bool = False
    pair_in_cooldown: bool = False
    would_breach_daily_loss: bool = False
    would_breach_drawdown: bool = False
    has_computable_stop_loss: bool = True
    bypass_sizing_rule: bool = False
    ai_scaled_quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    requested_quantity: Decimal
    approved_quantity: Decimal
    was_resized: bool
    max_position_notional: Decimal | None
    approved_notional: Decimal
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class RiskEvaluationResult:
    action: RiskDecisionAction
    reason_code: str | None
    approved_quantity: Decimal
    steps: list[RiskEvaluationStep] = field(default_factory=list)


def _round_down_to_step(value: Decimal, step: Decimal | None) -> Decimal:
    if step is None or step <= 0:
        return value

    increments = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return increments * step


def compute_position_sizing(
    *,
    requested_quantity: Decimal,
    reference_price: Decimal,
    account_equity: Decimal,
    max_position_size_pct: Decimal,
    qty_step_size: Decimal | None,
    supports_fractional: bool | None,
) -> PositionSizingResult:
    if requested_quantity <= 0:
        return PositionSizingResult(
            requested_quantity=requested_quantity,
            approved_quantity=Decimal("0"),
            was_resized=False,
            max_position_notional=None,
            approved_notional=Decimal("0"),
            reason_code="invalid_requested_quantity",
        )

    if reference_price <= 0:
        return PositionSizingResult(
            requested_quantity=requested_quantity,
            approved_quantity=Decimal("0"),
            was_resized=False,
            max_position_notional=None,
            approved_notional=Decimal("0"),
            reason_code="invalid_reference_price",
        )

    if account_equity <= 0:
        return PositionSizingResult(
            requested_quantity=requested_quantity,
            approved_quantity=Decimal("0"),
            was_resized=False,
            max_position_notional=Decimal("0"),
            approved_notional=Decimal("0"),
            reason_code="non_positive_account_equity",
        )

    if max_position_size_pct <= 0:
        return PositionSizingResult(
            requested_quantity=requested_quantity,
            approved_quantity=Decimal("0"),
            was_resized=False,
            max_position_notional=Decimal("0"),
            approved_notional=Decimal("0"),
            reason_code="invalid_max_position_size_pct",
        )

    max_notional = account_equity * max_position_size_pct
    requested_notional = requested_quantity * reference_price
    approved_notional = requested_notional if requested_notional <= max_notional else max_notional

    approved_quantity = approved_notional / reference_price

    if supports_fractional is False and (qty_step_size is None or qty_step_size <= 0):
        qty_step_size = Decimal("1")

    approved_quantity = _round_down_to_step(approved_quantity, qty_step_size)
    approved_notional = approved_quantity * reference_price

    return PositionSizingResult(
        requested_quantity=requested_quantity,
        approved_quantity=approved_quantity,
        was_resized=approved_quantity < requested_quantity,
        max_position_notional=max_notional,
        approved_notional=approved_notional,
        reason_code="position_resized_by_risk_engine" if approved_quantity < requested_quantity else None,
    )


def validate_minimum_viable_order(
    *,
    approved_quantity: Decimal,
    reference_price: Decimal,
    min_order_notional: Decimal | None,
    qty_step_size: Decimal | None,
) -> bool:
    if approved_quantity <= 0:
        return False

    if qty_step_size is not None and qty_step_size > 0 and approved_quantity < qty_step_size:
        return False

    if min_order_notional is not None and min_order_notional > 0:
        return (approved_quantity * reference_price) >= min_order_notional

    return True


def evaluate_signal_risk(
    *,
    request: RiskEvaluationRequest,
    reference_price: Decimal | None = None,
    context: RiskEvaluationContext | None = None,
) -> RiskEvaluationResult:
    """Deterministic Prompt 6.1 scaffold for risk evaluation ordering.

    This function establishes the canonical evaluation order and decision contract.
    Rule math and persistence behavior are intentionally deferred to later prompts.
    """

    resolved_context = context or RiskEvaluationContext()
    approved_quantity = request.quantity
    steps: list[RiskEvaluationStep] = []

    steps.append(RiskEvaluationStep(step="global_kill_switch", status="reject" if resolved_context.global_kill_switch_engaged else "pass", reason_code="global_kill_switch_engaged" if resolved_context.global_kill_switch_engaged else None))
    if resolved_context.global_kill_switch_engaged:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="global_kill_switch_engaged",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="account_pause", status="reject" if resolved_context.account_trading_paused else "pass", reason_code="account_trading_paused" if resolved_context.account_trading_paused else None))
    if resolved_context.account_trading_paused:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="account_trading_paused",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="no_trade_zone", status="reject" if resolved_context.asset_in_no_trade_zone else "pass", reason_code="asset_in_no_trade_zone" if resolved_context.asset_in_no_trade_zone else None))
    if resolved_context.asset_in_no_trade_zone:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="asset_in_no_trade_zone",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="cooldown", status="reject" if resolved_context.pair_in_cooldown else "pass", reason_code="strategy_asset_cooldown_active" if resolved_context.pair_in_cooldown else None))
    if resolved_context.pair_in_cooldown:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="strategy_asset_cooldown_active",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="daily_loss", status="reject" if resolved_context.would_breach_daily_loss else "pass", reason_code="max_daily_loss_breached" if resolved_context.would_breach_daily_loss else None))
    if resolved_context.would_breach_daily_loss:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="max_daily_loss_breached",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="drawdown", status="reject" if resolved_context.would_breach_drawdown else "pass", reason_code="max_drawdown_breached" if resolved_context.would_breach_drawdown else None))
    if resolved_context.would_breach_drawdown:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="max_drawdown_breached",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    steps.append(RiskEvaluationStep(step="stop_loss", status="reject" if not resolved_context.has_computable_stop_loss else "pass", reason_code="missing_stop_loss" if not resolved_context.has_computable_stop_loss else None))
    if not resolved_context.has_computable_stop_loss:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="missing_stop_loss",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    resized = False
    if not resolved_context.bypass_sizing_rule:
        if (
            reference_price is not None
            and request.account_equity is not None
            and request.max_position_size_pct is not None
        ):
            sizing_result = compute_position_sizing(
                requested_quantity=request.quantity,
                reference_price=reference_price,
                account_equity=request.account_equity,
                max_position_size_pct=request.max_position_size_pct,
                qty_step_size=request.qty_step_size,
                supports_fractional=request.supports_fractional,
            )
            if sizing_result.reason_code in {
                "invalid_requested_quantity",
                "invalid_reference_price",
                "non_positive_account_equity",
                "invalid_max_position_size_pct",
            }:
                return RiskEvaluationResult(
                    action=RiskDecisionAction.REJECT,
                    reason_code=sizing_result.reason_code,
                    approved_quantity=Decimal("0"),
                    steps=steps,
                )
            approved_quantity = sizing_result.approved_quantity
            resized = sizing_result.was_resized
        elif resolved_context.ai_scaled_quantity is not None and resolved_context.ai_scaled_quantity < approved_quantity:
            approved_quantity = resolved_context.ai_scaled_quantity
            resized = True

    steps.append(RiskEvaluationStep(step="position_size", status="resize" if resized else "pass", reason_code="position_resized_by_risk_engine" if resized else None))

    minimum_viable_pre_ai = validate_minimum_viable_order(
        approved_quantity=approved_quantity,
        reference_price=reference_price or Decimal("1"),
        min_order_notional=request.min_order_notional,
        qty_step_size=request.qty_step_size,
    )

    steps.append(RiskEvaluationStep(step="minimum_viable_order_pre_ai", status="reject" if not minimum_viable_pre_ai else "pass", reason_code="position_below_minimum_order_size" if not minimum_viable_pre_ai else None))
    if not minimum_viable_pre_ai:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="position_below_minimum_order_size",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    ai_scaled = False
    if resolved_context.ai_scaled_quantity is not None and resolved_context.ai_scaled_quantity < approved_quantity:
        approved_quantity = resolved_context.ai_scaled_quantity
        ai_scaled = True
    steps.append(RiskEvaluationStep(step="ai_confidence_scaling", status="resize" if ai_scaled else "pass", reason_code="position_resized_by_ai_confidence" if ai_scaled else None))

    minimum_viable_after_ai = validate_minimum_viable_order(
        approved_quantity=approved_quantity,
        reference_price=reference_price or Decimal("1"),
        min_order_notional=request.min_order_notional,
        qty_step_size=request.qty_step_size,
    )
    steps.append(RiskEvaluationStep(step="minimum_viable_order_post_ai", status="reject" if not minimum_viable_after_ai else "pass", reason_code="position_below_minimum_order_size" if not minimum_viable_after_ai else None))
    if not minimum_viable_after_ai:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="position_below_minimum_order_size",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    final_action = RiskDecisionAction.RESIZE if approved_quantity < request.quantity else RiskDecisionAction.APPROVE
    final_reason = "position_resized_by_risk_engine" if final_action == RiskDecisionAction.RESIZE else None

    return RiskEvaluationResult(
        action=final_action,
        reason_code=final_reason,
        approved_quantity=approved_quantity,
        steps=steps,
    )