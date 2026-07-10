from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.db.session import get_db
from app.main import create_app
from app.services import live_crypto_orders as live_crypto_orders_service


class _FakeSession:
    async def scalar(self, _statement: Any) -> Any:
        return None

    async def scalars(self, _statement: Any) -> Any:
        return []


def _create_client(*, raise_server_exceptions: bool = True) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


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


def test_live_crypto_order_submit_rejects_malformed_bearer_auth() -> None:
    payload = {
        "live_crypto_order_id": "11111111-1111-1111-1111-111111111111",
        "confirmation_challenge_id": "22222222-2222-2222-2222-222222222222",
        "confirmation_phrase": "BUY BTC",
        "operator_identity": "operator:human",
        "idempotency_token": "submit-token",
    }

    with _create_client() as client:
        response = client.post(
            "/live-crypto-orders/submit",
            json=payload,
            headers={"Authorization": "Bearer    "},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_live_crypto_order_prepare_rejects_authenticated_operator_mismatch() -> None:
    payload = {
        "live_trading_profile_id": "11111111-1111-1111-1111-111111111111",
        "crypto_order_preview_id": "22222222-2222-2222-2222-222222222222",
        "operator_identity": "operator:human",
        "idempotency_token": "prepare-token",
    }

    with _create_client() as client:
        response = client.post(
            "/live-crypto-orders/prepare-confirmation",
            json=payload,
            headers={"Authorization": "Bearer operator:other"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["message"] == "Authenticated operator identity mismatch"


def test_live_crypto_order_submit_fails_closed_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "live_crypto_order_id": "11111111-1111-1111-1111-111111111111",
        "confirmation_challenge_id": "22222222-2222-2222-2222-222222222222",
        "confirmation_phrase": "BUY BTC",
        "operator_identity": "operator:human",
        "idempotency_token": "submit-token",
    }
    monkeypatch.setattr(
        live_crypto_orders_service,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_order_submission_enabled": False,
                "live_crypto_dry_run_enabled": True,
                "live_crypto_max_order_usd": live_crypto_orders_service.Decimal("5"),
                "live_crypto_preview_max_age_seconds": 30,
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_readiness_max_age_seconds": 60,
                "live_crypto_price_max_age_seconds": 30,
                "live_crypto_confirmation_challenge_minutes": 1,
            },
        )(),
    )

    with _create_client(raise_server_exceptions=False) as client:
        response = client.post(
            "/live-crypto-orders/submit",
            json=payload,
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_live_crypto_order_prepare_fails_closed_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "live_trading_profile_id": "11111111-1111-1111-1111-111111111111",
        "crypto_order_preview_id": "22222222-2222-2222-2222-222222222222",
        "operator_identity": "operator:human",
        "idempotency_token": "prepare-token",
    }
    monkeypatch.setattr(
        live_crypto_orders_service,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_order_submission_enabled": False,
                "live_crypto_dry_run_enabled": True,
                "live_crypto_max_order_usd": live_crypto_orders_service.Decimal("5"),
                "live_crypto_preview_max_age_seconds": 30,
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_readiness_max_age_seconds": 60,
                "live_crypto_price_max_age_seconds": 30,
                "live_crypto_confirmation_challenge_minutes": 1,
            },
        )(),
    )

    with _create_client(raise_server_exceptions=False) as client:
        response = client.post(
            "/live-crypto-orders/prepare-confirmation",
            json=payload,
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"