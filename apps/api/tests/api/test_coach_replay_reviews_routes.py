from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot


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
    ) -> None:
        self.decision_records = decision_records
        self.decision_snapshots = decision_snapshots
        self.add_calls = 0
        self.begin_calls = 0

    def begin(self) -> Any:
        self.begin_calls += 1
        raise AssertionError("Coach replay reviews endpoint must not open write transactions")

    def add(self, _obj: Any) -> None:
        self.add_calls += 1
        raise AssertionError("Coach replay reviews endpoint must not insert rows")

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            for item in self.decision_records:
                if item.decision_id == decision_id:
                    return item
            return None

        if "FROM decision_snapshots" in sql and "decision_id_1" in params:
            decision_id = params.get("decision_id_1")
            for item in self.decision_snapshots:
                if item.decision_id == decision_id:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql and "decision_id_1" not in params:
            rows = list(self.decision_records)
            rows.sort(key=lambda item: (item.timestamp, str(item.decision_id)), reverse=True)
            return _ExecuteResult([item.decision_id for item in rows])

        if "FROM decision_explainability_records" in sql:
            return _ExecuteResult([])

        if "FROM decision_quality_scores" in sql:
            return _ExecuteResult([])

        if "FROM decision_counterfactual_results" in sql:
            return _ExecuteResult([])

        if "FROM decision_alternative_actions" in sql:
            return _ExecuteResult([])

        return _ExecuteResult([])


def _create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _decision_record(*, ts: datetime) -> DecisionRecord:
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=ts,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={"rsi": 42.0},
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.60"),
        confidence=Decimal("0.70"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=Decimal("0.01"),
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


def _decision_snapshot(decision_id: uuid.UUID) -> DecisionSnapshot:
    return DecisionSnapshot(
        decision_id=decision_id,
        timestamp=datetime(2026, 7, 8, tzinfo=timezone.utc),
        asset={"asset_id": str(uuid.uuid4())},
        exchange="binance",
        timeframe="1m",
        ohlcv_context=[],
        indicators={},
        generated_features={},
        market_regime={},
        volatility={},
        spread_liquidity_context=None,
        strategy_inputs={"strategy_id": str(uuid.uuid4())},
        risk_inputs={},
        current_position_state=None,
        open_trades=[],
        portfolio_exposure={},
        parameter_set_version="ps_v1",
        strategy_version="strategy_v1",
        ai_model_version="ai_v1",
        decision_engine_version="v1",
        configuration_version="risk_v1",
    )


def test_coach_replay_reviews_endpoint_is_read_only_and_deterministic() -> None:
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    first = _decision_record(ts=now)
    second = _decision_record(ts=now - timedelta(hours=1))

    fake = _FakeSession(
        decision_records=[second, first],
        decision_snapshots=[_decision_snapshot(first.decision_id)],
    )

    with _create_test_client(fake) as client:
        response_a = client.get("/decisions/coach/replay-reviews", params={"page": 1, "page_size": 50})
        response_b = client.get("/decisions/coach/replay-reviews", params={"page": 1, "page_size": 50})

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    payload_a = response_a.json()
    payload_b = response_b.json()

    assert payload_a["total"] == 2
    assert len(payload_a["items"]) == 2

    item = payload_a["items"][0]
    assert "decision_id" in item
    assert "decision_package_id" in item
    assert "package_hash" in item
    assert "package_version" in item
    assert "replay_ready" in item
    assert "summary" in item
    assert "strengths" in item
    assert "weaknesses" in item
    assert "missing_evidence" in item
    assert "suggested_followups" in item
    assert item["advisory_only"] is True

    deterministic_a = [
        (x["decision_id"], x["decision_package_id"], x["package_hash"], x["summary"], x["advisory_only"])
        for x in payload_a["items"]
    ]
    deterministic_b = [
        (x["decision_id"], x["decision_package_id"], x["package_hash"], x["summary"], x["advisory_only"])
        for x in payload_b["items"]
    ]
    assert deterministic_a == deterministic_b

    with_missing = [x for x in payload_a["items"] if x["missing_evidence"]]
    assert len(with_missing) >= 1

    # Route is read-only, no writes or replay side effects.
    assert fake.add_calls == 0
    assert fake.begin_calls == 0