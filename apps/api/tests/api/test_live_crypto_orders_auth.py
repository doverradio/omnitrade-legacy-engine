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
    return TestClient(app)


def test_live_crypto_order_submit_requires_bearer_auth() -> None:
    payload = {
        "live_crypto_order_id": "11111111-1111-1111-1111-111111111111",
        "confirmation_challenge_id": "22222222-2222-2222-2222-222222222222",
        "confirmation_phrase": "BUY BTC",
        "operator_identity": "operator:human",
        "idempotency_token": "submit-token",
    }

    with _create_client() as client:
        response = client.post("/live-crypto-orders/submit", json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"