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
    def __init__(self, strategies: list[Any], signals: list[Any], trades: list[Any], decision_records: list[Any], candles: dict[uuid.UUID, Decimal] | None = None) -> None:
        self.strategies = strategies
        self.signals = signals
        self.trades = trades
        self.decision_records = decision_records
        self.candles = candles or {}

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM strategies" in sql:
            return _ExecuteResult(self.strategies)

        if "FROM signals" in sql:
            strategy_ids = set(params.values())
            return _ExecuteResult([signal for signal in self.signals if signal.strategy_id in strategy_ids])

        if "FROM trades" in sql:
            if params:
                signal_ids = set(params.values())
                return _ExecuteResult([trade for trade in self.trades if trade.signal_id in signal_ids and trade.is_paper is True])
            return _ExecuteResult([trade for trade in self.trades if trade.is_paper is True])

        if "FROM decision_records" in sql:
            return _ExecuteResult(self.decision_records)

        if "FROM candles" in sql:
            asset_id = next((value for key, value in params.items() if "asset_id" in str(key)), None)
            price = self.candles.get(asset_id)
            return _ExecuteResult([SimpleNamespace(close=price)] if price is not None else [])

        return _ExecuteResult([])

    async def scalar(self, statement: Any):
        sql = str(statement)
        params = statement.compile().params
        if "FROM candles" in sql:
            asset_id = next((value for key, value in params.items() if "asset_id" in str(key)), None)
            return self.candles.get(asset_id)
        return None


def _client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_strategy_scoreboard_returns_empty_state_when_no_strategies() -> None:
    with _client(_FakeSession([], [], [], [])) as client:
        response = client.get("/arena/strategy-scoreboard")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_strategy_scoreboard_returns_single_strategy_row() -> None:
    strategy_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    signal_buy_id = uuid.uuid4()
    signal_sell_id = uuid.uuid4()

    strategy = SimpleNamespace(
        id=strategy_id,
        name="MA Crossover",
        slug="ma_crossover",
        is_active=True,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    signal_buy = SimpleNamespace(
        id=signal_buy_id,
        strategy_id=strategy_id,
        action="buy",
        signal_time=datetime(2026, 7, 8, 10, tzinfo=timezone.utc),
    )
    signal_sell = SimpleNamespace(
        id=signal_sell_id,
        strategy_id=strategy_id,
        action="sell",
        signal_time=datetime(2026, 7, 8, 12, tzinfo=timezone.utc),
    )
    trade_buy = SimpleNamespace(
        signal_id=signal_buy_id,
        asset_id=asset_id,
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("1"),
        is_paper=True,
        executed_at=datetime(2026, 7, 8, 10, 5, tzinfo=timezone.utc),
        id=uuid.uuid4(),
    )
    trade_sell = SimpleNamespace(
        signal_id=signal_sell_id,
        asset_id=asset_id,
        side="sell",
        quantity=Decimal("1"),
        price=Decimal("110"),
        fee=Decimal("1"),
        is_paper=True,
        executed_at=datetime(2026, 7, 8, 12, 5, tzinfo=timezone.utc),
        id=uuid.uuid4(),
    )
    decision_record = SimpleNamespace(
        generated_signals=[{"signal_id": str(signal_buy_id), "action": "buy"}],
        supporting_strategies=[{"strategy_id": str(strategy_id)}],
        opposing_strategies=[],
    )

    with _client(_FakeSession([strategy], [signal_buy, signal_sell], [trade_buy, trade_sell], [decision_record], {asset_id: Decimal("110")})) as client:
        response = client.get("/arena/strategy-scoreboard")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["strategy_name"] == "MA Crossover"
    assert row["enabled"] is True
    assert row["status"] == "active"
    assert row["signals_generated"] == 2
    assert row["buy_signals"] == 1
    assert row["sell_signals"] == 1
    assert row["paper_trades"] == 2
    assert row["open_positions"] == 0
    assert row["realized_pnl"] == "8"
    assert row["unrealized_pnl"] == "0"
    assert row["decision_records"] == 1


def test_strategy_scoreboard_returns_multiple_strategies() -> None:
    active_strategy_id = uuid.uuid4()
    disabled_strategy_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    active_strategy = SimpleNamespace(
        id=active_strategy_id,
        name="MA Crossover",
        slug="ma_crossover",
        is_active=True,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    disabled_strategy = SimpleNamespace(
        id=disabled_strategy_id,
        name="RSI Mean Reversion",
        slug="rsi_mean_reversion",
        is_active=False,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    active_signal = SimpleNamespace(
        id=uuid.uuid4(),
        strategy_id=active_strategy_id,
        action="hold",
        signal_time=datetime(2026, 7, 8, 9, tzinfo=timezone.utc),
    )
    disabled_signal = SimpleNamespace(
        id=uuid.uuid4(),
        strategy_id=disabled_strategy_id,
        action="buy",
        signal_time=datetime(2026, 7, 8, 11, tzinfo=timezone.utc),
    )
    active_trade = SimpleNamespace(
        signal_id=active_signal.id,
        asset_id=asset_id,
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("1"),
        is_paper=True,
        executed_at=datetime(2026, 7, 8, 9, 5, tzinfo=timezone.utc),
        id=uuid.uuid4(),
    )
    disabled_trade = SimpleNamespace(
        signal_id=disabled_signal.id,
        asset_id=asset_id,
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("200"),
        fee=Decimal("1"),
        is_paper=True,
        executed_at=datetime(2026, 7, 8, 11, 5, tzinfo=timezone.utc),
        id=uuid.uuid4(),
    )
    decision_record = SimpleNamespace(
        generated_signals=[{"signal_id": str(active_signal.id), "action": "hold"}],
        supporting_strategies=[{"strategy_id": str(active_strategy_id)}],
        opposing_strategies=[{"strategy_id": str(disabled_strategy_id)}],
    )

    with _client(
        _FakeSession(
            [active_strategy, disabled_strategy],
            [active_signal, disabled_signal],
            [active_trade, disabled_trade],
            [decision_record],
            {asset_id: Decimal("210")},
        )
    ) as client:
        response = client.get("/arena/strategy-scoreboard")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["strategy_name"] == "MA Crossover"
    assert payload["items"][0]["enabled"] is True
    assert payload["items"][1]["strategy_name"] == "RSI Mean Reversion"
    assert payload["items"][1]["enabled"] is False
