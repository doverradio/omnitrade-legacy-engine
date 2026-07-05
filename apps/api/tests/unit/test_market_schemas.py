from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.models.asset import Asset
from app.models.candle import Candle
from app.schemas.asset import AssetListResponse, AssetResponse
from app.schemas.candle import CandleListResponse, CandleResponse


def test_asset_schema_serializes_small_account_fields_as_contract_strings() -> None:
    asset = Asset(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        is_active=True,
        supports_fractional=True,
        min_order_notional=Decimal("1.00"),
        qty_step_size=Decimal("0.00001000"),
    )

    payload = AssetListResponse(items=[AssetResponse.model_validate(asset)]).model_dump(mode="json")

    assert payload == {
        "items": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "symbol": "BTCUSDT",
                "asset_class": "crypto",
                "exchange": "binance_us",
                "is_active": True,
                "supports_fractional": True,
                "min_order_notional": "1.00",
                "qty_step_size": "0.00001000",
            }
        ]
    }


def test_candle_schema_serializes_numeric_fields_as_strings() -> None:
    candle = Candle(
        asset_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        interval="1d",
        open_time=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 2, 10, 1, tzinfo=timezone.utc),
        open=Decimal("65000.10"),
        high=Decimal("65120.00"),
        low=Decimal("64950.50"),
        close=Decimal("65080.00"),
        volume=Decimal("12.4531"),
        source="binance_us",
    )

    payload = CandleListResponse(
        asset_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        interval="1d",
        items=[CandleResponse.model_validate(candle)],
    ).model_dump(mode="json")

    assert payload == {
        "asset_id": "11111111-1111-1111-1111-111111111111",
        "interval": "1d",
        "items": [
            {
                "open_time": "2026-07-02T10:00:00Z",
                "open": "65000.10",
                "high": "65120.00",
                "low": "64950.50",
                "close": "65080.00",
                "volume": "12.4531",
            }
        ],
    }