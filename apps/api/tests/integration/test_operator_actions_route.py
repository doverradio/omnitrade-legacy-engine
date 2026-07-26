from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.routes import operator_actions as route_module
from app.db.session import get_db
from app.main import create_app


def _create_client() -> TestClient:
    app = create_app()

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=True)


def _action_view(action_id: uuid.UUID) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "action_id": action_id, "action_type": "RUN_CONTROLLED_PROOF", "status": "ACCEPTED",
        "actor": "operator:human", "idempotency_key": "k-1", "parameters": {"product_id": "BTC-USD"},
        "result": None, "linked_resource_type": "controlled_proof_run", "linked_resource_id": uuid.uuid4(),
        "blocked_reason": None, "failure_reason": None, "requested_at": now, "accepted_at": now,
        "started_at": None, "completed_at": None, "expires_at": None,
    }


def test_submit_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "submit_operator_action", _unexpected)
    client = _create_client()

    response = client.post(
        "/api/v1/operator/actions",
        json={"action_type": "RUN_CONTROLLED_PROOF", "idempotency_key": "k-1", "parameters": {"product_id": "BTC-USD"}},
    )

    assert response.status_code == 401


def test_get_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "get_operator_action", _unexpected)
    client = _create_client()

    response = client.get(f"/api/v1/operator/actions/{uuid.uuid4()}")

    assert response.status_code == 401


def test_list_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "list_operator_actions", _unexpected)
    client = _create_client()

    response = client.get("/api/v1/operator/actions")

    assert response.status_code == 401


def test_submit_endpoint_succeeds_with_operator_auth_and_ignores_actor_field(monkeypatch) -> None:
    action_id = uuid.uuid4()
    seen_kwargs: dict = {}

    async def _fake_submit(*, db, action_type, idempotency_key, parameters, actor):
        seen_kwargs.update(action_type=action_type, idempotency_key=idempotency_key, parameters=parameters, actor=actor)
        return _action_view(action_id)

    monkeypatch.setattr(route_module, "submit_operator_action", _fake_submit)
    client = _create_client()

    # A caller attempting to smuggle in an actor field must have it silently
    # ignored -- the schema has no such field, and the real actor identity
    # always comes from the authorization header via get_authorized_operator.
    response = client.post(
        "/api/v1/operator/actions",
        json={
            "action_type": "RUN_CONTROLLED_PROOF", "idempotency_key": "k-2",
            "parameters": {"product_id": "BTC-USD"}, "actor": "operator:mallory",
        },
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 201
    assert response.json()["action_id"] == str(action_id)
    assert seen_kwargs["actor"] == "operator:human"
    assert seen_kwargs["parameters"] == {"product_id": "BTC-USD"}


def test_get_endpoint_succeeds_with_operator_auth(monkeypatch) -> None:
    action_id = uuid.uuid4()

    async def _fake_get(*, db, action_id):
        return _action_view(action_id)

    monkeypatch.setattr(route_module, "get_operator_action", _fake_get)
    client = _create_client()

    response = client.get(f"/api/v1/operator/actions/{action_id}", headers={"Authorization": "Bearer operator:human"})

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"


def test_list_endpoint_succeeds_with_operator_auth_and_passes_filters(monkeypatch) -> None:
    seen_kwargs: dict = {}

    async def _fake_list(*, db, action_type, status, limit):
        seen_kwargs.update(action_type=action_type, status=status, limit=limit)
        return [_action_view(uuid.uuid4())]

    monkeypatch.setattr(route_module, "list_operator_actions", _fake_list)
    client = _create_client()

    response = client.get(
        "/api/v1/operator/actions?action_type=RUN_CONTROLLED_PROOF&status=SUCCEEDED&limit=5",
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert seen_kwargs == {"action_type": "RUN_CONTROLLED_PROOF", "status": "SUCCEEDED", "limit": 5}


def test_list_endpoint_rejects_limit_above_server_maximum(monkeypatch) -> None:
    async def _unexpected(**kwargs):
        raise AssertionError("must not be called with an out-of-range limit")

    monkeypatch.setattr(route_module, "list_operator_actions", _unexpected)
    client = _create_client()

    response = client.get(
        "/api/v1/operator/actions?limit=99999", headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 422
