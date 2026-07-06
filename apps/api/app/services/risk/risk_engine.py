from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
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
    resized_quantity: Decimal | None = None
    meets_minimum_viable_order: bool = True
    ai_scaled_quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskEvaluationResult:
    action: RiskDecisionAction
    reason_code: str | None
    approved_quantity: Decimal
    steps: list[RiskEvaluationStep] = field(default_factory=list)


def evaluate_signal_risk(
    *,
    request: RiskEvaluationRequest,
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
    if resolved_context.resized_quantity is not None and resolved_context.resized_quantity < approved_quantity:
        approved_quantity = resolved_context.resized_quantity
        resized = True
    steps.append(RiskEvaluationStep(step="position_size", status="resize" if resized else "pass", reason_code="position_resized_by_risk_engine" if resized else None))

    steps.append(RiskEvaluationStep(step="minimum_viable_order_pre_ai", status="reject" if not resolved_context.meets_minimum_viable_order else "pass", reason_code="position_below_minimum_order_size" if not resolved_context.meets_minimum_viable_order else None))
    if not resolved_context.meets_minimum_viable_order:
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

    minimum_viable_after_ai = approved_quantity > 0 and resolved_context.meets_minimum_viable_order
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