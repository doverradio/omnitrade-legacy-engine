from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.routes import controlled_proofs as route_module
from app.db.session import get_db
from app.main import create_app


def _create_client() -> TestClient:
    app = create_app()

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=True)


class _FakeProof:
    def __init__(self, proof_id: uuid.UUID) -> None:
        self.proof_id = proof_id


def _view_payload(proof_id: uuid.UUID) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "proof_id": proof_id, "status": "REQUESTED", "provider": "kraken_spot", "environment": "production",
        "campaign_id": uuid.uuid4(), "campaign_version": 1, "product_id": "BTC-USD",
        "max_notional_usd": Decimal("5"), "requested_by": "operator:human", "requested_at": now,
        "expires_at": now, "claimed_at": None, "blocked_reason": None, "failure_reason": None,
        "cancelled_at": None, "cancelled_by": None, "audit_correlation_id": uuid.uuid4(),
        "decision": None, "mandate": None, "package": None, "buy_order": None, "position": None,
        "sell_order": None, "reconciliation": None, "fees_usd": None, "net_pnl_usd": None,
        "terminal_verdict": None,
    }


def test_create_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "create_controlled_proof", _unexpected)
    client = _create_client()

    response = client.post(
        "/api/v1/operator/controlled-proofs",
        json={"product_id": "BTC-USD", "idempotency_key": "k-1"},
    )

    assert response.status_code == 401


def test_get_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "get_controlled_proof_view", _unexpected)
    client = _create_client()

    response = client.get(f"/api/v1/operator/controlled-proofs/{uuid.uuid4()}")

    assert response.status_code == 401


def test_cancel_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "cancel_controlled_proof", _unexpected)
    client = _create_client()

    response = client.post(f"/api/v1/operator/controlled-proofs/{uuid.uuid4()}/cancel", json={})

    assert response.status_code == 401


def test_create_endpoint_succeeds_with_operator_auth_and_ignores_extra_fields(monkeypatch) -> None:
    proof_id = uuid.uuid4()
    seen_kwargs: dict = {}

    async def _fake_create(*, db, product_id, idempotency_key, expires_in_minutes, actor):
        seen_kwargs.update(product_id=product_id, idempotency_key=idempotency_key, actor=actor)
        return _FakeProof(proof_id)

    async def _fake_view(*, db, proof_id):
        return _view_payload(proof_id)

    monkeypatch.setattr(route_module, "create_controlled_proof", _fake_create)
    monkeypatch.setattr(route_module, "get_controlled_proof_view", _fake_view)
    client = _create_client()

    # A caller attempting to smuggle in scope/provider/campaign/notional
    # fields must have them silently ignored -- the schema simply has no
    # such fields, so pydantic drops them rather than passing them through.
    response = client.post(
        "/api/v1/operator/controlled-proofs",
        json={
            "product_id": "BTC-USD", "idempotency_key": "k-2",
            "provider": "coinbase", "max_notional_usd": "500000", "campaign_id": str(uuid.uuid4()),
        },
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 201
    assert response.json()["proof_id"] == str(proof_id)
    assert seen_kwargs["actor"] == "operator:human"
    assert seen_kwargs["product_id"] == "BTC-USD"


def test_get_endpoint_succeeds_with_operator_auth(monkeypatch) -> None:
    proof_id = uuid.uuid4()

    async def _fake_view(*, db, proof_id):
        return _view_payload(proof_id)

    monkeypatch.setattr(route_module, "get_controlled_proof_view", _fake_view)
    client = _create_client()

    response = client.get(
        f"/api/v1/operator/controlled-proofs/{proof_id}",
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REQUESTED"


def test_cancel_endpoint_succeeds_with_operator_auth(monkeypatch) -> None:
    proof_id = uuid.uuid4()
    seen_actor = {}

    async def _fake_cancel(*, db, proof_id, actor, reason):
        seen_actor["actor"] = actor
        return _FakeProof(proof_id)

    async def _fake_view(*, db, proof_id):
        payload = _view_payload(proof_id)
        payload["status"] = "CANCELLED"
        return payload

    monkeypatch.setattr(route_module, "cancel_controlled_proof", _fake_cancel)
    monkeypatch.setattr(route_module, "get_controlled_proof_view", _fake_view)
    client = _create_client()

    response = client.post(
        f"/api/v1/operator/controlled-proofs/{proof_id}/cancel",
        json={"reason": "operator changed mind"},
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert seen_actor["actor"] == "operator:human"
