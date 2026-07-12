from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_alternative_action import DecisionAlternativeAction
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.services.decisions.package import DECISION_PACKAGE_SCHEMA_VERSION, DecisionPackageBuilder
from app.services.strategies.identity import build_strategy_identity


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


class _FakeSession:
    def __init__(
        self,
        *,
        decision_records: list[DecisionRecord],
        decision_snapshots: list[DecisionSnapshot],
        explainability_records: list[DecisionExplainabilityRecord],
        quality_scores: list[DecisionQualityScore],
        counterfactual_results: list[DecisionCounterfactualResult],
        alternative_actions: list[DecisionAlternativeAction],
    ) -> None:
        self.decision_records = decision_records
        self.decision_snapshots = decision_snapshots
        self.explainability_records = explainability_records
        self.quality_scores = quality_scores
        self.counterfactual_results = counterfactual_results
        self.alternative_actions = alternative_actions

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql:
            decision_id = params.get("decision_id_1")
            for item in self.decision_records:
                if item.decision_id == decision_id:
                    return item
            return None

        if "FROM decision_snapshots" in sql:
            decision_id = params.get("decision_id_1")
            for item in self.decision_snapshots:
                if item.decision_id == decision_id:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_explainability_records" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.explainability_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_quality_scores" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.quality_scores if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_counterfactual_results" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.counterfactual_results if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.horizon_minutes, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_alternative_actions" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.alternative_actions if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows)

        return _ExecuteResult([])


def _decision_record(*, decision_id: uuid.UUID | None = None) -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    row_id = decision_id or uuid.uuid4()
    return DecisionRecord(
        decision_id=row_id,
        idempotency_key=str(uuid.uuid4()),
        source_lineage={
            "signals": [str(uuid.uuid4())],
            "model_outputs": [str(uuid.uuid4())],
            "risk_events": [str(uuid.uuid4())],
            "trades": [str(uuid.uuid4())],
        },
        field_provenance={"confidence": [{"source": "signal.ai_confidence"}]},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={"rsi": 42.0},
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.60"),
        confidence=Decimal("0.70"),
        supporting_strategies=[{"model_name": "signal_scorer"}],
        opposing_strategies=[{"model_name": "volatility_guard"}],
        risk_adjustments=[{"action_taken": "resized"}],
        expected_risk={"value": "0.03"},
        expected_reward={"value": "0.06"},
        position_size=Decimal("0.01"),
        trade_accepted=True,
        trade_rejected_reason=None,
        execution_details={"paper_account_id": str(uuid.uuid4())},
        exit_details=None,
        pnl={"pct": "0.08"},
        duration="1h",
        outcome="win",
        post_trade_notes={"summary": "solid setup"},
        lessons_learned=[{"lesson": "follow trend"}],
        ai_reflection={"note": "hindsight"},
        future_tags=["trend_up"],
        confidence_calibration={"evaluation_state": "pending_outcome"},
        review_status="unreviewed",
        human_notes=None,
    )


def _decision_snapshot(decision_id: uuid.UUID) -> DecisionSnapshot:
    return DecisionSnapshot(
        decision_id=decision_id,
        timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        exchange="binance",
        timeframe="1m",
        ohlcv_context=[{"open": "1", "high": "2", "low": "0.5", "close": "1.5"}],
        indicators={"rsi": 42.0},
        generated_features={"volatility": "low"},
        market_regime={"regime_tag": "trend_up"},
        volatility={"atr": "12.5"},
        spread_liquidity_context=None,
        strategy_inputs={"strategy_id": str(uuid.uuid4()), "selected_strategy_identity": build_strategy_identity(slug="ma_crossover", module_version="1.0.0")},
        risk_inputs={"max_position": "0.02"},
        current_position_state=None,
        open_trades=[],
        portfolio_exposure={"equity": "10000"},
        parameter_set_version="ps_v1",
        strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
        ai_model_version="ai_v1",
        decision_engine_version="v1",
        configuration_version="risk_v1",
    )


def _explainability(decision_id: uuid.UUID) -> DecisionExplainabilityRecord:
    return DecisionExplainabilityRecord(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        evidence_role="supporting",
        evidence_name="signal_scorer",
        evidence_payload={"score": "0.71"},
        provenance={"source_refs": ["decision_record.supporting_strategies"]},
        availability_state="known",
        state_reason=None,
        created_at=datetime(2026, 7, 6, 0, 0, 1, tzinfo=timezone.utc),
    )


def _quality(decision_id: uuid.UUID) -> DecisionQualityScore:
    return DecisionQualityScore(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        scoring_model_version="dqe_v1",
        composite_score=Decimal("0.82"),
        component_scores=[{"name": "risk_discipline", "score": "1.0"}],
        weight_profile={"risk_discipline": "0.16"},
        provenance={"source_ids": {"decision_record": str(decision_id)}},
        created_at=datetime(2026, 7, 6, 0, 0, 2, tzinfo=timezone.utc),
    )


def _counterfactual(decision_id: uuid.UUID) -> DecisionCounterfactualResult:
    return DecisionCounterfactualResult(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        horizon_label="15m",
        horizon_minutes=15,
        decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        evaluated_at=datetime(2026, 7, 6, 0, 15, tzinfo=timezone.utc),
        asset_symbol="BTCUSDT",
        actual_action="buy",
        shadow_buy_return_pct=Decimal("0.02"),
        shadow_sell_return_pct=Decimal("-0.01"),
        shadow_wait_return_pct=Decimal("0.00"),
        best_action="buy",
        actual_action_correct=True,
        evaluation_state="resolved",
        state_reason=None,
        lesson_tags=[{"tag": "counterfactual_neutral", "reason": "stable"}],
        feature_snapshot={"regime_tag": "trend_up"},
        created_at=datetime(2026, 7, 6, 0, 0, 3, tzinfo=timezone.utc),
    )


def _alternative(decision_id: uuid.UUID) -> DecisionAlternativeAction:
    return DecisionAlternativeAction(
        id=uuid.uuid4(),
        decision_id=decision_id,
        idempotency_key=str(uuid.uuid4()),
        chosen_action="buy",
        alternative_action="wait",
        reference_horizon_minutes=15,
        comparison_payload={"buy": "0.02", "wait": "0.00"},
        provenance={"source_refs": ["decision_record.generated_signals"]},
        availability_state="known",
        state_reason=None,
        created_at=datetime(2026, 7, 6, 0, 0, 4, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_builder_returns_versioned_deterministic_package() -> None:
    decision = _decision_record()
    session = _FakeSession(
        decision_records=[decision],
        decision_snapshots=[_decision_snapshot(decision.decision_id)],
        explainability_records=[_explainability(decision.decision_id)],
        quality_scores=[_quality(decision.decision_id)],
        counterfactual_results=[_counterfactual(decision.decision_id)],
        alternative_actions=[_alternative(decision.decision_id)],
    )

    builder = DecisionPackageBuilder()
    package_a = await builder.build_decision_package(db=session, decision_id=decision.decision_id)
    package_b = await builder.build_decision_package(db=session, decision_id=decision.decision_id)

    assert package_a is not None
    assert package_b is not None
    assert package_a.schema_version == DECISION_PACKAGE_SCHEMA_VERSION
    assert package_a.content_hash == package_b.content_hash
    assert package_a.decision_id == decision.decision_id
    assert package_a.decision_snapshot is not None
    assert package_a.availability_state.decision_snapshot == "known"
    assert package_a.availability_state.explainability_records == "known"
    assert package_a.availability_state.quality_scores == "known"
    assert package_a.availability_state.counterfactual_results == "known"
    assert package_a.availability_state.alternative_actions == "known"
    assert package_a.explainability_records[0].evidence_name == "signal_scorer"
    assert package_a.counterfactual_results[0].horizon_minutes == 15


@pytest.mark.asyncio
async def test_builder_handles_missing_optional_sections_gracefully() -> None:
    decision = _decision_record()
    session = _FakeSession(
        decision_records=[decision],
        decision_snapshots=[],
        explainability_records=[],
        quality_scores=[],
        counterfactual_results=[],
        alternative_actions=[],
    )

    builder = DecisionPackageBuilder()
    package = await builder.build_decision_package(db=session, decision_id=decision.decision_id)

    assert package is not None
    assert package.decision_snapshot is None
    assert package.availability_state.decision_snapshot == "unavailable"
    assert package.explainability_records == []
    assert package.quality_scores == []
    assert package.counterfactual_results == []
    assert package.alternative_actions == []


@pytest.mark.asyncio
async def test_builder_returns_none_when_decision_record_is_missing() -> None:
    session = _FakeSession(
        decision_records=[],
        decision_snapshots=[],
        explainability_records=[],
        quality_scores=[],
        counterfactual_results=[],
        alternative_actions=[],
    )

    builder = DecisionPackageBuilder()
    package = await builder.build_decision_package(db=session, decision_id=uuid.uuid4())

    assert package is None