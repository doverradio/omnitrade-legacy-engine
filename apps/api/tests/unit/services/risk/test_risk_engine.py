from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.risk import (
    RiskDecisionAction,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    apply_manual_kill_switch_rearm,
    compute_position_sizing,
    evaluate_signal_risk,
    validate_no_trade_zone,
    validate_daily_loss_limit,
    validate_kill_switch_state,
    validate_max_drawdown,
    validate_strategy_asset_cooldown,
    validate_minimum_viable_order,
)


def _request(quantity: str = "0.5") -> RiskEvaluationRequest:
    return RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal(quantity),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("100"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("110"),
        max_drawdown_pct=Decimal("0.15"),
    )


def test_evaluate_signal_risk_approves_when_context_is_clear() -> None:
    result = evaluate_signal_risk(request=_request(), reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.APPROVE
    assert result.reason_code is None
    assert result.approved_quantity == Decimal("0.5")
    assert [step.step for step in result.steps] == [
        "global_kill_switch",
        "account_pause",
        "no_trade_zone",
        "cooldown",
        "daily_loss",
        "drawdown",
        "stop_loss",
        "position_size",
        "minimum_viable_order_pre_ai",
        "ai_confidence_scaling",
        "minimum_viable_order_post_ai",
    ]


def test_evaluate_signal_risk_short_circuits_on_earliest_rejection() -> None:
    result = evaluate_signal_risk(
        request=_request(),
        reference_price=Decimal("10"),
        context=RiskEvaluationContext(global_kill_switch_engaged=True, would_breach_daily_loss=True),
    )

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "global_kill_switch_engaged"
    assert result.approved_quantity == Decimal("0")
    assert len(result.steps) == 1
    assert result.steps[0].step == "global_kill_switch"


def test_evaluate_signal_risk_returns_resize_when_quantity_reduced() -> None:
    result = evaluate_signal_risk(
        request=_request(quantity="1.0"),
        reference_price=Decimal("10"),
    )

    assert result.action == RiskDecisionAction.RESIZE
    assert result.reason_code == "position_resized_by_risk_engine"
    assert result.approved_quantity == Decimal("0.50")
    assert any(step.step == "position_size" and step.status == "resize" for step in result.steps)


def test_evaluate_signal_risk_rejects_missing_stop_loss() -> None:
    result = evaluate_signal_risk(
        request=_request(),
        reference_price=Decimal("10"),
        context=RiskEvaluationContext(has_computable_stop_loss=False),
    )

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "missing_stop_loss"
    assert result.approved_quantity == Decimal("0")


def test_evaluate_signal_risk_rejects_below_minimum_viable_order() -> None:
    result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            side="buy",
            quantity=Decimal("0.5"),
            account_equity=Decimal("25"),
            max_position_size_pct=Decimal("0.05"),
            min_order_notional=Decimal("5"),
            qty_step_size=Decimal("0.0001"),
            supports_fractional=True,
        ),
        reference_price=Decimal("100"),
    )

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "position_below_minimum_order_size"
    assert result.approved_quantity == Decimal("0")


def test_compute_position_sizing_uses_percentage_of_equity_not_fixed_dollars() -> None:
    sizing = compute_position_sizing(
        requested_quantity=Decimal("10"),
        reference_price=Decimal("20"),
        account_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.05"),
        qty_step_size=Decimal("0.0001"),
        supports_fractional=True,
    )

    assert sizing.max_position_notional == Decimal("1.25")
    assert sizing.approved_quantity == Decimal("0.0625")
    assert sizing.was_resized is True
    assert sizing.reason_code == "position_resized_by_risk_engine"


def test_compute_position_sizing_respects_non_fractional_stock_constraints() -> None:
    sizing = compute_position_sizing(
        requested_quantity=Decimal("2.8"),
        reference_price=Decimal("50"),
        account_equity=Decimal("1000"),
        max_position_size_pct=Decimal("0.2"),
        qty_step_size=None,
        supports_fractional=False,
    )

    assert sizing.approved_quantity == Decimal("2")
    assert sizing.approved_notional == Decimal("100")


def test_validate_minimum_viable_order_checks_notional_and_step_size() -> None:
    assert validate_minimum_viable_order(
        approved_quantity=Decimal("0.02"),
        reference_price=Decimal("100"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.0001"),
    )

    assert not validate_minimum_viable_order(
        approved_quantity=Decimal("0.005"),
        reference_price=Decimal("100"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.0001"),
    )

    assert not validate_minimum_viable_order(
        approved_quantity=Decimal("0.00001"),
        reference_price=Decimal("100"),
        min_order_notional=Decimal("0.0005"),
        qty_step_size=Decimal("0.001"),
    )


def test_validate_daily_loss_limit_breaches_when_threshold_crossed() -> None:
    result = validate_daily_loss_limit(
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("96"),
        max_daily_loss_pct=Decimal("0.03"),
    )

    assert result.breached is True
    assert result.loss_pct == Decimal("0.04")
    assert result.reason_code == "max_daily_loss_breached"


def test_validate_daily_loss_limit_rejects_invalid_inputs() -> None:
    result = validate_daily_loss_limit(
        start_of_day_equity=Decimal("0"),
        current_equity=Decimal("96"),
        max_daily_loss_pct=Decimal("0.03"),
    )

    assert result.breached is False
    assert result.loss_pct is None
    assert result.reason_code == "invalid_start_of_day_equity"


def test_validate_max_drawdown_breaches_when_threshold_crossed() -> None:
    result = validate_max_drawdown(
        high_water_mark_equity=Decimal("120"),
        current_equity=Decimal("96"),
        max_drawdown_pct=Decimal("0.15"),
    )

    assert result.breached is True
    assert result.drawdown_pct == Decimal("0.2")
    assert result.reason_code == "max_drawdown_breached"


def test_validate_max_drawdown_rejects_invalid_inputs() -> None:
    result = validate_max_drawdown(
        high_water_mark_equity=Decimal("0"),
        current_equity=Decimal("96"),
        max_drawdown_pct=Decimal("0.15"),
    )

    assert result.breached is False
    assert result.drawdown_pct is None
    assert result.reason_code == "invalid_high_water_mark_equity"


def test_evaluate_signal_risk_rejects_when_daily_loss_breached() -> None:
    request = RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.5"),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("95"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("110"),
        max_drawdown_pct=Decimal("0.15"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "max_daily_loss_breached"


def test_evaluate_signal_risk_rejects_when_drawdown_breached() -> None:
    request = RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.5"),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("100"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("120"),
        max_drawdown_pct=Decimal("0.15"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "max_drawdown_breached"


def test_validate_strategy_asset_cooldown_activates_within_cooldown_window() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    result = validate_strategy_asset_cooldown(
        consecutive_losses_on_pair=3,
        cooldown_after_losses=3,
        last_loss_at=now - timedelta(minutes=30),
        cooldown_duration_minutes=Decimal("60"),
        evaluation_time=now,
    )

    assert result.active is True
    assert result.reason_code == "strategy_asset_cooldown_active"
    assert result.remaining_minutes == Decimal("30")


def test_validate_strategy_asset_cooldown_expires_after_duration() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    result = validate_strategy_asset_cooldown(
        consecutive_losses_on_pair=3,
        cooldown_after_losses=3,
        last_loss_at=now - timedelta(minutes=61),
        cooldown_duration_minutes=Decimal("60"),
        evaluation_time=now,
    )

    assert result.active is False
    assert result.reason_code is None
    assert result.remaining_minutes == Decimal("0")


def test_validate_strategy_asset_cooldown_rejects_invalid_timestamps() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    result = validate_strategy_asset_cooldown(
        consecutive_losses_on_pair=3,
        cooldown_after_losses=3,
        last_loss_at=now + timedelta(minutes=1),
        cooldown_duration_minutes=Decimal("60"),
        evaluation_time=now,
    )

    assert result.active is False
    assert result.reason_code == "invalid_cooldown_timestamps"


def test_validate_no_trade_zone_flags_data_quality_gaps() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    result = validate_no_trade_zone(
        evaluation_time=now,
        session_open_time=None,
        session_close_time=None,
        no_trade_open_minutes=None,
        no_trade_close_minutes=None,
        data_is_stale=False,
        data_has_gaps=True,
    )

    assert result.in_zone is True
    assert result.reason_code == "asset_in_no_trade_zone_data_quality"


def test_validate_no_trade_zone_flags_time_window() -> None:
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    session_close = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)
    evaluation_time = session_open + timedelta(minutes=10)
    result = validate_no_trade_zone(
        evaluation_time=evaluation_time,
        session_open_time=session_open,
        session_close_time=session_close,
        no_trade_open_minutes=Decimal("15"),
        no_trade_close_minutes=Decimal("10"),
        data_is_stale=False,
        data_has_gaps=False,
    )

    assert result.in_zone is True
    assert result.reason_code == "asset_in_no_trade_zone_time_window"


def test_validate_no_trade_zone_rejects_invalid_session_window() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    result = validate_no_trade_zone(
        evaluation_time=now,
        session_open_time=now,
        session_close_time=now,
        no_trade_open_minutes=Decimal("15"),
        no_trade_close_minutes=Decimal("10"),
        data_is_stale=False,
        data_has_gaps=False,
    )

    assert result.in_zone is False
    assert result.reason_code == "invalid_session_window"


def test_evaluate_signal_risk_rejects_when_no_trade_zone_is_active() -> None:
    session_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
    session_close = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)
    request = RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.5"),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("100"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("110"),
        max_drawdown_pct=Decimal("0.15"),
        evaluation_time=session_open + timedelta(minutes=5),
        session_open_time=session_open,
        session_close_time=session_close,
        no_trade_open_minutes=Decimal("10"),
        no_trade_close_minutes=Decimal("10"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "asset_in_no_trade_zone"


def test_evaluate_signal_risk_rejects_when_cooldown_is_active() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    request = RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.5"),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("100"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("110"),
        max_drawdown_pct=Decimal("0.15"),
        evaluation_time=now,
        consecutive_losses_on_pair=3,
        cooldown_after_losses=3,
        last_loss_at=now - timedelta(minutes=15),
        cooldown_duration_minutes=Decimal("60"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "strategy_asset_cooldown_active"


def test_evaluate_signal_risk_rejects_invalid_cooldown_configuration() -> None:
    now = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    request = RiskEvaluationRequest(
        signal_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.5"),
        account_equity=Decimal("100"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        start_of_day_equity=Decimal("100"),
        current_equity=Decimal("100"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("110"),
        max_drawdown_pct=Decimal("0.15"),
        evaluation_time=now,
        consecutive_losses_on_pair=3,
        cooldown_after_losses=0,
        last_loss_at=now - timedelta(minutes=15),
        cooldown_duration_minutes=Decimal("60"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "invalid_cooldown_after_losses"


def test_validate_kill_switch_state_fails_closed_on_unknown_state() -> None:
    result = validate_kill_switch_state(
        scope="global",
        engaged_state=None,
        rearm_required=False,
        rearmed_by_human=False,
    )

    assert result.block_trading is True
    assert result.reason_code == "global_kill_switch_state_unknown"


def test_validate_kill_switch_state_requires_manual_rearm() -> None:
    result = validate_kill_switch_state(
        scope="account",
        engaged_state=False,
        rearm_required=True,
        rearmed_by_human=False,
    )

    assert result.block_trading is True
    assert result.reason_code == "account_kill_switch_requires_manual_rearm"


def test_validate_kill_switch_state_passes_after_human_rearm() -> None:
    result = validate_kill_switch_state(
        scope="global",
        engaged_state=False,
        rearm_required=True,
        rearmed_by_human=True,
    )

    assert result.block_trading is False
    assert result.reason_code is None


def test_apply_manual_kill_switch_rearm_requires_human_actor() -> None:
    result = apply_manual_kill_switch_rearm(
        engaged=True,
        rearm_required=True,
        actor_is_human=False,
    )

    assert result.state_changed is False
    assert result.engaged is True
    assert result.rearm_required is True
    assert result.reason_code == "manual_rearm_requires_human_actor"


def test_apply_manual_kill_switch_rearm_clears_state_for_human_actor() -> None:
    result = apply_manual_kill_switch_rearm(
        engaged=True,
        rearm_required=True,
        actor_is_human=True,
    )

    assert result.state_changed is True
    assert result.engaged is False
    assert result.rearm_required is False
    assert result.rearmed_by_human is True
    assert result.reason_code == "kill_switch_manual_rearm_completed"


def test_evaluate_signal_risk_rejects_when_global_kill_switch_requires_manual_rearm() -> None:
    request = _request()
    request = RiskEvaluationRequest(
        signal_id=request.signal_id,
        paper_account_id=request.paper_account_id,
        asset_id=request.asset_id,
        side=request.side,
        quantity=request.quantity,
        account_equity=request.account_equity,
        max_position_size_pct=request.max_position_size_pct,
        min_order_notional=request.min_order_notional,
        qty_step_size=request.qty_step_size,
        supports_fractional=request.supports_fractional,
        start_of_day_equity=request.start_of_day_equity,
        current_equity=request.current_equity,
        max_daily_loss_pct=request.max_daily_loss_pct,
        high_water_mark_equity=request.high_water_mark_equity,
        max_drawdown_pct=request.max_drawdown_pct,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=True,
        global_kill_switch_rearmed_by_human=False,
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "global_kill_switch_requires_manual_rearm"
    assert len(result.steps) == 1
    assert result.steps[0].step == "global_kill_switch"


def test_evaluate_signal_risk_rejects_when_account_kill_switch_engaged() -> None:
    request = _request()
    request = RiskEvaluationRequest(
        signal_id=request.signal_id,
        paper_account_id=request.paper_account_id,
        asset_id=request.asset_id,
        side=request.side,
        quantity=request.quantity,
        account_equity=request.account_equity,
        max_position_size_pct=request.max_position_size_pct,
        min_order_notional=request.min_order_notional,
        qty_step_size=request.qty_step_size,
        supports_fractional=request.supports_fractional,
        start_of_day_equity=request.start_of_day_equity,
        current_equity=request.current_equity,
        max_daily_loss_pct=request.max_daily_loss_pct,
        high_water_mark_equity=request.high_water_mark_equity,
        max_drawdown_pct=request.max_drawdown_pct,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        global_kill_switch_rearmed_by_human=False,
        account_kill_switch_engaged_state=True,
        account_kill_switch_rearm_required=True,
        account_kill_switch_rearmed_by_human=False,
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("10"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "account_kill_switch_engaged"