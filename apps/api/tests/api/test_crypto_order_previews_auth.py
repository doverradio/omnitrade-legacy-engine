from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app


class _FakeSession:
    async def scalar(self, _statement: Any) -> Any:
        return None


def _create_client(*, raise_server_exceptions: bool = True) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_crypto_preview_create_requires_operator_auth() -> None:
    payload = {
        "exchange_connection_id": "11111111-1111-1111-1111-111111111111",
        "environment": "production",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "quote_size": "5.00",
        "requested_amount_currency": "USD",
    }

    with _create_client() as client:
        response = client.post("/crypto-order-previews", json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_crypto_preview_create_rejects_non_operator_auth() -> None:
    payload = {
        "exchange_connection_id": "11111111-1111-1111-1111-111111111111",
        "environment": "production",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "quote_size": "5.00",
        "requested_amount_currency": "USD",
    }

    with _create_client() as client:
        response = client.post(
            "/crypto-order-previews",
            json=payload,
            headers={"Authorization": "Bearer service:automation"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
