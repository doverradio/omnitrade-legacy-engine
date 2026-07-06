from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.candle import Candle
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_record import DecisionRecord
from app.services.decisions.counterfactuals import evaluate_counterfactual_outcome_ledger_v1


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
    def __init__(self, *, decision_records: list[DecisionRecord], candles: list[Candle]) -> None:
        self.decision_records = decision_records
        self.candles = candles
        self.counterfactual_results: list[DecisionCounterfactualResult] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_counterfactual_results" in sql:
            decision_id = params.get("decision_id_1")
            horizon_minutes = params.get("horizon_minutes_1")
            for item in self.counterfactual_results:
                if item.decision_id == decision_id and item.horizon_minutes == horizon_minutes:
                    return item.id
            return None

        if "SELECT candles.close" in sql:
            asset_id = params.get("asset_id_1")
            target_ts = params.get("open_time_1")
            rows = [
                candle for candle in self.candles if candle.asset_id == asset_id and candle.open_time <= target_ts
            ]
            rows.sort(key=lambda item: item.open_time, reverse=True)
            return rows[0].close if rows else None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)

        if "FROM decision_records" in sql:
            return _ExecuteResult(list(self.decision_records))

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionCounterfactualResult):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.counterfactual_results.append(obj)


def _decision_record(*, symbol: str, action: str, ts: datetime) -> DecisionRecord:
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=ts,
        asset={"asset_id": str(uuid.uuid4()), "symbol": symbol},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": action, "status": "generated"}],
        signal_strength=Decimal("0.55"),
        confidence=Decimal("0.70"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=action != "hold",
        trade_rejected_reason="wait_signal" if action == "hold" else None,
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


def _candles_for(asset_id: uuid.UUID, start: datetime) -> list[Candle]:
    return [
        Candle(
            asset_id=asset_id,
            interval="1m",
            open_time=start,
            close_time=start + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            source="binance_us",
        ),
        Candle(
            asset_id=asset_id,
            interval="1m",
            open_time=start + timedelta(minutes=15),
            close_time=start + timedelta(minutes=16),
            open=Decimal("103"),
            high=Decimal("103"),
            low=Decimal("103"),
            close=Decimal("103"),
            volume=Decimal("1"),
            source="binance_us",
        ),
        Candle(
            asset_id=asset_id,
            interval="1m",
            open_time=start + timedelta(hours=1),
            close_time=start + timedelta(hours=1, minutes=1),
            open=Decimal("102"),
            high=Decimal("102"),
            low=Decimal("102"),
            close=Decimal("102"),
            volume=Decimal("1"),
            source="binance_us",
        ),
        Candle(
            asset_id=asset_id,
            interval="1m",
            open_time=start + timedelta(hours=24),
            close_time=start + timedelta(hours=24, minutes=1),
            open=Decimal("110"),
            high=Decimal("110"),
            low=Decimal("110"),
            close=Decimal("110"),
            volume=Decimal("1"),
            source="binance_us",
        ),
    ]


@pytest.mark.asyncio
async def test_counterfactual_v1_persists_btc_only_horizons_idempotently() -> None:
    base = datetime(2026, 7, 6, tzinfo=timezone.utc)

    btc_decision = _decision_record(symbol="BTCUSDT", action="buy", ts=base)
    eth_decision = _decision_record(symbol="ETHUSDT", action="sell", ts=base)

    btc_asset_id = uuid.UUID(btc_decision.asset["asset_id"])
    eth_asset_id = uuid.UUID(eth_decision.asset["asset_id"])

    session = _FakeSession(
        decision_records=[btc_decision, eth_decision],
        candles=_candles_for(btc_asset_id, base) + _candles_for(eth_asset_id, base),
    )

    first = await evaluate_counterfactual_outcome_ledger_v1(
        db=session,
        as_of=base + timedelta(hours=25),
    )
    second = await evaluate_counterfactual_outcome_ledger_v1(
        db=session,
        as_of=base + timedelta(hours=25),
    )

    assert first.inserted_results == 3
    assert first.skipped_non_btc == 1
    assert second.inserted_results == 0
    assert second.skipped_existing == 3

    inserted_horizons = sorted(item.horizon_minutes for item in session.counterfactual_results)
    assert inserted_horizons == [15, 60, 1440]
    assert all(item.asset_symbol == "BTCUSDT" for item in session.counterfactual_results)
    assert all(item.evaluation_state == "resolved" for item in session.counterfactual_results)
