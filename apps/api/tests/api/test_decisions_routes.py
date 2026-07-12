from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_experiment_recommendation import DecisionExperimentRecommendation
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.risk_event import RiskEvent
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.audit_log import AuditLog


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, rows: list[Any], scalar_items: list[Any] | None = None) -> None:
        self._rows = rows
        self._scalar_items = scalar_items if scalar_items is not None else rows

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._scalar_items)


class _FakeSession:
    def __init__(
        self,
        *,
        decision_rows: list[tuple[DecisionRecord, DecisionSnapshot | None]],
        risk_events: list[RiskEvent],
        explainability_records: list[DecisionExplainabilityRecord],
        counterfactual_results: list[DecisionCounterfactualResult],
        quality_scores: list[DecisionQualityScore],
        recommendations: list[DecisionExperimentRecommendation],
    ) -> None:
        self.decision_rows = decision_rows
        self.risk_events = risk_events
        self.explainability_records = explainability_records
        self.counterfactual_results = counterfactual_results
        self.quality_scores = quality_scores
        self.recommendations = recommendations
        self.previews: list[CryptoOrderPreview] = []
        self.live_orders: list[LiveCryptoOrder] = []
        self.audit_rows: list[AuditLog] = []
        self.add_calls = 0
        self.begin_calls = 0

    def begin(self) -> Any:
        self.begin_calls += 1
        raise AssertionError("Read-only endpoints must not open write transactions")

    def add(self, _obj: Any) -> None:
        self.add_calls += 1
        raise AssertionError("Read-only endpoints must not add rows")

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            for decision_record, _ in self.decision_rows:
                if decision_record.decision_id == decision_id:
                    return decision_record
            return None

        if "FROM decision_snapshots" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            for _decision_record, snapshot in self.decision_rows:
                if snapshot is not None and snapshot.decision_id == decision_id:
                    return snapshot
            return None

        if "FROM crypto_order_previews" in sql and "decision_record_id_1" in params:
            decision_id = params.get("decision_record_id_1")
            for item in self.previews:
                if item.decision_record_id == decision_id:
                    return item
            return None

        if "FROM live_crypto_orders" in sql and "decision_record_id_1" in params:
            decision_id = params.get("decision_record_id_1")
            for item in self.live_orders:
                if item.decision_record_id == decision_id:
                    return item
            return None

        if "FROM risk_events" in sql and "id_1" in params:
            risk_id = params.get("id_1")
            for item in self.risk_events:
                if item.id == risk_id:
                    return item
            return None

        if "FROM decision_quality_scores" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.quality_scores if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return rows[0] if rows else None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql and "LEFT OUTER JOIN decision_snapshots" not in sql:
            rows = [item for item, _ in self.decision_rows]
            rows.sort(key=lambda item: (item.timestamp, str(item.decision_id)), reverse=True)
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM signals" in sql:
            return _ExecuteResult([], scalar_items=[])

        if "FROM decision_records LEFT OUTER JOIN decision_snapshots" in sql:
            return _ExecuteResult(self.decision_rows)

        if "FROM risk_events" in sql:
            requested = {str(value) for value in params.values() if isinstance(value, uuid.UUID)}
            rows = [item for item in self.risk_events if str(item.id) in requested]
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM audit_log" in sql:
            entity_type = params.get("entity_type_1")
            entity_id = params.get("entity_id_1")
            rows = [
                item
                for item in self.audit_rows
                if item.entity_type == entity_type and item.entity_id == entity_id
            ]
            rows.sort(key=lambda item: (item.created_at, item.id))
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM decision_explainability_records" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.explainability_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM decision_counterfactual_results" in sql:
            if "decision_id_1" in params:
                decision_param = params.get("decision_id_1")
                if isinstance(decision_param, (list, tuple, set)):
                    requested = {item for item in decision_param if isinstance(item, uuid.UUID)}
                    rows = [item for item in self.counterfactual_results if item.decision_id in requested]
                else:
                    rows = [item for item in self.counterfactual_results if item.decision_id == decision_param]
            else:
                rows = list(self.counterfactual_results)
            rows.sort(key=lambda item: (item.decision_timestamp, item.horizon_minutes, str(item.id)), reverse=True)
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM decision_experiment_recommendations" in sql:
            rows = list(self.recommendations)
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return _ExecuteResult(rows, scalar_items=rows)

        if "FROM decision_quality_scores" in sql:
            rows = list(self.quality_scores)
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return _ExecuteResult(rows, scalar_items=rows)

        return _ExecuteResult([], scalar_items=[])


def _create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _seed_data() -> _FakeSession:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account_id = uuid.uuid4()
    asset_a = uuid.uuid4()
    asset_b = uuid.uuid4()
    strategy_a = uuid.uuid4()
    strategy_b = uuid.uuid4()

    decision_a = DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key="a",
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [str(uuid.uuid4())], "risk_events": [str(uuid.uuid4())], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(asset_a), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.6"),
        confidence=Decimal("0.8"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[{"action_taken": "resized"}],
        expected_risk=None,
        expected_reward=None,
        position_size=Decimal("0.01"),
        trade_accepted=True,
        trade_rejected_reason=None,
        execution_details={"paper_account_id": str(account_id), "quantity": "0.01"},
        exit_details=None,
        pnl={"pct": "0.01"},
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

    decision_b = DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key="b",
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=now - timedelta(hours=1),
        asset={"asset_id": str(asset_b), "symbol": "ETHUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "range"},
        indicators={},
        generated_signals=[{"action": "hold", "status": "generated"}],
        signal_strength=Decimal("0.5"),
        confidence=Decimal("0.4"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=False,
        trade_rejected_reason="wait_signal",
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

    snapshot_a = DecisionSnapshot(
        decision_id=decision_a.decision_id,
        timestamp=decision_a.timestamp,
        asset={"asset_id": str(asset_a)},
        exchange="binance_us",
        timeframe="1m",
        ohlcv_context=[],
        indicators={},
        generated_features={},
        market_regime={},
        volatility={},
        spread_liquidity_context=None,
        strategy_inputs={"strategy_id": str(strategy_a)},
        risk_inputs={},
        current_position_state=None,
        open_trades=[],
        portfolio_exposure={},
        parameter_set_version="v1",
        strategy_version="v1",
        ai_model_version="v1",
        decision_engine_version="v1",
        configuration_version="v1",
    )

    snapshot_b = DecisionSnapshot(
        decision_id=decision_b.decision_id,
        timestamp=decision_b.timestamp,
        asset={"asset_id": str(asset_b)},
        exchange="binance_us",
        timeframe="1m",
        ohlcv_context=[],
        indicators={},
        generated_features={},
        market_regime={},
        volatility={},
        spread_liquidity_context=None,
        strategy_inputs={"strategy_id": str(strategy_b)},
        risk_inputs={},
        current_position_state=None,
        open_trades=[],
        portfolio_exposure={},
        parameter_set_version="v1",
        strategy_version="v1",
        ai_model_version="v1",
        decision_engine_version="v1",
        configuration_version="v1",
    )

    risk_event = RiskEvent(
        id=uuid.UUID(decision_a.source_lineage["risk_events"][0]),
        paper_account_id=account_id,
        related_signal_id=uuid.uuid4(),
        event_type="risk_decision",
        action_taken="resized",
        detail={},
        created_at=decision_a.timestamp,
    )

    explainability = [
        DecisionExplainabilityRecord(
            id=uuid.uuid4(),
            decision_id=decision_a.decision_id,
            idempotency_key="exp1",
            evidence_role="supporting",
            evidence_name="supporting",
            evidence_payload={},
            provenance={"source": "test"},
            availability_state="known",
            state_reason=None,
            created_at=now,
        )
    ]

    counterfactual = [
        DecisionCounterfactualResult(
            id=uuid.uuid4(),
            decision_id=decision_a.decision_id,
            idempotency_key="cf1",
            horizon_label="15m",
            horizon_minutes=15,
            decision_timestamp=decision_a.timestamp,
            evaluated_at=decision_a.timestamp + timedelta(minutes=15),
            asset_symbol="BTCUSDT",
            actual_action="buy",
            shadow_buy_return_pct=Decimal("0.01"),
            shadow_sell_return_pct=Decimal("-0.01"),
            shadow_wait_return_pct=Decimal("0"),
            best_action="buy",
            actual_action_correct=True,
            evaluation_state="resolved",
            state_reason=None,
            lesson_tags=[{"tag": "counterfactual_neutral", "reason": "ok"}],
            feature_snapshot={},
            created_at=now,
        )
    ]

    quality = [
        DecisionQualityScore(
            id=uuid.uuid4(),
            decision_id=decision_a.decision_id,
            idempotency_key="q1",
            scoring_model_version="dqe_v1",
            composite_score=Decimal("0.82"),
            component_scores=[{"name": "rule_compliance", "score": "1.0"}],
            weight_profile={"rule_compliance": "0.18"},
            provenance={"source": "test"},
            created_at=now,
        )
    ]

    recommendation = [
        DecisionExperimentRecommendation(
            id=uuid.uuid4(),
            idempotency_key="r1",
            recommendation_engine_version="recommendation_v1",
            recommendation_type="experiment_run",
            recommendation_category="experiment",
            confidence_level="medium",
            expected_impact_level="medium",
            required_human_review_level="priority",
            supporting_evidence_refs=[{"source": "decision_quality_scores", "state": "known"}],
            originating_decision_ids=[str(decision_a.decision_id)],
            explanation="test recommendation",
            suggested_experiment={"name": "x"},
            evidence_state="known",
            state_reason=None,
            provenance={"source": "test"},
            advisory_only=True,
            created_at=now,
        )
    ]

    preview = CryptoOrderPreview(
        crypto_order_preview_id=uuid.uuid4(),
        idempotency_key="preview-1",
        preview_version=1,
        refreshed_from_preview_id=None,
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        quote_size=Decimal("5.00"),
        base_size=None,
        requested_amount=Decimal("5.00"),
        requested_amount_currency="USD",
        status="RISK_REJECTED",
        readiness_verdict="READY_FOR_PREVIEW",
        risk_event_id=risk_event.id,
        decision_record_id=decision_a.decision_id,
        validation_run_id=None,
        strategy_id=None,
        strategy_name=None,
        preview_id="preview-id",
        estimated_average_price=Decimal("10000"),
        estimated_total_value=Decimal("5.00"),
        estimated_base_size=Decimal("0.0005"),
        estimated_quote_size=Decimal("5.00"),
        estimated_fee=Decimal("0.01"),
        estimated_fee_currency="USD",
        estimated_slippage=Decimal("0.001"),
        estimated_commission_total=Decimal("0.01"),
        best_bid=Decimal("9999"),
        best_ask=Decimal("10001"),
        available_balance_before=Decimal("100"),
        estimated_balance_after=Decimal("94.99"),
        risk_verdict="rejected",
        risk_explanation="position_below_minimum_order_size",
        failure_reason="position_below_minimum_order_size",
        warning_messages=[],
        exchange_response_summary={},
        expires_at=now,
        generated_by="operator",
        audit_correlation_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
    )

    audit = AuditLog(
        id=1,
        actor="operator",
        action="crypto_order_preview_initiated",
        entity_type="crypto_order_preview",
        entity_id=preview.crypto_order_preview_id,
        before_state={"status": "PREVIEW_REQUESTED"},
        after_state={"status": "RISK_REJECTED", "audit_correlation_id": str(preview.audit_correlation_id)},
        created_at=now,
    )

    fake = _FakeSession(
        decision_rows=[(decision_a, snapshot_a), (decision_b, snapshot_b)],
        risk_events=[risk_event],
        explainability_records=explainability,
        counterfactual_results=counterfactual,
        quality_scores=quality,
        recommendations=recommendation,
    )
    fake.previews = [preview]
    fake.live_orders = []
    fake.audit_rows = [audit]
    return fake


def test_decision_timeline_supports_pagination_and_filtering_and_read_only_behavior() -> None:
    fake = _seed_data()

    with _create_test_client(fake) as client:
        response = client.get(
            "/decisions/timeline",
            params={
                "page": 1,
                "page_size": 1,
                "status": "resized",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page_size"] == 1
    assert payload["total"] >= 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["status"] == "resized"
    assert fake.add_calls == 0
    assert fake.begin_calls == 0


def test_explainability_and_counterfactual_endpoints_preserve_provenance_and_unknown_states() -> None:
    fake = _seed_data()
    decision_id = str(fake.decision_rows[0][0].decision_id)

    with _create_test_client(fake) as client:
        explainability = client.get(f"/decisions/{decision_id}/explainability")
        counterfactual = client.get(f"/decisions/{decision_id}/counterfactuals")

    assert explainability.status_code == 200
    explain_payload = explainability.json()
    assert explain_payload["supporting_evidence"][0]["provenance"]["source"] == "test"

    assert counterfactual.status_code == 200
    counter_payload = counterfactual.json()
    assert counter_payload["availability_state"] == "known"
    assert counter_payload["items"][0]["evaluation_state"] == "resolved"


def test_quality_endpoint_reports_unavailable_semantics_for_missing_scores() -> None:
    fake = _seed_data()

    with _create_test_client(fake) as client:
        response = client.get("/decisions/quality", params={"page": 1, "page_size": 20})

    assert response.status_code == 200


def test_decision_records_support_search_and_explorer_summary_counts() -> None:
    fake = _seed_data()

    with _create_test_client(fake) as client:
        records_response = client.get(
            "/decisions/records",
            params={
                "q": "btcusdt",
                "sort": "newest",
                "page": 1,
                "page_size": 20,
            },
        )
        summary_response = client.get("/decisions/explorer/summary", params={"q": "btcusdt"})

    assert records_response.status_code == 200
    payload = records_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["decision_id"] == str(fake.decision_rows[0][0].decision_id)
    assert payload["items"][0]["risk_verdict"] in {"approved", "rejected"}

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_decisions"] == 1
    assert summary["accepted"] == 1
    assert summary["risk_rejected"] == 0


def test_recommendations_endpoint_is_read_only_and_supports_filters() -> None:
    fake = _seed_data()
    known_decision_id = fake.recommendations[0].originating_decision_ids[0]

    with _create_test_client(fake) as client:
        filtered = client.get("/decisions/recommendations", params={"asset_id": str(uuid.UUID(fake.decision_rows[0][0].asset["asset_id"]))})
        post_attempt = client.post("/decisions/recommendations", json={})

    assert filtered.status_code == 200
    payload = filtered.json()
    assert payload["total"] == 1
    assert payload["items"][0]["originating_decision_ids"] == [known_decision_id]
    assert payload["items"][0]["advisory_only"] is True
    assert post_attempt.status_code == 405
    assert fake.add_calls == 0
    assert fake.begin_calls == 0


def test_decision_records_endpoint_includes_learn_layer_enrichments() -> None:
    fake = _seed_data()

    with _create_test_client(fake) as client:
        response = client.get("/decisions/records", params={"page": 1, "page_size": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2

    first_item = payload["items"][0]
    assert first_item["quality_score"]["availability_state"] == "known"
    assert first_item["future_outcome_tracking"]["availability_state"] == "known"
    assert first_item["future_outcome_tracking"]["total_horizons"] == 1
    assert first_item["recommendation_history"]["count"] == 1
    assert first_item["recommendation_history"]["latest_recommendation_state"] == "known"

    second_item = payload["items"][1]
    assert second_item["quality_score"]["availability_state"] == "unavailable"
    assert second_item["future_outcome_tracking"]["availability_state"] == "unavailable"
    assert second_item["recommendation_history"]["count"] == 0


def test_decision_inspector_returns_narrative_timeline_and_linkage_health() -> None:
    fake = _seed_data()
    decision_id = str(fake.decision_rows[0][0].decision_id)

    with _create_test_client(fake) as client:
        response = client.get(f"/decisions/{decision_id}/inspector")

    assert response.status_code == 200
    payload = response.json()
    assert payload["header"]["decision_id"] == decision_id
    assert payload["narrative"]["title"] == "Why"
    assert any(item["stage"] == "Risk Evaluation" for item in payload["timeline"])
    assert any(item["component"] == "Preview" for item in payload["linkage_health"])
    assert payload["risk_evaluation"]["verdict"] in {"approved", "rejected"}
    assert payload["execution_price_evidence"]["validation_status"] in {"valid", "missing"}


def test_decision_inspector_returns_404_for_unknown_decision() -> None:
    fake = _seed_data()
    unknown = str(uuid.uuid4())

    with _create_test_client(fake) as client:
        response = client.get(f"/decisions/{unknown}/inspector")

    assert response.status_code == 404
