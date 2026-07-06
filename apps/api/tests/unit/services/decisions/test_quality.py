from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import (
    DecisionQualityScore,
    _prevent_decision_quality_score_delete,
    _prevent_decision_quality_score_update,
)
from app.models.decision_record import DecisionRecord
from app.services.decisions.quality import (
    DEFAULT_COMPONENT_WEIGHTS,
    build_decision_quality_idempotency_key,
    build_decision_quality_score_draft,
)


def _decision_record(*, pnl_pct: str | None = "0.03") -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    pnl = {"pct": pnl_pct} if pnl_pct is not None else None
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": "buy", "status": "executed"}],
        signal_strength=Decimal("0.60"),
        confidence=Decimal("0.70"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[{"action_taken": "resized"}],
        expected_risk=None,
        expected_reward=None,
        position_size=Decimal("0.01"),
        trade_accepted=True,
        trade_rejected_reason=None,
        execution_details={"trade_id": str(uuid.uuid4())},
        exit_details=None,
        pnl=pnl,
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


def _explainability_records(decision_id: uuid.UUID) -> list[DecisionExplainabilityRecord]:
    roles = ["supporting", "opposing", "confidence_factor", "risk_adjustment"]
    return [
        DecisionExplainabilityRecord(
            id=uuid.uuid4(),
            decision_id=decision_id,
            idempotency_key=f"k-{role}",
            evidence_role=role,
            evidence_name=f"{role}_evidence",
            evidence_payload={},
            provenance={},
            availability_state="known",
            state_reason=None,
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
        for role in roles
    ]


def _counterfactual_records(decision_id: uuid.UUID) -> list[DecisionCounterfactualResult]:
    rows: list[DecisionCounterfactualResult] = []
    for horizon in (15, 60, 1440):
        rows.append(
            DecisionCounterfactualResult(
                id=uuid.uuid4(),
                decision_id=decision_id,
                idempotency_key=f"c-{horizon}",
                horizon_label="15m" if horizon == 15 else "1h" if horizon == 60 else "24h",
                horizon_minutes=horizon,
                decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
                evaluated_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
                asset_symbol="BTCUSDT",
                actual_action="buy",
                shadow_buy_return_pct=Decimal("0.02"),
                shadow_sell_return_pct=Decimal("-0.02"),
                shadow_wait_return_pct=Decimal("0"),
                best_action="buy",
                actual_action_correct=True,
                evaluation_state="resolved",
                state_reason=None,
                lesson_tags=[{"tag": "counterfactual_neutral", "reason": "stable"}],
                feature_snapshot={},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            )
        )
    return rows


def test_deterministic_scoring_and_repeatable_calculations() -> None:
    decision = _decision_record()
    explainability = _explainability_records(decision.decision_id)
    counterfactuals = _counterfactual_records(decision.decision_id)

    draft_a = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability,
        counterfactual_records=counterfactuals,
    )
    draft_b = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability,
        counterfactual_records=counterfactuals,
    )

    assert draft_a.composite_score == draft_b.composite_score
    assert [c.name for c in draft_a.component_scores] == [c.name for c in draft_b.component_scores]
    assert [c.score for c in draft_a.component_scores] == [c.score for c in draft_b.component_scores]


def test_component_weighting_changes_composite_score_deterministically() -> None:
    decision = _decision_record(pnl_pct="0.08")
    explainability = _explainability_records(decision.decision_id)
    counterfactuals = _counterfactual_records(decision.decision_id)

    default_draft = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability,
        counterfactual_records=counterfactuals,
    )

    weighted = dict(DEFAULT_COMPONENT_WEIGHTS)
    weighted["profit_contribution"] = Decimal("0.30")
    weighted["counterfactual_outcome_quality"] = Decimal("0.05")

    adjusted_draft = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability,
        counterfactual_records=counterfactuals,
        component_weights=weighted,
    )

    assert adjusted_draft.composite_score != default_draft.composite_score
    assert adjusted_draft.weight_profile["profit_contribution"] > default_draft.weight_profile["profit_contribution"]


def test_idempotency_key_preserves_provenance_and_is_repeatable() -> None:
    decision = _decision_record()
    explainability = _explainability_records(decision.decision_id)
    counterfactuals = _counterfactual_records(decision.decision_id)

    draft = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability,
        counterfactual_records=counterfactuals,
    )

    key_a = build_decision_quality_idempotency_key(
        decision_id=decision.decision_id,
        scoring_model_version=draft.scoring_model_version,
        component_scores=draft.component_scores,
        weight_profile=draft.weight_profile,
        provenance=draft.provenance,
    )
    key_b = build_decision_quality_idempotency_key(
        decision_id=decision.decision_id,
        scoring_model_version=draft.scoring_model_version,
        component_scores=draft.component_scores,
        weight_profile=draft.weight_profile,
        provenance=draft.provenance,
    )

    assert key_a == key_b
    assert draft.provenance["source_ids"]["decision_record"] == str(decision.decision_id)
    assert len(draft.provenance["source_ids"]["counterfactual_results"]) == 3


def test_append_only_behavior_for_quality_scores() -> None:
    score = DecisionQualityScore(
        decision_id=uuid.uuid4(),
        idempotency_key="k",
        scoring_model_version="dqe_v1",
        composite_score=Decimal("0.85"),
        component_scores=[],
        weight_profile={},
        provenance={},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_quality_score_update(None, None, score)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_quality_score_delete(None, None, score)
