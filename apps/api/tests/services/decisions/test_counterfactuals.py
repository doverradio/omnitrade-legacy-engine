from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.decision_counterfactual_result import (
    DecisionCounterfactualResult,
    _prevent_decision_counterfactual_result_delete,
    _prevent_decision_counterfactual_result_update,
)
from app.models.decision_record import DecisionRecord
from app.services.decisions.counterfactuals import (
    V1_COUNTERFACTUAL_HORIZONS,
    build_counterfactual_result_draft,
    build_counterfactual_result_idempotency_key,
)


def _decision_record(
    *,
    action: str = "buy",
    confidence: Decimal | None = Decimal("0.90"),
    rejected_reason: str | None = None,
    regime_tag: str | None = "trend_up",
) -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": regime_tag},
        indicators={},
        generated_signals=[{"action": action, "status": "generated"}],
        signal_strength=Decimal("0.60"),
        confidence=confidence,
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=action != "hold",
        trade_rejected_reason=rejected_reason,
        execution_details=None,
        exit_details=None,
        pnl=None,
        duration=None,
        outcome=None,
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )


def test_v1_horizons_are_bounded_to_15m_1h_24h() -> None:
    assert V1_COUNTERFACTUAL_HORIZONS == (("15m", 15), ("1h", 60), ("24h", 1440))


def test_shadow_outcomes_cover_buy_sell_wait_with_best_action_selection() -> None:
    decision = _decision_record(action="hold", confidence=Decimal("0.30"))

    draft = build_counterfactual_result_draft(
        decision_record=decision,
        asset_symbol="BTCUSDT",
        actual_action="wait",
        horizon_label="1h",
        horizon_minutes=60,
        evaluated_at=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        entry_price=Decimal("100"),
        horizon_price=Decimal("120"),
    )

    assert draft.evaluation_state == "resolved"
    assert draft.shadow_buy_return_pct == Decimal("0.2")
    assert draft.shadow_sell_return_pct == Decimal("-0.2")
    assert draft.shadow_wait_return_pct == Decimal("0")
    assert draft.best_action == "buy"
    assert draft.actual_action_correct is False
    assert any(item["tag"] == "missed_breakout" for item in draft.lesson_tags)


def test_unavailable_state_when_market_prices_are_missing() -> None:
    decision = _decision_record(action="sell")

    draft = build_counterfactual_result_draft(
        decision_record=decision,
        asset_symbol="BTCUSDT",
        actual_action="sell",
        horizon_label="15m",
        horizon_minutes=15,
        evaluated_at=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        entry_price=None,
        horizon_price=Decimal("98"),
    )

    assert draft.evaluation_state == "unavailable"
    assert draft.state_reason == "missing_entry_price"
    assert draft.best_action is None
    assert draft.actual_action_correct is None
    assert any(item["tag"] == "counterfactual_data_unavailable" for item in draft.lesson_tags)


def test_lesson_tags_include_wait_and_confidence_signals() -> None:
    decision = _decision_record(
        action="hold",
        confidence=Decimal("0.95"),
        rejected_reason="volatility_limit_triggered",
    )

    draft = build_counterfactual_result_draft(
        decision_record=decision,
        asset_symbol="BTCUSDT",
        actual_action="wait",
        horizon_label="24h",
        horizon_minutes=1440,
        evaluated_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
        entry_price=Decimal("100"),
        horizon_price=Decimal("100"),
    )

    tags = {item["tag"] for item in draft.lesson_tags}

    assert draft.actual_action_correct is True
    assert "wait_was_correct" in tags
    assert "volatility_filter_saved_trade" in tags


def test_idempotency_key_is_stable_for_decision_and_horizon() -> None:
    decision_id = uuid.uuid4()

    key_a = build_counterfactual_result_idempotency_key(decision_id=decision_id, horizon_minutes=60)
    key_b = build_counterfactual_result_idempotency_key(decision_id=decision_id, horizon_minutes=60)
    key_c = build_counterfactual_result_idempotency_key(decision_id=decision_id, horizon_minutes=15)

    assert key_a == key_b
    assert key_a != key_c


def test_append_only_behavior_for_counterfactual_records() -> None:
    record = DecisionCounterfactualResult(
        decision_id=uuid.uuid4(),
        idempotency_key="k",
        horizon_label="15m",
        horizon_minutes=15,
        decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        evaluated_at=datetime(2026, 7, 6, 0, 15, tzinfo=timezone.utc),
        asset_symbol="BTCUSDT",
        actual_action="buy",
        shadow_buy_return_pct=Decimal("0.01"),
        shadow_sell_return_pct=Decimal("-0.01"),
        shadow_wait_return_pct=Decimal("0"),
        best_action="buy",
        actual_action_correct=True,
        evaluation_state="resolved",
        state_reason=None,
        lesson_tags=[{"tag": "counterfactual_neutral", "reason": "baseline"}],
        feature_snapshot={"asset_symbol": "BTCUSDT"},
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_counterfactual_result_update(None, None, record)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_counterfactual_result_delete(None, None, record)
