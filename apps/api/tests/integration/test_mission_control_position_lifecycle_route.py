from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.position_lifecycle import PositionLifecycleItemResponse, PositionLifecycleResponse


class _DummySession:
    async def execute(self, statement, params=None):
        _ = (statement, params)
        return None


def _payload() -> PositionLifecycleResponse:
    return PositionLifecycleResponse(
        generated_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        count=1,
        items=[
            PositionLifecycleItemResponse(
                position_id="a1111111-1111-1111-1111-111111111111",
                live_trading_profile_id="b1111111-1111-1111-1111-111111111111",
                account_id="c1111111-1111-1111-1111-111111111111",
                capital_campaign_id=7,
                symbol="BTC-USD",
                asset_class="crypto",
                policy_id="pl-policy-crypto-venue-neutral-v1",
                policy_version="1.0.0",
                lifecycle_state="HOLDING_FOR_PROFIT",
                recommendation="HOLD_FOR_PROFIT",
                reason="Expected net result is below threshold.",
                position_size="0.0100",
                entry_price="65000.00",
                current_price="65100.00",
                current_market_value="651.00",
                expected_net_realized_pnl_if_sold_now="-1.25",
                break_even_price="65220.00",
                minimum_profitable_exit_price="65420.00",
                opened_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
                last_fill_at=datetime(2026, 7, 10, 9, 5, tzinfo=timezone.utc),
                provider_order_ids=["ord-1"],
                provider_fill_ids=["fill-1"],
                accounting_record_count=1,
                market_data_timestamp=datetime(2026, 7, 10, 11, 59, tzinfo=timezone.utc),
                market_data_interval="15m",
                market_data_source="kraken_spot",
                market_data_candle_id=42,
                market_data_age_minutes=1,
                market_data_stale=False,
                stale_indicator=False,
                dust_indicator=False,
                closed_indicator=False,
                evaluated_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
            )
        ],
    )


def test_position_lifecycle_route_returns_shape(monkeypatch) -> None:
    app = create_app()
    captured = {}

    async def _override_db():
        yield _DummySession()

    async def _service_stub(*_args, **_kwargs):
        captured.update(_kwargs)
        return _payload()

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.mission_control.build_position_lifecycle_report", _service_stub)

    with TestClient(app) as client:
        response = client.get(
            "/mission-control/positions/lifecycle"
            "?account_id=c1111111-1111-1111-1111-111111111111"
            "&campaign_id=7"
            "&asset_class=crypto"
            "&recommendation=HOLD_FOR_PROFIT"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["symbol"] == "BTC-USD"
    assert payload["items"][0]["lifecycle_state"] == "HOLDING_FOR_PROFIT"
    assert payload["items"][0]["policy_id"] == "pl-policy-crypto-venue-neutral-v1"
    assert captured["position_id"] is None
    assert captured["account_id"] == UUID("c1111111-1111-1111-1111-111111111111")
    assert captured["campaign_id"] == 7


def test_position_lifecycle_route_forwards_position_id_filter(monkeypatch) -> None:
    app = create_app()
    captured = {}

    async def _override_db():
        yield _DummySession()

    async def _service_stub(*_args, **_kwargs):
        captured.update(_kwargs)
        return _payload()

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.mission_control.build_position_lifecycle_report", _service_stub)

    with TestClient(app) as client:
        response = client.get("/mission-control/positions/lifecycle?position_id=position-abc")

    assert response.status_code == 200
    assert captured["position_id"] == "position-abc"


def test_position_lifecycle_route_ignores_removed_latest_only_query(monkeypatch) -> None:
    app = create_app()

    async def _override_db():
        yield _DummySession()

    async def _service_stub(*_args, **_kwargs):
        return _payload()

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.mission_control.build_position_lifecycle_report", _service_stub)

    with TestClient(app) as client:
        response = client.get("/mission-control/positions/lifecycle?latest_only=true")

    assert response.status_code == 200
