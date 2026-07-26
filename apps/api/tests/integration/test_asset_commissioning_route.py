from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.routes import asset_commissioning as route_module
from app.db.session import get_db
from app.main import create_app


def _create_client() -> TestClient:
    app = create_app()

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=True)


def _preview_payload() -> dict:
    return {
        "provider": "kraken", "canonical_product_id": "SOL-USD", "provider_symbol": "SOL-USD",
        "provider_supported": True, "asset_registered": False, "asset_id": None, "candle_count": 0,
        "candle_count_required": 51, "market_data_current": False, "campaign_mutation_required": False,
        "mandate_successor_required": True, "preserved_risk_constraints": {"authorized_capital_usd": "25"},
        "runtime_discovery_mutation_required": True, "expected_changes": ["create Asset row"],
        "blockers": [], "plan": ["PROVIDER_VERIFIED: SOL-USD tradable"],
    }


def test_preview_endpoint_never_requires_auth_and_returns_plan(monkeypatch) -> None:
    async def _fake_preview(*, db, provider, product_id, campaign_id, environment):
        return _preview_payload()

    monkeypatch.setattr(route_module, "preview_asset_commissioning", _fake_preview)
    client = _create_client()

    response = client.post(
        "/operator/assets/commission/preview",
        json={"provider": "kraken", "product_id": "SOL-USD", "campaign_id": str(uuid.uuid4()), "environment": "production"},
    )

    assert response.status_code == 200
    assert response.json()["provider_supported"] is True
    assert response.json()["blockers"] == []


def test_commission_endpoint_requires_operator_auth(monkeypatch) -> None:
    async def _unexpected_commission(**kwargs):
        raise AssertionError("must not be called without authorization")

    monkeypatch.setattr(route_module, "commission_asset", _unexpected_commission)
    client = _create_client()

    response = client.post(
        "/operator/assets/commission",
        json={
            "provider": "kraken", "product_id": "SOL-USD", "campaign_id": str(uuid.uuid4()),
            "environment": "production", "activate": False, "idempotency_key": "test-key",
        },
    )

    assert response.status_code == 401


def test_commission_endpoint_succeeds_with_operator_auth(monkeypatch) -> None:
    commissioning_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    async def _fake_commission(*, db, provider, product_id, campaign_id, environment, activate, idempotency_key, actor):
        assert actor == "operator:human"
        return SimpleNamespaceRun(commissioning_id=commissioning_id, campaign_id=campaign_id)

    monkeypatch.setattr(route_module, "commission_asset", _fake_commission)
    client = _create_client()

    response = client.post(
        "/operator/assets/commission",
        json={
            "provider": "kraken", "product_id": "SOL-USD", "campaign_id": str(campaign_id),
            "environment": "production", "activate": False, "idempotency_key": "test-key",
        },
        headers={"Authorization": "Bearer operator:human"},
    )

    assert response.status_code == 201
    assert response.json()["commissioning_id"] == str(commissioning_id)
    assert response.json()["status"] == "IN_PROGRESS"


def test_readiness_endpoint_uses_configured_default_campaign_when_omitted(monkeypatch) -> None:
    captured = {}

    async def _fake_readiness(*, db, product_id, campaign_id):
        captured["campaign_id"] = campaign_id
        return {
            "product_id": product_id, "provider_supported": True, "asset_registered": True,
            "market_data_current": True, "candle_count": 60, "campaign_authorized": True,
            "mandate_authorized": True, "runtime_selected": True, "strategy_evaluation_observed": False,
            "live_execution_eligible": False, "blockers": ["no_strategy_roster_run_observed_for_this_asset_yet"],
            "warnings": [], "overall_status": "NOT_READY",
        }

    configured_campaign_id = uuid.uuid4()

    class _FakeSettings:
        automatic_mandate_package_activation_campaign_id = configured_campaign_id

    monkeypatch.setattr(route_module, "get_asset_readiness", _fake_readiness)
    monkeypatch.setattr(route_module, "get_settings", lambda: _FakeSettings())
    client = _create_client()

    response = client.get("/operator/assets/SOL-USD/readiness")

    assert response.status_code == 200
    assert captured["campaign_id"] == configured_campaign_id
    assert response.json()["overall_status"] == "NOT_READY"


def test_readiness_endpoint_without_campaign_id_or_default_is_a_clean_error(monkeypatch) -> None:
    class _FakeSettings:
        automatic_mandate_package_activation_campaign_id = None

    monkeypatch.setattr(route_module, "get_settings", lambda: _FakeSettings())
    client = _create_client()

    response = client.get("/operator/assets/SOL-USD/readiness")

    assert response.status_code == 400


class SimpleNamespaceRun:
    def __init__(self, *, commissioning_id, campaign_id) -> None:
        self.commissioning_id = commissioning_id
        self.provider = "kraken"
        self.product_id = "SOL-USD"
        self.campaign_id = campaign_id
        self.environment = "production"
        self.status = "IN_PROGRESS"
        self.stages = {}
        self.asset_id = None
        self.mandate_version_id = None
        self.failure_reason = None
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
