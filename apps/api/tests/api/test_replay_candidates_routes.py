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
        raise AssertionError("Replay candidates endpoint must not open write transactions")

    def add(self, _obj: Any) -> None:
        self.add_calls += 1
        raise AssertionError("Replay candidates endpoint must not insert rows")

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


def _decision_record(*, decision_id: uuid.UUID | None = None, ts: datetime | None = None) -> DecisionRecord:
    timestamp = ts or datetime(2026, 7, 7, tzinfo=timezone.utc)
    row_id = decision_id or uuid.uuid4()
    return DecisionRecord(
        decision_id=row_id,
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=timestamp,
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
        timestamp=datetime(2026, 7, 7, tzinfo=timezone.utc),
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


def test_replay_candidates_can_be_listed_and_are_read_only() -> None:
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    latest = _decision_record(ts=now)
    older = _decision_record(ts=now - timedelta(hours=1))

    fake = _FakeSession(
        decision_records=[older, latest],
        decision_snapshots=[_decision_snapshot(latest.decision_id)],
    )

    with _create_test_client(fake) as client:
        first = client.get("/decisions/replay/candidates", params={"page": 1, "page_size": 50})
        second = client.get("/decisions/replay/candidates", params={"page": 1, "page_size": 50})

    assert first.status_code == 200
    payload = first.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2

    item = payload["items"][0]
    assert "decision_package_id" in item
    assert "decision_id" in item
    assert "package_hash" in item
    assert "package_version" in item
    assert "replay_ready" in item
    assert "missing_artifacts" in item
    assert "unavailable_artifacts" in item
    assert "candidate_reason" in item
    assert "created_at" in item

    # Readiness output should be deterministic across repeated reads.
    payload_two = second.json()
    pairs_one = [(x["decision_id"], x["package_hash"], x["replay_ready"]) for x in payload["items"]]
    pairs_two = [(x["decision_id"], x["package_hash"], x["replay_ready"]) for x in payload_two["items"]]
    assert pairs_one == pairs_two

    # Missing optional artifacts must be explicit and must not break listing.
    with_missing_optional = [x for x in payload["items"] if x["unavailable_artifacts"]]
    assert len(with_missing_optional) >= 1
    assert any("decision_snapshot" in x["unavailable_artifacts"] for x in with_missing_optional)

    # Endpoint remains read-only and does not invoke write pathways.
    assert fake.add_calls == 0
    assert fake.begin_calls == 0