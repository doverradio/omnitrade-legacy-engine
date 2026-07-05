from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.asset import Asset
from app.models.candle import Candle


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
    def __init__(self, assets: list[Asset], candles: list[Candle]) -> None:
        self.assets = assets
        self.candles = candles

    async def scalar(self, statement: Any) -> Any:
        params = statement.compile().params
        asset_id = next((value for value in params.values() if isinstance(value, uuid.UUID)), None)
        if asset_id is None:
            return None
        if any(asset.id == asset_id for asset in self.assets):
            return asset_id
        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM assets" in sql:
            asset_class = next((value for value in params.values() if value in {"crypto", "stock"}), None)
            is_active = False if "assets.is_active IS false" in sql else True

            filtered_assets = [asset for asset in self.assets if asset.is_active is is_active]
            if asset_class is not None:
                filtered_assets = [asset for asset in filtered_assets if asset.asset_class == asset_class]

            return _ExecuteResult(filtered_assets)

        if "FROM candles" in sql:
            values = list(params.values())
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            interval = next((value for value in values if value in {"1m", "5m", "15m", "1h", "1d"}), None)
            datetimes = [value for value in values if isinstance(value, datetime)]
            end_time = datetimes[0] if datetimes else datetime.now(timezone.utc)
            start_time = datetimes[1] if len(datetimes) > 1 else None

            filtered_candles = [
                candle
                for candle in self.candles
                if candle.asset_id == asset_id
                and candle.interval == interval
                and candle.open_time <= end_time
                and (start_time is None or candle.open_time >= start_time)
            ]
            filtered_candles.sort(key=lambda candle: candle.open_time)

            return _ExecuteResult(filtered_candles)

        return _ExecuteResult([])


@pytest.fixture
def seeded_market_data() -> dict[str, Any]:
    active_asset_id = uuid.uuid4()
    inactive_asset_id = uuid.uuid4()
    open_time = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)

    assets = [
        Asset(
            id=active_asset_id,
            symbol="BTCUSDT",
            asset_class="crypto",
            exchange="binance_us",
            base_currency="USDT",
            supports_fractional=True,
            min_order_notional=Decimal("1.00"),
            qty_step_size=Decimal("0.00001000"),
            is_active=True,
        ),
        Asset(
            id=inactive_asset_id,
            symbol="AAPL",
            asset_class="stock",
            exchange="alpaca",
            supports_fractional=True,
            is_active=False,
        ),
    ]

    candles = [
        Candle(
            asset_id=active_asset_id,
            interval="1m",
            open_time=open_time,
            close_time=datetime(2026, 7, 2, 10, 1, tzinfo=timezone.utc),
            open=Decimal("65000.10"),
            high=Decimal("65120.00"),
            low=Decimal("64950.50"),
            close=Decimal("65080.00"),
            volume=Decimal("12.4531"),
            source="binance_us",
        )
    ]

    return {
        "active_asset_id": active_asset_id,
        "inactive_asset_id": inactive_asset_id,
        "open_time": open_time.isoformat().replace("+00:00", "Z"),
        "session": _FakeSession(assets=assets, candles=candles),
    }


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_get_markets_assets_success(seeded_market_data: dict[str, Any]) -> None:
    with create_test_client(seeded_market_data["session"]) as client:
        response = client.get("/markets/assets")

    assert response.status_code == 200
    payload = response.json()
    ids = {item["id"] for item in payload["items"]}
    assert str(seeded_market_data["active_asset_id"]) in ids
    assert str(seeded_market_data["inactive_asset_id"]) not in ids


def test_get_markets_candles_success(seeded_market_data: dict[str, Any]) -> None:
    with create_test_client(seeded_market_data["session"]) as client:
        response = client.get(
            "/markets/candles",
            params={"asset_id": str(seeded_market_data["active_asset_id"]), "interval": "1m"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_id"] == str(seeded_market_data["active_asset_id"])
    assert payload["interval"] == "1m"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["open_time"] == seeded_market_data["open_time"]


def test_get_markets_candles_unknown_asset_returns_404(seeded_market_data: dict[str, Any]) -> None:
    with create_test_client(seeded_market_data["session"]) as client:
        response = client.get(
            "/markets/candles",
            params={"asset_id": str(uuid.uuid4()), "interval": "1m"},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"


def test_get_markets_candles_invalid_interval_returns_400(seeded_market_data: dict[str, Any]) -> None:
    with create_test_client(seeded_market_data["session"]) as client:
        response = client.get(
            "/markets/candles",
            params={"asset_id": str(seeded_market_data["active_asset_id"]), "interval": "2m"},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"


def test_get_markets_candles_invalid_range_returns_400(seeded_market_data: dict[str, Any]) -> None:
    with create_test_client(seeded_market_data["session"]) as client:
        response = client.get(
            "/markets/candles",
            params={
                "asset_id": str(seeded_market_data["active_asset_id"]),
                "interval": "1m",
                "start_time": "2026-07-02T10:01:00Z",
                "end_time": "2026-07-02T10:00:00Z",
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
