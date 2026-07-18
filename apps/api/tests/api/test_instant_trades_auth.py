from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app


class _FakeSession:
    async def scalar(self, _statement: Any) -> Any:
        return None

    async def scalars(self, _statement: Any) -> Any:
        return []


def _create_client() -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=False)


def test_instant_buy_requires_bearer_auth() -> None:
    payload = {
        "paper_account_id": "11111111-1111-1111-1111-111111111111",
        "live_trading_profile_id": "22222222-2222-2222-2222-222222222222",
        "provider": "kraken_spot",
        "environment": "production",
        "product": "BTC-USD",
        "quote_amount": "5.00",
        "actor": "11111111-1111-1111-1111-111111111111",
        "confirmation": True,
        "idempotency_key": "idem-1",
    }

    with _create_client() as client:
        response = client.post("/instant-trades/buy", json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
