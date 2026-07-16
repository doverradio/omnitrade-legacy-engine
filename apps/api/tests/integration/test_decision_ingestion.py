from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.model_output import ModelOutput
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.services.decisions.ingestion import build_signal_idempotency_key, ingest_decision_records
from app.services.decisions.replay_context import REPLAY_CONTEXT_KEYS


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
        signals: list[Signal],
        model_outputs: list[ModelOutput],
        risk_events: list[RiskEvent],
        trades: list[Trade],
        strategies: list[Strategy] | None = None,
    ) -> None:
        self.signals = signals
        self.model_outputs = model_outputs
        self.risk_events = risk_events
        self.trades = trades
        self.strategies = strategies or []

        self.decision_records: list[DecisionRecord] = []
        self.decision_snapshots: list[DecisionSnapshot] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql and "idempotency_key" in sql:
            key = params.get("idempotency_key_1")
            for item in self.decision_records:
                if item.idempotency_key == key:
                    return item.decision_id
            return None

        if "FROM strategies" in sql:
            strategy_id = params.get("id_1")
            for item in self.strategies:
                if item.id == strategy_id:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM signals" in sql:
            rows = list(self.signals)
            if "id_1" in params:
                requested = {value for value in params.values() if isinstance(value, uuid.UUID)}
                rows = [item for item in rows if item.id in requested]
            rows.sort(key=lambda item: (item.signal_time, item.id))
            return _ExecuteResult(rows)

        if "FROM model_outputs" in sql:
            signal_id = params.get("related_signal_id_1")
            rows = [item for item in self.model_outputs if item.related_signal_id == signal_id]
            rows.sort(key=lambda item: (item.created_at, item.id))
            return _ExecuteResult(rows)

        if "FROM risk_events" in sql:
            signal_id = params.get("related_signal_id_1")
            rows = [item for item in self.risk_events if item.related_signal_id == signal_id]
            rows.sort(key=lambda item: (item.created_at, item.id))
            return _ExecuteResult(rows)

        if "FROM trades" in sql:
            signal_id = params.get("signal_id_1")
            rows = [item for item in self.trades if item.signal_id == signal_id]
            rows.sort(key=lambda item: (item.executed_at, item.id))
            return _ExecuteResult(rows)

        if "FROM strategies" in sql:
            strategy_id = params.get("id_1")
            rows = [item for item in self.strategies if item.id == strategy_id]
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionRecord):
            if not getattr(obj, "decision_id", None):
                obj.decision_id = uuid.uuid4()
            self.decision_records.append(obj)
            return

        if isinstance(obj, DecisionSnapshot):
            self.decision_snapshots.append(obj)

    async def flush(self) -> None:
        return None


def _serialize_source_state(session: _FakeSession) -> dict[str, Any]:
    return {
        "signals": [
            {
                "id": str(item.id),
                "strategy_id": str(item.strategy_id),
                "parameter_set_id": str(item.parameter_set_id),
                "asset_id": str(item.asset_id),
                "signal_time": item.signal_time.isoformat(),
                "action": item.action,
                "raw_strength": format(item.raw_strength, "f") if item.raw_strength is not None else None,
                "ai_confidence": format(item.ai_confidence, "f") if item.ai_confidence is not None else None,
                "regime_tag": item.regime_tag,
                "status": item.status,
            }
            for item in session.signals
        ],
        "model_outputs": [
            {
                "id": str(item.id),
                "model_name": item.model_name,
                "model_version": item.model_version,
                "related_signal_id": str(item.related_signal_id) if item.related_signal_id else None,
                "related_trade_id": str(item.related_trade_id) if item.related_trade_id else None,
                "input_summary": copy.deepcopy(item.input_summary),
                "output": copy.deepcopy(item.output),
                "explanation": item.explanation,
            }
            for item in session.model_outputs
        ],
        "risk_events": [
            {
                "id": str(item.id),
                "paper_account_id": str(item.paper_account_id) if item.paper_account_id else None,
                "related_signal_id": str(item.related_signal_id) if item.related_signal_id else None,
                "event_type": item.event_type,
                "action_taken": item.action_taken,
                "detail": copy.deepcopy(item.detail),
            }
            for item in session.risk_events
        ],
        "trades": [
            {
                "id": str(item.id),
                "paper_account_id": str(item.paper_account_id),
                "signal_id": str(item.signal_id) if item.signal_id else None,
                "asset_id": str(item.asset_id),
                "side": item.side,
                "quantity": format(item.quantity, "f"),
                "price": format(item.price, "f"),
                "fee": format(item.fee, "f"),
                "is_paper": item.is_paper,
                "execution_venue": item.execution_venue,
            }
            for item in session.trades
        ],
    }


def _build_signal(*, action: str, status: str) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        parameter_set_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        signal_time=datetime(2026, 7, 6, tzinfo=timezone.utc),
        action=action,
        raw_strength=Decimal("0.62"),
        ai_confidence=Decimal("0.71"),
        regime_tag="trending_up",
        status=status,
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _build_strategy(*, strategy_id: uuid.UUID, slug: str = "ma_crossover", module_version: str = "1.0.0") -> Strategy:
    return Strategy(
        id=strategy_id,
        name="MA Crossover",
        slug=slug,
        description=None,
        module_version=module_version,
        is_active=True,
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_duplicate_ingestion_produces_no_duplicate_decision_records() -> None:
    signal = _build_signal(action="buy", status="executed")
    strategy = _build_strategy(strategy_id=signal.strategy_id)
    model_output = ModelOutput(
        id=uuid.uuid4(),
        model_name="signal_scorer",
        model_version="1.0.0",
        related_signal_id=signal.id,
        related_trade_id=None,
        input_summary={"volatility": {"atr": "0.1"}},
        output={"score": "0.71"},
        explanation="score",
        created_at=signal.signal_time,
    )
    risk_event = RiskEvent(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        related_signal_id=signal.id,
        event_type="risk_approval",
        action_taken="approved",
        detail={"reason_code": None},
        created_at=signal.signal_time,
    )
    trade = Trade(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        signal_id=signal.id,
        asset_id=signal.asset_id,
        side="buy",
        quantity=Decimal("0.01"),
        price=Decimal("100"),
        fee=Decimal("0.01"),
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=signal.signal_time,
        created_at=signal.signal_time,
    )
    session = _FakeSession(
        signals=[signal],
        model_outputs=[model_output],
        risk_events=[risk_event],
        trades=[trade],
        strategies=[strategy],
    )

    first = await ingest_decision_records(db=session)
    second = await ingest_decision_records(db=session)

    assert first.inserted_records == 1
    assert second.inserted_records == 0
    assert second.skipped_existing == 1
    assert len(session.decision_records) == 1
    assert len(session.decision_snapshots) == 1


@pytest.mark.asyncio
async def test_repeated_ingestion_is_idempotent_and_stable() -> None:
    signal = _build_signal(action="hold", status="generated")
    strategy = _build_strategy(strategy_id=signal.strategy_id)
    session = _FakeSession(signals=[signal], model_outputs=[], risk_events=[], trades=[], strategies=[strategy])

    await ingest_decision_records(db=session)
    first_record = session.decision_records[0]
    first_snapshot = session.decision_snapshots[0]

    await ingest_decision_records(db=session)

    assert len(session.decision_records) == 1
    assert len(session.decision_snapshots) == 1
    assert session.decision_records[0].decision_id == first_record.decision_id
    assert session.decision_snapshots[0].decision_id == first_snapshot.decision_id
    assert session.decision_records[0].idempotency_key == build_signal_idempotency_key(signal.id)


@pytest.mark.asyncio
async def test_provenance_links_remain_stable_across_runs() -> None:
    signal = _build_signal(action="sell", status="risk_rejected")
    strategy = _build_strategy(strategy_id=signal.strategy_id)
    risk_event = RiskEvent(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        related_signal_id=signal.id,
        event_type="drawdown_limit",
        action_taken="blocked",
        detail={"reason_code": "max_drawdown_breached"},
        created_at=signal.signal_time + timedelta(seconds=1),
    )
    session = _FakeSession(signals=[signal], model_outputs=[], risk_events=[risk_event], trades=[], strategies=[strategy])

    await ingest_decision_records(db=session)
    provenance_first = copy.deepcopy(session.decision_records[0].field_provenance)
    lineage_first = copy.deepcopy(session.decision_records[0].source_lineage)

    await ingest_decision_records(db=session)
    provenance_second = session.decision_records[0].field_provenance
    lineage_second = session.decision_records[0].source_lineage

    assert lineage_first == lineage_second
    assert provenance_first == provenance_second
    assert lineage_second["signals"] == [str(signal.id)]
    assert lineage_second["risk_events"] == [str(risk_event.id)]


@pytest.mark.asyncio
async def test_ingestion_does_not_mutate_source_tables() -> None:
    signal = _build_signal(action="buy", status="risk_approved")
    strategy = _build_strategy(strategy_id=signal.strategy_id)
    model_output = ModelOutput(
        id=uuid.uuid4(),
        model_name="explainer",
        model_version="1.0.0",
        related_signal_id=signal.id,
        related_trade_id=None,
        input_summary={"feature": "value"},
        output={"explanation": "ok"},
        explanation="ok",
        created_at=signal.signal_time,
    )
    session = _FakeSession(
        signals=[signal],
        model_outputs=[model_output],
        risk_events=[],
        trades=[],
        strategies=[strategy],
    )

    source_before = _serialize_source_state(session)

    await ingest_decision_records(db=session)

    source_after = _serialize_source_state(session)

    assert source_after == source_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_taken", "expected"),
    [
        ("approved", "ALLOW"),
        ("resized", "ALLOW_RESIZED"),
        ("blocked", "BLOCK"),
        ("mystery", "UNKNOWN"),
    ],
)
async def test_replay_context_normalizes_risk_verdict(action_taken: str, expected: str) -> None:
    signal = _build_signal(action="buy", status="generated")
    strategy = _build_strategy(strategy_id=signal.strategy_id)
    risk_event = RiskEvent(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        related_signal_id=signal.id,
        event_type="risk_gate",
        action_taken=action_taken,
        detail={"reason_code": None},
        created_at=signal.signal_time,
    )
    session = _FakeSession(signals=[signal], model_outputs=[], risk_events=[risk_event], trades=[], strategies=[strategy])

    await ingest_decision_records(db=session)

    replay_context = session.decision_records[0].indicators["replay_context"]
    assert replay_context["normalized_risk_verdict"] == expected


@pytest.mark.asyncio
async def test_replay_context_uses_strategy_identity_version_and_unknown_expected_fields() -> None:
    signal = _build_signal(action="buy", status="executed")
    strategy = _build_strategy(strategy_id=signal.strategy_id, slug="ma_crossover", module_version="2.1.0")
    trade = Trade(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        signal_id=signal.id,
        asset_id=signal.asset_id,
        side="buy",
        quantity=Decimal("0.005"),
        price=Decimal("25000"),
        fee=Decimal("0.10"),
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=signal.signal_time,
        created_at=signal.signal_time,
    )
    session = _FakeSession(
        signals=[signal],
        model_outputs=[],
        risk_events=[],
        trades=[trade],
        strategies=[strategy],
    )

    await ingest_decision_records(db=session)

    replay_context = session.decision_records[0].indicators["replay_context"]
    assert sorted(replay_context.keys()) == sorted(REPLAY_CONTEXT_KEYS)
    assert replay_context["strategy_identity"] == "ma_crossover"
    assert replay_context["strategy_version"] == "2.1.0"
    assert replay_context["timeframe"] == "UNKNOWN"
    assert replay_context["expected_gross_edge"] == "UNKNOWN"
    assert replay_context["expected_fees"] == "UNKNOWN"
    assert replay_context["expected_slippage"] == "UNKNOWN"
    assert replay_context["expected_net_edge"] == "UNKNOWN"
    assert replay_context["actual_execution_fee"] == "0.10"
    assert replay_context["actual_execution_price"] == "25000"
    assert replay_context["actual_execution_quantity"] == "0.005"
    assert "live_trading_profile_id" in replay_context["unknown_fields"]