from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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


# Regression for the second production incident: the first fix (sizing up
# to the venue minimum whenever *account_equity * max_position_size_pct*
# covered it) never activated in production, because the prior regression
# tests used unrealistic headroom (account_equity=1000, max_position_size_pct=1)
# that made the account-level cap irrelevant. The real production account
# was small (account_equity=23.7205, max_position_size_pct=0.10 -> a generic
# cap of only ~$2.37), well below both the $5 venue minimum and the
# campaign's own $5 authorization. compute_position_sizing had no way to
# know the campaign had already, separately, governed and authorized
# exactly $5 for this trade -- it only ever saw the generic account-wide
# heuristic. These tests use the exact production numbers from the VPS log.
_PRODUCTION_ACCOUNT_EQUITY = Decimal("23.7205")
_PRODUCTION_MAX_POSITION_SIZE_PCT = Decimal("0.10")  # 10% of equity, not 0.10%
_PRODUCTION_REFERENCE_PRICE = Decimal("65425.5")
_PRODUCTION_REQUESTED_QUANTITY = Decimal("0.00007642280150705764571917677358")
_PRODUCTION_MIN_ORDER_NOTIONAL = Decimal("5")


def test_compute_position_sizing_generic_account_cap_alone_still_rejects() -> None:
    """Demonstrates the exact production failure: account_equity * pct =
    ~$2.37, below the $5 venue minimum, and with no explicit campaign
    authorization supplied the generic cap alone is correctly *not* enough
    to justify sizing up -- confirming the true, current-production root
    cause is the generic percentage cap, not rounding, and that this
    (not campaign authority) is what must fail closed on its own."""
    sizing = compute_position_sizing(
        requested_quantity=_PRODUCTION_REQUESTED_QUANTITY,
        reference_price=_PRODUCTION_REFERENCE_PRICE,
        account_equity=_PRODUCTION_ACCOUNT_EQUITY,
        max_position_size_pct=_PRODUCTION_MAX_POSITION_SIZE_PCT,
        qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
        min_order_notional=_PRODUCTION_MIN_ORDER_NOTIONAL,
        campaign_authorized_notional=None,
    )

    # compute_position_sizing's ordinary resize-down still produces a
    # legitimate, non-zero quantity capped at the generic account authority
    # ($2.37) -- it is the *next* gate, validate_minimum_viable_order, that
    # correctly refuses it as unexecutable, and evaluate_signal_risk that
    # then reports the final approved_quantity as exactly 0 for a rejection
    # (see test_evaluate_signal_risk_without_campaign_authorization_still_rejects).
    assert sizing.max_position_notional == Decimal("2.37205")
    assert sizing.approved_notional == Decimal("2.371674375")
    assert sizing.approved_notional < _PRODUCTION_MIN_ORDER_NOTIONAL
    assert sizing.reason_code != "position_sized_up_to_minimum_viable_order"
    assert not validate_minimum_viable_order(
        approved_quantity=sizing.approved_quantity,
        reference_price=_PRODUCTION_REFERENCE_PRICE,
        min_order_notional=_PRODUCTION_MIN_ORDER_NOTIONAL,
        qty_step_size=Decimal("0.00000001"),
    )


def test_compute_position_sizing_campaign_authorization_rescues_exact_production_incident() -> None:
    """The fix: when the caller explicitly supplies the campaign's own,
    separately-governed authorization for this trade (authoritative.py's
    proposed_allocation -- exactly $5, matching maximum_position_size and
    maximum_total_exposure for this proving campaign), it is a valid
    additional basis -- alongside the generic account cap -- for clearing
    the venue's minimum. This reproduces the exact VPS log values and must
    resolve to an executable order."""
    sizing = compute_position_sizing(
        requested_quantity=_PRODUCTION_REQUESTED_QUANTITY,
        reference_price=_PRODUCTION_REFERENCE_PRICE,
        account_equity=_PRODUCTION_ACCOUNT_EQUITY,
        max_position_size_pct=_PRODUCTION_MAX_POSITION_SIZE_PCT,
        qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
        min_order_notional=_PRODUCTION_MIN_ORDER_NOTIONAL,
        campaign_authorized_notional=Decimal("5"),
    )

    assert sizing.approved_quantity > Decimal("0")
    assert sizing.approved_notional >= Decimal("5")
    assert sizing.reason_code == "position_sized_up_to_minimum_viable_order"
    # Never exceeds what the campaign explicitly authorized, beyond the one
    # quantity increment that is mechanically unavoidable when quantizing an
    # exact dollar target to a fixed tradable step.
    assert sizing.approved_notional <= Decimal("5") + (Decimal("0.00000001") * _PRODUCTION_REFERENCE_PRICE)


def test_compute_position_sizing_ignores_oversized_request_as_a_fake_authorization() -> None:
    """Safety guard: a request that is merely large -- and gets correctly
    clipped down by the generic account cap for that reason -- must never be
    mistaken for a deliberate authorization. Without an explicit
    campaign_authorized_notional, an oversized ask does not unlock the
    minimum-order rescue no matter how large it was."""
    sizing = compute_position_sizing(
        requested_quantity=Decimal("0.5"),  # $50 at this price -- far more than the account allows
        reference_price=Decimal("100"),
        account_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.05"),  # max_position_notional = 1.25
        qty_step_size=Decimal("0.0001"),
        supports_fractional=True,
        min_order_notional=Decimal("5"),
    )

    assert sizing.approved_notional < Decimal("5")
    assert sizing.reason_code != "position_sized_up_to_minimum_viable_order"


def test_compute_position_sizing_does_not_size_up_beyond_authorized_capital() -> None:
    """No silent auto-sizing beyond campaign authority: when even an
    explicitly-authorized ceiling is below the venue minimum, the order is
    correctly left short and rejected downstream -- never forced through."""
    sizing = compute_position_sizing(
        requested_quantity=Decimal("0.01"),
        reference_price=Decimal("100"),
        account_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.05"),  # max_position_notional = 1.25
        qty_step_size=Decimal("0.0001"),
        supports_fractional=True,
        min_order_notional=Decimal("5"),
        campaign_authorized_notional=Decimal("1"),  # explicitly authorized, but still below the $5 minimum
    )

    assert sizing.approved_notional < Decimal("5")
    assert sizing.reason_code != "position_sized_up_to_minimum_viable_order"

    assert not validate_minimum_viable_order(
        approved_quantity=sizing.approved_quantity,
        reference_price=Decimal("100"),
        min_order_notional=Decimal("5"),
        qty_step_size=Decimal("0.0001"),
    )


@pytest.mark.parametrize(
    ("provider_label", "requested_quantity", "reference_price", "qty_step_size", "supports_fractional", "min_order_notional", "account_equity", "max_position_size_pct"),
    [
        # Small accounts, realistic percentage caps -- not the unrealistic
        # account_equity=100000/max_position_size_pct=1 used previously,
        # which made the generic cap irrelevant and masked this exact bug.
        ("kraken_like_fractional_crypto", _PRODUCTION_REQUESTED_QUANTITY, _PRODUCTION_REFERENCE_PRICE, Decimal("0.00000001"), True, Decimal("5"), Decimal("23.7205"), Decimal("0.10")),
        ("coinbase_like_fractional_crypto", Decimal("0.00001526"), Decimal("65425.5"), Decimal("0.00000001"), True, Decimal("1"), Decimal("23.7205"), Decimal("0.10")),
        ("alpaca_like_whole_share_equity", Decimal("0.4"), Decimal("50"), None, False, Decimal("1"), Decimal("25"), Decimal("0.10")),
    ],
)
def test_compute_position_sizing_honors_provider_specific_minimums(
    provider_label: str,
    requested_quantity: Decimal,
    reference_price: Decimal,
    qty_step_size: Decimal | None,
    supports_fractional: bool,
    min_order_notional: Decimal,
    account_equity: Decimal,
    max_position_size_pct: Decimal,
) -> None:
    """The same, provider-agnostic sizing function must honor whatever
    minimum a given provider profile supplies -- proving multiple providers
    behave consistently through one shared mechanism, with no
    provider-specific branching -- once the campaign's own governed
    authorization (matching requested_quantity's implied notional) is
    supplied explicitly, exactly as authoritative.py does."""
    campaign_authorized_notional = requested_quantity * reference_price
    sizing = compute_position_sizing(
        requested_quantity=requested_quantity,
        reference_price=reference_price,
        account_equity=account_equity,
        max_position_size_pct=max_position_size_pct,
        qty_step_size=qty_step_size,
        supports_fractional=supports_fractional,
        min_order_notional=min_order_notional,
        campaign_authorized_notional=campaign_authorized_notional,
    )

    assert sizing.approved_notional >= min_order_notional, provider_label


def test_evaluate_signal_risk_sizes_up_to_minimum_and_approves() -> None:
    """End-to-end through evaluate_signal_risk (not just the sizing helper),
    using the exact production values: an executable order is produced and
    approved, not rejected, once the campaign's authorization is passed
    through RiskEvaluationRequest.campaign_authorized_notional."""
    result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            side="buy",
            quantity=_PRODUCTION_REQUESTED_QUANTITY,
            account_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_position_size_pct=_PRODUCTION_MAX_POSITION_SIZE_PCT,
            min_order_notional=_PRODUCTION_MIN_ORDER_NOTIONAL,
            campaign_authorized_notional=Decimal("5"),
            qty_step_size=Decimal("0.00000001"),
            supports_fractional=True,
            start_of_day_equity=_PRODUCTION_ACCOUNT_EQUITY,
            current_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_daily_loss_pct=Decimal("0.03"),
            high_water_mark_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_drawdown_pct=Decimal("0.15"),
        ),
        reference_price=_PRODUCTION_REFERENCE_PRICE,
    )

    assert result.action == RiskDecisionAction.RESIZE
    assert result.reason_code == "position_sized_up_to_minimum_viable_order"
    assert result.approved_quantity * _PRODUCTION_REFERENCE_PRICE >= Decimal("5")


def test_evaluate_signal_risk_without_campaign_authorization_still_rejects() -> None:
    """Same production numbers, but without an explicit campaign
    authorization (e.g. a non-campaign caller): the generic account cap
    alone is not enough, and the order is correctly rejected -- proving the
    rescue is opt-in per caller, not a blanket behavior change."""
    result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            side="buy",
            quantity=_PRODUCTION_REQUESTED_QUANTITY,
            account_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_position_size_pct=_PRODUCTION_MAX_POSITION_SIZE_PCT,
            min_order_notional=_PRODUCTION_MIN_ORDER_NOTIONAL,
            qty_step_size=Decimal("0.00000001"),
            supports_fractional=True,
            start_of_day_equity=_PRODUCTION_ACCOUNT_EQUITY,
            current_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_daily_loss_pct=Decimal("0.03"),
            high_water_mark_equity=_PRODUCTION_ACCOUNT_EQUITY,
            max_drawdown_pct=Decimal("0.15"),
        ),
        reference_price=_PRODUCTION_REFERENCE_PRICE,
    )

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code == "position_below_minimum_order_size"
    assert result.approved_quantity == Decimal("0")


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