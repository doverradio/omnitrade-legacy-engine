from __future__ import annotations

import uuid
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.services.decisions.replay_candidates import (
    certify_decision_package_readiness_v0,
    list_replay_candidates_v0,
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


class _FakeSession:
    def __init__(
        self,
        *,
        decision_records: list[DecisionRecord],
        decision_snapshots: list[DecisionSnapshot],
    ) -> None:
        self.decision_records = decision_records
        self.decision_snapshots = decision_snapshots

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


def _decision_record(*, decision_id: uuid.UUID | None = None, ts: datetime | None = None) -> DecisionRecord:
    timestamp = ts or datetime(2026, 7, 6, tzinfo=timezone.utc)
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
        timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
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


@pytest.mark.asyncio
async def test_certification_is_deterministic() -> None:
    decision = _decision_record()
    session = _FakeSession(decision_records=[decision], decision_snapshots=[_decision_snapshot(decision.decision_id)])

    a = await certify_decision_package_readiness_v0(db=session, decision_id=decision.decision_id)
    b = await certify_decision_package_readiness_v0(db=session, decision_id=decision.decision_id)

    assert a is not None
    assert b is not None
    assert a.decision_package_id == b.decision_package_id
    assert a.package_hash == b.package_hash
    assert a.package_version == b.package_version
    assert a.replay_ready is True


@pytest.mark.asyncio
async def test_missing_optional_artifacts_are_explicitly_reported() -> None:
    decision = _decision_record()
    session = _FakeSession(decision_records=[decision], decision_snapshots=[])

    item = await certify_decision_package_readiness_v0(db=session, decision_id=decision.decision_id)

    assert item is not None
    assert item.replay_ready is True
    assert "decision_snapshot" in item.missing_artifacts
    assert "decision_snapshot" in item.unavailable_artifacts
    assert item.candidate_reason == "replay_ready_with_missing_optional_artifacts"


@pytest.mark.asyncio
async def test_replay_candidates_list_is_readable_and_sorted() -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    older = now - timedelta(hours=1)

    recent = _decision_record(ts=now)
    previous = _decision_record(ts=older)

    session = _FakeSession(
        decision_records=[previous, recent],
        decision_snapshots=[_decision_snapshot(recent.decision_id), _decision_snapshot(previous.decision_id)],
    )

    rows = await list_replay_candidates_v0(db=session)

    assert len(rows) == 2
    assert rows[0].decision_id == recent.decision_id
    assert rows[1].decision_id == previous.decision_id