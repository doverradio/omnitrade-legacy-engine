from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routes import backtests as backtests_route_module
from app.db.session import get_db
from app.main import create_app
from app.models.asset import Asset
from app.models.backtest import Backtest
from app.models.candle import Candle
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.services.backtesting.persistence import PersistedBacktestResult, PersistedBacktestTrade


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
        assets: list[Asset],
        strategies: list[Strategy],
        parameter_sets: list[ParameterSet],
        candles: list[Candle],
    ) -> None:
        self.assets = assets
        self.strategies = strategies
        self.parameter_sets = parameter_sets
        self.candles = candles

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())
        if "FROM strategies" in sql:
            strategy_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((item for item in self.strategies if item.id == strategy_id), None)
        if "FROM parameter_sets" in sql:
            parameter_set_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((item for item in self.parameter_sets if item.id == parameter_set_id), None)
        if "FROM assets" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((item for item in self.assets if item.id == asset_id), None)
        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM candles" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            interval = next((value for value in values if isinstance(value, str) and value in {"1m", "5m", "15m", "1h", "1d"}), None)
            datetimes = [value for value in values if isinstance(value, datetime)]
            start_time = min(datetimes) if datetimes else None
            end_time = max(datetimes) if datetimes else None
            filtered = [
                candle
                for candle in self.candles
                if candle.asset_id == asset_id
                and candle.interval == interval
                and (start_time is None or candle.open_time >= start_time)
                and (end_time is None or candle.open_time <= end_time)
            ]
            filtered.sort(key=lambda candle: candle.open_time)
            return _ExecuteResult(filtered)

        return _ExecuteResult([])


@pytest.fixture
def seeded_backtest_data() -> dict[str, Any]:
    strategy_id = uuid.uuid4()
    parameter_set_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    open_time = datetime(2026, 7, 1, tzinfo=timezone.utc)

    strategy = Strategy(
        id=strategy_id,
        name="MA Crossover",
        slug="ma_crossover",
        module_version="1.0.0",
        is_active=False,
    )
    parameter_set = ParameterSet(
        id=parameter_set_id,
        strategy_id=strategy_id,
        label="default",
        params={"fast_period": 10, "slow_period": 50, "ma_type": "sma"},
        created_by="system",
    )
    asset = Asset(
        id=asset_id,
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        is_active=True,
        supports_fractional=True,
        min_order_notional=Decimal("1.00"),
        qty_step_size=Decimal("0.00001000"),
    )
    candles = [
        Candle(
            asset_id=asset_id,
            interval="1h",
            open_time=open_time + timedelta(hours=index),
            close_time=open_time + timedelta(hours=index + 1),
            open=Decimal("100") + index,
            high=Decimal("101") + index,
            low=Decimal("99") + index,
            close=Decimal("100") + index,
            volume=Decimal("10"),
            source="binance_us",
        )
        for index in range(60)
    ]

    return {
        "strategy_id": strategy_id,
        "parameter_set_id": parameter_set_id,
        "asset_id": asset_id,
        "session": _FakeSession(assets=[asset], strategies=[strategy], parameter_sets=[parameter_set], candles=candles),
    }


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_post_backtests_run_success(monkeypatch: pytest.MonkeyPatch, seeded_backtest_data: dict[str, Any]) -> None:
    created_backtest_id = uuid.uuid4()

    class _FakeBacktest:
        id = created_backtest_id

    async def fake_create_backtest_record(*args, **kwargs):
        return _FakeBacktest()

    async def fake_mark_backtest_running(*args, **kwargs):
        return None

    async def fake_run_backtest_and_persist(*args, **kwargs):
        return None

    monkeypatch.setattr(backtests_route_module, "create_backtest_record", fake_create_backtest_record)
    monkeypatch.setattr(backtests_route_module, "mark_backtest_running", fake_mark_backtest_running)
    monkeypatch.setattr(backtests_route_module, "run_backtest_and_persist", fake_run_backtest_and_persist)

    payload = {
        "strategy_id": str(seeded_backtest_data["strategy_id"]),
        "parameter_set_id": str(seeded_backtest_data["parameter_set_id"]),
        "asset_id": str(seeded_backtest_data["asset_id"]),
        "interval": "1h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-03T12:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 202
    assert response.json() == {"backtest_id": str(created_backtest_id), "status": "running"}


def test_post_backtests_run_invalid_strategy(monkeypatch: pytest.MonkeyPatch, seeded_backtest_data: dict[str, Any]) -> None:
    payload = {
        "strategy_id": str(uuid.uuid4()),
        "parameter_set_id": str(seeded_backtest_data["parameter_set_id"]),
        "asset_id": str(seeded_backtest_data["asset_id"]),
        "interval": "1h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-02T00:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 404


def test_post_backtests_run_invalid_asset(seeded_backtest_data: dict[str, Any]) -> None:
    payload = {
        "strategy_id": str(seeded_backtest_data["strategy_id"]),
        "parameter_set_id": str(seeded_backtest_data["parameter_set_id"]),
        "asset_id": str(uuid.uuid4()),
        "interval": "1h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-02T00:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 404


def test_post_backtests_run_invalid_parameter_set(seeded_backtest_data: dict[str, Any]) -> None:
    payload = {
        "strategy_id": str(seeded_backtest_data["strategy_id"]),
        "parameter_set_id": str(uuid.uuid4()),
        "asset_id": str(seeded_backtest_data["asset_id"]),
        "interval": "1h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-02T00:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 404


def test_post_backtests_run_invalid_interval(seeded_backtest_data: dict[str, Any]) -> None:
    payload = {
        "strategy_id": str(seeded_backtest_data["strategy_id"]),
        "parameter_set_id": str(seeded_backtest_data["parameter_set_id"]),
        "asset_id": str(seeded_backtest_data["asset_id"]),
        "interval": "2h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-02T00:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 400


def test_post_backtests_run_insufficient_history(seeded_backtest_data: dict[str, Any]) -> None:
    payload = {
        "strategy_id": str(seeded_backtest_data["strategy_id"]),
        "parameter_set_id": str(seeded_backtest_data["parameter_set_id"]),
        "asset_id": str(seeded_backtest_data["asset_id"]),
        "interval": "1h",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-07-01T05:00:00Z",
        "initial_capital": "25",
        "fee_bps": "10",
        "slippage_bps": "5",
    }

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.post("/backtests/run", json=payload)

    assert response.status_code == 400


def test_get_backtest_unknown_id(seeded_backtest_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_persisted_backtest(*args, **kwargs):
        return None

    monkeypatch.setattr(backtests_route_module, "get_persisted_backtest", fake_get_persisted_backtest)

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.get(f"/backtests/{uuid.uuid4()}")

    assert response.status_code == 404


def test_get_backtest_completed_retrieval(seeded_backtest_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_persisted_backtest(*args, **kwargs):
        return PersistedBacktestResult(
            id=str(uuid.uuid4()),
            status="completed",
            strategy_id=str(seeded_backtest_data["strategy_id"]),
            parameter_set_id=str(seeded_backtest_data["parameter_set_id"]),
            asset_id=str(seeded_backtest_data["asset_id"]),
            interval="1h",
            start_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 7, 2, tzinfo=timezone.utc),
            initial_capital="25.00",
            fee_bps="10",
            slippage_bps="5",
            metrics={
                "total_return_usd": "4.12",
                "total_return_pct": "0.165",
                "win_rate": "0.57",
                "max_drawdown": "0.092",
                "sharpe_like": "1.21",
                "trade_count": 42,
                "average_trade_usd": "0.098",
                "fee_drag_pct": "0.34",
            },
            small_account_warning={"type": "high_fee_drag", "detail": "Fees consumed 34% of gross backtest gains at this starting balance."},
            trades=(
                PersistedBacktestTrade(
                    side="buy",
                    quantity="0.00038",
                    price="64200.00",
                    executed_at=datetime(2025, 2, 11, 14, 0, tzinfo=timezone.utc),
                    reason="fast MA crossed above slow MA",
                ),
            ),
        )

    monkeypatch.setattr(backtests_route_module, "get_persisted_backtest", fake_get_persisted_backtest)

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.get(f"/backtests/{uuid.uuid4()}")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["metrics"]["trade_count"] == 42


def test_get_backtest_trades_retrieval(seeded_backtest_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_persisted_backtest_trades(*args, **kwargs):
        return (
            PersistedBacktestTrade(
                side="buy",
                quantity="0.00038",
                price="64200.00",
                executed_at=datetime(2025, 2, 11, 14, 0, tzinfo=timezone.utc),
                reason="fast MA crossed above slow MA",
            ),
        )

    monkeypatch.setattr(backtests_route_module, "list_persisted_backtest_trades", fake_list_persisted_backtest_trades)

    with create_test_client(seeded_backtest_data["session"]) as client:
        response = client.get(f"/backtests/{uuid.uuid4()}/trades")

    assert response.status_code == 200
    assert response.json()["items"][0]["side"] == "buy"