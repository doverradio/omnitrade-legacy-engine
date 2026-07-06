from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
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
    start_of_day_equity: Decimal | None = None
    current_equity: Decimal | None = None
    max_daily_loss_pct: Decimal | None = None
    high_water_mark_equity: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    consecutive_losses_on_pair: int | None = None
    cooldown_after_losses: int | None = None
    last_loss_at: datetime | None = None
    cooldown_duration_minutes: Decimal | None = None
    evaluation_time: datetime | None = None
    session_open_time: datetime | None = None
    session_close_time: datetime | None = None
    no_trade_open_minutes: Decimal | None = None
    no_trade_close_minutes: Decimal | None = None
    data_is_stale: bool | None = None
    data_has_gaps: bool | None = None
    global_kill_switch_engaged_state: bool | None = None
    global_kill_switch_rearm_required: bool | None = None
    global_kill_switch_rearmed_by_human: bool | None = None
    global_kill_switch_state_observed: bool = False
    account_kill_switch_engaged_state: bool | None = None
    account_kill_switch_rearm_required: bool | None = None
    account_kill_switch_rearmed_by_human: bool | None = None
    account_kill_switch_state_observed: bool = False
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
class DailyLossValidationResult:
    breached: bool
    loss_pct: Decimal | None
    threshold_pct: Decimal | None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class DrawdownValidationResult:
    breached: bool
    drawdown_pct: Decimal | None
    threshold_pct: Decimal | None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class CooldownValidationResult:
    active: bool
    remaining_minutes: Decimal | None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class NoTradeZoneValidationResult:
    in_zone: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class KillSwitchValidationResult:
    block_trading: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class KillSwitchRearmResult:
    engaged: bool
    rearm_required: bool
    rearmed_by_human: bool
    state_changed: bool
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


def validate_daily_loss_limit(
    *,
    start_of_day_equity: Decimal,
    current_equity: Decimal,
    max_daily_loss_pct: Decimal,
) -> DailyLossValidationResult:
    if start_of_day_equity <= 0:
        return DailyLossValidationResult(
            breached=False,
            loss_pct=None,
            threshold_pct=max_daily_loss_pct,
            reason_code="invalid_start_of_day_equity",
        )

    if max_daily_loss_pct <= 0:
        return DailyLossValidationResult(
            breached=False,
            loss_pct=None,
            threshold_pct=max_daily_loss_pct,
            reason_code="invalid_max_daily_loss_pct",
        )

    loss_pct = (start_of_day_equity - current_equity) / start_of_day_equity
    loss_pct = loss_pct if loss_pct > 0 else Decimal("0")

    return DailyLossValidationResult(
        breached=loss_pct >= max_daily_loss_pct,
        loss_pct=loss_pct,
        threshold_pct=max_daily_loss_pct,
        reason_code="max_daily_loss_breached" if loss_pct >= max_daily_loss_pct else None,
    )


def validate_max_drawdown(
    *,
    high_water_mark_equity: Decimal,
    current_equity: Decimal,
    max_drawdown_pct: Decimal,
) -> DrawdownValidationResult:
    if high_water_mark_equity <= 0:
        return DrawdownValidationResult(
            breached=False,
            drawdown_pct=None,
            threshold_pct=max_drawdown_pct,
            reason_code="invalid_high_water_mark_equity",
        )

    if max_drawdown_pct <= 0:
        return DrawdownValidationResult(
            breached=False,
            drawdown_pct=None,
            threshold_pct=max_drawdown_pct,
            reason_code="invalid_max_drawdown_pct",
        )

    drawdown_pct = (high_water_mark_equity - current_equity) / high_water_mark_equity
    drawdown_pct = drawdown_pct if drawdown_pct > 0 else Decimal("0")

    return DrawdownValidationResult(
        breached=drawdown_pct >= max_drawdown_pct,
        drawdown_pct=drawdown_pct,
        threshold_pct=max_drawdown_pct,
        reason_code="max_drawdown_breached" if drawdown_pct >= max_drawdown_pct else None,
    )


def validate_strategy_asset_cooldown(
    *,
    consecutive_losses_on_pair: int,
    cooldown_after_losses: int,
    last_loss_at: datetime,
    cooldown_duration_minutes: Decimal,
    evaluation_time: datetime,
) -> CooldownValidationResult:
    if consecutive_losses_on_pair < 0:
        return CooldownValidationResult(active=False, remaining_minutes=None, reason_code="invalid_consecutive_losses")

    if cooldown_after_losses <= 0:
        return CooldownValidationResult(active=False, remaining_minutes=None, reason_code="invalid_cooldown_after_losses")

    if cooldown_duration_minutes <= 0:
        return CooldownValidationResult(active=False, remaining_minutes=None, reason_code="invalid_cooldown_duration_minutes")

    if consecutive_losses_on_pair < cooldown_after_losses:
        return CooldownValidationResult(active=False, remaining_minutes=Decimal("0"), reason_code=None)

    elapsed_minutes = Decimal(str((evaluation_time - last_loss_at).total_seconds())) / Decimal("60")
    if elapsed_minutes < 0:
        return CooldownValidationResult(active=False, remaining_minutes=None, reason_code="invalid_cooldown_timestamps")

    remaining_minutes = cooldown_duration_minutes - elapsed_minutes
    if remaining_minutes > 0:
        return CooldownValidationResult(
            active=True,
            remaining_minutes=remaining_minutes,
            reason_code="strategy_asset_cooldown_active",
        )

    return CooldownValidationResult(active=False, remaining_minutes=Decimal("0"), reason_code=None)


def validate_no_trade_zone(
    *,
    evaluation_time: datetime,
    session_open_time: datetime | None,
    session_close_time: datetime | None,
    no_trade_open_minutes: Decimal | None,
    no_trade_close_minutes: Decimal | None,
    data_is_stale: bool,
    data_has_gaps: bool,
) -> NoTradeZoneValidationResult:
    if data_is_stale or data_has_gaps:
        return NoTradeZoneValidationResult(in_zone=True, reason_code="asset_in_no_trade_zone_data_quality")

    has_time_guardrails = (no_trade_open_minutes is not None and no_trade_open_minutes > 0) or (
        no_trade_close_minutes is not None and no_trade_close_minutes > 0
    )
    if not has_time_guardrails:
        return NoTradeZoneValidationResult(in_zone=False, reason_code=None)

    if session_open_time is None or session_close_time is None:
        return NoTradeZoneValidationResult(in_zone=False, reason_code="invalid_session_window")

    if session_close_time <= session_open_time:
        return NoTradeZoneValidationResult(in_zone=False, reason_code="invalid_session_window")

    if no_trade_open_minutes is not None and no_trade_open_minutes < 0:
        return NoTradeZoneValidationResult(in_zone=False, reason_code="invalid_no_trade_open_minutes")

    if no_trade_close_minutes is not None and no_trade_close_minutes < 0:
        return NoTradeZoneValidationResult(in_zone=False, reason_code="invalid_no_trade_close_minutes")

    minutes_since_open = Decimal(str((evaluation_time - session_open_time).total_seconds())) / Decimal("60")
    minutes_until_close = Decimal(str((session_close_time - evaluation_time).total_seconds())) / Decimal("60")

    if no_trade_open_minutes is not None and no_trade_open_minutes > 0 and minutes_since_open >= 0 and minutes_since_open < no_trade_open_minutes:
        return NoTradeZoneValidationResult(in_zone=True, reason_code="asset_in_no_trade_zone_time_window")

    if no_trade_close_minutes is not None and no_trade_close_minutes > 0 and minutes_until_close >= 0 and minutes_until_close < no_trade_close_minutes:
        return NoTradeZoneValidationResult(in_zone=True, reason_code="asset_in_no_trade_zone_time_window")

    return NoTradeZoneValidationResult(in_zone=False, reason_code=None)


def validate_kill_switch_state(
    *,
    scope: str,
    engaged_state: bool | None,
    rearm_required: bool | None,
    rearmed_by_human: bool | None,
) -> KillSwitchValidationResult:
    scope_prefix = "global" if scope == "global" else "account"

    if engaged_state is None:
        return KillSwitchValidationResult(
            block_trading=True,
            reason_code=f"{scope_prefix}_kill_switch_state_unknown",
        )

    if rearm_required is None:
        return KillSwitchValidationResult(
            block_trading=True,
            reason_code=f"{scope_prefix}_kill_switch_rearm_state_unknown",
        )

    if engaged_state:
        return KillSwitchValidationResult(
            block_trading=True,
            reason_code=f"{scope_prefix}_kill_switch_engaged",
        )

    if rearm_required and rearmed_by_human is None:
        return KillSwitchValidationResult(
            block_trading=True,
            reason_code=f"{scope_prefix}_kill_switch_rearm_state_unknown",
        )

    if rearm_required and not rearmed_by_human:
        return KillSwitchValidationResult(
            block_trading=True,
            reason_code=f"{scope_prefix}_kill_switch_requires_manual_rearm",
        )

    return KillSwitchValidationResult(block_trading=False, reason_code=None)


def apply_manual_kill_switch_rearm(
    *,
    engaged: bool,
    rearm_required: bool,
    actor_is_human: bool,
) -> KillSwitchRearmResult:
    if not actor_is_human:
        return KillSwitchRearmResult(
            engaged=engaged,
            rearm_required=rearm_required,
            rearmed_by_human=False,
            state_changed=False,
            reason_code="manual_rearm_requires_human_actor",
        )

    if not rearm_required:
        return KillSwitchRearmResult(
            engaged=engaged,
            rearm_required=False,
            rearmed_by_human=True,
            state_changed=False,
            reason_code="kill_switch_rearm_not_required",
        )

    return KillSwitchRearmResult(
        engaged=False,
        rearm_required=False,
        rearmed_by_human=True,
        state_changed=True,
        reason_code="kill_switch_manual_rearm_completed",
    )


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

    global_kill_switch_blocked = resolved_context.global_kill_switch_engaged
    global_kill_switch_reason = "global_kill_switch_engaged" if resolved_context.global_kill_switch_engaged else None
    if (
        request.global_kill_switch_state_observed
        or
        request.global_kill_switch_engaged_state is not None
        or request.global_kill_switch_rearm_required is not None
        or request.global_kill_switch_rearmed_by_human is not None
    ):
        global_kill_switch_result = validate_kill_switch_state(
            scope="global",
            engaged_state=request.global_kill_switch_engaged_state,
            rearm_required=request.global_kill_switch_rearm_required,
            rearmed_by_human=request.global_kill_switch_rearmed_by_human,
        )
        global_kill_switch_blocked = global_kill_switch_result.block_trading
        global_kill_switch_reason = global_kill_switch_result.reason_code

    steps.append(RiskEvaluationStep(step="global_kill_switch", status="reject" if global_kill_switch_blocked else "pass", reason_code=global_kill_switch_reason))
    if global_kill_switch_blocked:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code=global_kill_switch_reason,
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    account_kill_switch_blocked = resolved_context.account_trading_paused
    account_kill_switch_reason = "account_trading_paused" if resolved_context.account_trading_paused else None
    if (
        request.account_kill_switch_state_observed
        or
        request.account_kill_switch_engaged_state is not None
        or request.account_kill_switch_rearm_required is not None
        or request.account_kill_switch_rearmed_by_human is not None
    ):
        account_kill_switch_result = validate_kill_switch_state(
            scope="account",
            engaged_state=request.account_kill_switch_engaged_state,
            rearm_required=request.account_kill_switch_rearm_required,
            rearmed_by_human=request.account_kill_switch_rearmed_by_human,
        )
        account_kill_switch_blocked = account_kill_switch_result.block_trading
        account_kill_switch_reason = account_kill_switch_result.reason_code

    steps.append(RiskEvaluationStep(step="account_pause", status="reject" if account_kill_switch_blocked else "pass", reason_code=account_kill_switch_reason))
    if account_kill_switch_blocked:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code=account_kill_switch_reason,
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    asset_in_no_trade_zone = resolved_context.asset_in_no_trade_zone
    if (
        request.evaluation_time is not None
        and (
            request.data_is_stale is not None
            or request.data_has_gaps is not None
            or request.no_trade_open_minutes is not None
            or request.no_trade_close_minutes is not None
        )
    ):
        no_trade_result = validate_no_trade_zone(
            evaluation_time=request.evaluation_time,
            session_open_time=request.session_open_time,
            session_close_time=request.session_close_time,
            no_trade_open_minutes=request.no_trade_open_minutes,
            no_trade_close_minutes=request.no_trade_close_minutes,
            data_is_stale=bool(request.data_is_stale),
            data_has_gaps=bool(request.data_has_gaps),
        )
        if no_trade_result.reason_code in {
            "invalid_session_window",
            "invalid_no_trade_open_minutes",
            "invalid_no_trade_close_minutes",
        }:
            return RiskEvaluationResult(
                action=RiskDecisionAction.REJECT,
                reason_code=no_trade_result.reason_code,
                approved_quantity=Decimal("0"),
                steps=steps,
            )
        asset_in_no_trade_zone = no_trade_result.in_zone

    steps.append(RiskEvaluationStep(step="no_trade_zone", status="reject" if asset_in_no_trade_zone else "pass", reason_code="asset_in_no_trade_zone" if asset_in_no_trade_zone else None))
    if asset_in_no_trade_zone:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="asset_in_no_trade_zone",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    pair_in_cooldown = resolved_context.pair_in_cooldown
    if (
        request.consecutive_losses_on_pair is not None
        and request.cooldown_after_losses is not None
        and request.last_loss_at is not None
        and request.cooldown_duration_minutes is not None
        and request.evaluation_time is not None
    ):
        cooldown_result = validate_strategy_asset_cooldown(
            consecutive_losses_on_pair=request.consecutive_losses_on_pair,
            cooldown_after_losses=request.cooldown_after_losses,
            last_loss_at=request.last_loss_at,
            cooldown_duration_minutes=request.cooldown_duration_minutes,
            evaluation_time=request.evaluation_time,
        )
        if cooldown_result.reason_code in {
            "invalid_consecutive_losses",
            "invalid_cooldown_after_losses",
            "invalid_cooldown_duration_minutes",
            "invalid_cooldown_timestamps",
        }:
            return RiskEvaluationResult(
                action=RiskDecisionAction.REJECT,
                reason_code=cooldown_result.reason_code,
                approved_quantity=Decimal("0"),
                steps=steps,
            )
        pair_in_cooldown = cooldown_result.active

    steps.append(RiskEvaluationStep(step="cooldown", status="reject" if pair_in_cooldown else "pass", reason_code="strategy_asset_cooldown_active" if pair_in_cooldown else None))
    if pair_in_cooldown:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="strategy_asset_cooldown_active",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    would_breach_daily_loss = resolved_context.would_breach_daily_loss
    if (
        request.start_of_day_equity is not None
        and request.current_equity is not None
        and request.max_daily_loss_pct is not None
    ):
        daily_loss_result = validate_daily_loss_limit(
            start_of_day_equity=request.start_of_day_equity,
            current_equity=request.current_equity,
            max_daily_loss_pct=request.max_daily_loss_pct,
        )
        if daily_loss_result.reason_code in {"invalid_start_of_day_equity", "invalid_max_daily_loss_pct"}:
            return RiskEvaluationResult(
                action=RiskDecisionAction.REJECT,
                reason_code=daily_loss_result.reason_code,
                approved_quantity=Decimal("0"),
                steps=steps,
            )
        would_breach_daily_loss = daily_loss_result.breached

    steps.append(RiskEvaluationStep(step="daily_loss", status="reject" if would_breach_daily_loss else "pass", reason_code="max_daily_loss_breached" if would_breach_daily_loss else None))
    if would_breach_daily_loss:
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="max_daily_loss_breached",
            approved_quantity=Decimal("0"),
            steps=steps,
        )

    would_breach_drawdown = resolved_context.would_breach_drawdown
    if (
        request.high_water_mark_equity is not None
        and request.current_equity is not None
        and request.max_drawdown_pct is not None
    ):
        drawdown_result = validate_max_drawdown(
            high_water_mark_equity=request.high_water_mark_equity,
            current_equity=request.current_equity,
            max_drawdown_pct=request.max_drawdown_pct,
        )
        if drawdown_result.reason_code in {"invalid_high_water_mark_equity", "invalid_max_drawdown_pct"}:
            return RiskEvaluationResult(
                action=RiskDecisionAction.REJECT,
                reason_code=drawdown_result.reason_code,
                approved_quantity=Decimal("0"),
                steps=steps,
            )
        would_breach_drawdown = drawdown_result.breached

    steps.append(RiskEvaluationStep(step="drawdown", status="reject" if would_breach_drawdown else "pass", reason_code="max_drawdown_breached" if would_breach_drawdown else None))
    if would_breach_drawdown:
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