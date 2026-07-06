from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_experiment_recommendation import (
    DecisionExperimentRecommendation,
    _prevent_decision_experiment_recommendation_delete,
    _prevent_decision_experiment_recommendation_update,
)
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.services.decisions.recommendations import (
    build_experiment_recommendation_draft,
    generate_experiment_recommendations_v1,
    read_experiment_recommendations,
)


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        *,
        decision_records: list[DecisionRecord],
        quality_scores: list[DecisionQualityScore],
        counterfactual_records: list[DecisionCounterfactualResult],
    ) -> None:
        self.decision_records = decision_records
        self.quality_scores = quality_scores
        self.counterfactual_records = counterfactual_records
        self.recommendations: list[DecisionExperimentRecommendation] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_quality_scores" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.quality_scores if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return rows[0] if rows else None

        if "FROM decision_experiment_recommendations" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.recommendations:
                if item.idempotency_key == key:
                    return item.id
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql:
            return _ExecuteResult(list(self.decision_records))

        if "FROM decision_counterfactual_results" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.counterfactual_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.horizon_minutes, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_experiment_recommendations" in sql:
            rows = list(self.recommendations)
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionExperimentRecommendation):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(timezone.utc)
            self.recommendations.append(obj)


def _decision_record() -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
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
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.6"),
        confidence=Decimal("0.7"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=True,
        trade_rejected_reason=None,
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


def _quality_score(decision_id: uuid.UUID, *, score: str = "0.72") -> DecisionQualityScore:
    return DecisionQualityScore(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        scoring_model_version="dqe_v1",
        composite_score=Decimal(score),
        component_scores=[],
        weight_profile={},
        provenance={},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _counterfactual(decision_id: uuid.UUID, *, state: str = "resolved", correct: bool = False) -> DecisionCounterfactualResult:
    return DecisionCounterfactualResult(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        horizon_label="15m",
        horizon_minutes=15,
        decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        evaluated_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        asset_symbol="BTCUSDT",
        actual_action="buy",
        shadow_buy_return_pct=Decimal("0.01"),
        shadow_sell_return_pct=Decimal("-0.01"),
        shadow_wait_return_pct=Decimal("0"),
        best_action="sell" if not correct else "buy",
        actual_action_correct=correct,
        evaluation_state=state,
        state_reason=None,
        lesson_tags=[{"tag": "missed_breakout", "reason": "test"}],
        feature_snapshot={},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def test_deterministic_recommendation_generation_and_provenance() -> None:
    decision = _decision_record()
    quality = _quality_score(decision.decision_id)
    counterfactual = _counterfactual(decision.decision_id)

    draft_a = build_experiment_recommendation_draft(
        decision_record=decision,
        quality_score=quality,
        counterfactual_results=[counterfactual],
    )
    draft_b = build_experiment_recommendation_draft(
        decision_record=decision,
        quality_score=quality,
        counterfactual_results=[counterfactual],
    )

    assert draft_a.recommendation_type == draft_b.recommendation_type
    assert draft_a.confidence_level == draft_b.confidence_level
    assert draft_a.provenance == draft_b.provenance
    assert draft_a.provenance["source_ids"]["decision_records"] == [str(decision.decision_id)]


def test_evidence_linkage_and_unknown_unavailable_states() -> None:
    decision = _decision_record()

    unavailable = build_experiment_recommendation_draft(
        decision_record=decision,
        quality_score=None,
        counterfactual_results=[],
    )
    unknown = build_experiment_recommendation_draft(
        decision_record=decision,
        quality_score=_quality_score(decision.decision_id),
        counterfactual_results=[_counterfactual(decision.decision_id, state="unknown")],
    )

    assert unavailable.evidence_state == "unavailable"
    assert unavailable.state_reason is not None
    assert unknown.evidence_state == "unknown"
    assert any(ref["source"] == "decision_counterfactual_results" for ref in unknown.supporting_evidence_refs)


def test_confidence_handling_and_advisory_only_model_guards() -> None:
    decision = _decision_record()
    high_quality = _quality_score(decision.decision_id, score="0.90")
    good_cf = _counterfactual(decision.decision_id, correct=True)

    draft = build_experiment_recommendation_draft(
        decision_record=decision,
        quality_score=high_quality,
        counterfactual_results=[good_cf],
    )

    assert draft.confidence_level in {"medium", "high"}

    rec = DecisionExperimentRecommendation(
        idempotency_key="k",
        recommendation_engine_version="recommendation_v1",
        recommendation_type="experiment_run",
        recommendation_category="experiment",
        confidence_level="medium",
        expected_impact_level="medium",
        required_human_review_level="standard",
        supporting_evidence_refs=[],
        originating_decision_ids=[str(decision.decision_id)],
        explanation="advisory",
        suggested_experiment={"name": "test"},
        evidence_state="known",
        state_reason=None,
        provenance={},
        advisory_only=True,
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_experiment_recommendation_update(None, None, rec)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_experiment_recommendation_delete(None, None, rec)


@pytest.mark.asyncio
async def test_append_only_recommendation_persistence_is_idempotent() -> None:
    decision = _decision_record()
    session = _FakeSession(
        decision_records=[decision],
        quality_scores=[_quality_score(decision.decision_id)],
        counterfactual_records=[_counterfactual(decision.decision_id)],
    )

    first = await generate_experiment_recommendations_v1(db=session, decision_ids=[decision.decision_id])
    second = await generate_experiment_recommendations_v1(db=session, decision_ids=[decision.decision_id])
    read_rows = await read_experiment_recommendations(db=session)

    assert first.inserted_recommendations == 1
    assert second.inserted_recommendations == 0
    assert len(session.recommendations) == 1
    assert len(read_rows) == 1
    assert read_rows[0].advisory_only is True
