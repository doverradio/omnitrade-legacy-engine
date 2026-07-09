from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app


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
    def __init__(self, strategies: list[Any], signals: list[Any], trades: list[Any], decision_records: list[Any]) -> None:
        self.strategies = strategies
        self.signals = signals
        self.trades = trades
        self.decision_records = decision_records

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        if "FROM strategies" in sql:
            return _ExecuteResult(self.strategies)
        if "FROM signals" in sql:
            return _ExecuteResult(self.signals)
        if "FROM trades" in sql:
            return _ExecuteResult(self.trades)
        if "FROM decision_records" in sql:
            return _ExecuteResult(self.decision_records)
        return _ExecuteResult([])


def _client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_strategy_health_returns_empty_when_no_strategies() -> None:
    with _client(_FakeSession([], [], [], [])) as client:
        response = client.get("/arena/strategy-health")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_strategy_health_reports_enabled_and_disabled_rows() -> None:
    now = datetime.now(timezone.utc)

    enabled_strategy_id = uuid.uuid4()
    disabled_strategy_id = uuid.uuid4()
    enabled_signal_id = uuid.uuid4()

    enabled_strategy = SimpleNamespace(
        id=enabled_strategy_id,
        name="MA Crossover",
        slug="ma_crossover",
        is_active=True,
        created_at=now,
    )
    disabled_strategy = SimpleNamespace(
        id=disabled_strategy_id,
        name="RSI Mean Reversion",
        slug="rsi_mean_reversion",
        is_active=False,
        created_at=now,
    )

    signal = SimpleNamespace(
        id=enabled_signal_id,
        strategy_id=enabled_strategy_id,
        signal_time=now,
        created_at=now,
    )

    trade = SimpleNamespace(
        id=uuid.uuid4(),
        signal_id=enabled_signal_id,
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("1"),
        is_paper=True,
        executed_at=now,
    )

    decision_record = SimpleNamespace(
        timestamp=now,
        generated_signals=[{"signal_id": str(enabled_signal_id), "action": "buy"}],
        supporting_strategies=[{"strategy_id": str(enabled_strategy_id)}],
        opposing_strategies=[],
    )

    with _client(_FakeSession([enabled_strategy, disabled_strategy], [signal], [trade], [decision_record])) as client:
        response = client.get("/arena/strategy-health")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2

    enabled = next(item for item in payload["items"] if item["strategy_name"] == "MA Crossover")
    assert enabled["enabled"] is True
    assert enabled["signals_today"] >= 1
    assert enabled["decision_records_today"] >= 1
    assert enabled["status"] == "active"

    disabled = next(item for item in payload["items"] if item["strategy_name"] == "RSI Mean Reversion")
    assert disabled["enabled"] is False
    assert disabled["status"] == "disabled"
