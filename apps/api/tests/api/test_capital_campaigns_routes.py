from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.db.session import get_db
from app.main import create_app
from app.schemas.capital_campaigns import CapitalCampaignResponse


def _campaign_response(campaign_uuid: str = "11111111-1111-1111-1111-111111111111") -> CapitalCampaignResponse:
    return CapitalCampaignResponse(
        id=1,
        uuid=uuid.UUID(campaign_uuid),
        owner="owner-1",
        name="Campaign A",
        description="Foundation campaign",
        status="RUNNING",
        campaign_type="paper_validation",
        exchange="coinbase_advanced",
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("26"),
        realized_profit=Decimal("1"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0.1"),
        roi=Decimal("4"),
        created_at=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc),
    )


def _create_client() -> TestClient:
    app = create_app()

    async def _override_db():
        yield object()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_capital_campaign_routes_shape(monkeypatch) -> None:
    async def _list_stub(*_args, **_kwargs):
        return [_campaign_response()]

    async def _create_stub(*_args, **_kwargs):
        return _campaign_response("22222222-2222-2222-2222-222222222222")

    async def _get_stub(*_args, **_kwargs):
        return _campaign_response()

    async def _update_stub(*_args, **_kwargs):
        return _campaign_response()

    async def _delete_stub(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.routes.capital_campaigns.list_capital_campaigns", _list_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.create_capital_campaign", _create_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.get_capital_campaign", _get_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.update_capital_campaign", _update_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.delete_capital_campaign", _delete_stub)

    with _create_client() as client:
        listing = client.get("/capital-campaigns")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["name"] == "Campaign A"

        created = client.post(
            "/capital-campaigns",
            json={
                "owner": "owner-1",
                "name": "Campaign A",
                "campaign_type": "paper_validation",
                "starting_capital": "25",
            },
        )
        assert created.status_code == 201

        detail = client.get("/capital-campaigns/11111111-1111-1111-1111-111111111111")
        assert detail.status_code == 200
        assert detail.json()["status"] == "RUNNING"

        patched = client.patch(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111",
            json={"status": "PAUSED"},
        )
        assert patched.status_code == 200

        deleted = client.delete("/capital-campaigns/11111111-1111-1111-1111-111111111111")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_capital_campaign_route_rejects_invalid_uuid() -> None:
    with _create_client() as client:
        response = client.get("/capital-campaigns/not-a-uuid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_capital_campaign_route_returns_404_when_missing(monkeypatch) -> None:
    async def _missing_stub(*_args, **_kwargs):
        raise NotFoundError(message="Capital campaign not found", details={})

    monkeypatch.setattr("app.api.routes.capital_campaigns.get_capital_campaign", _missing_stub)

    with _create_client() as client:
        response = client.get("/capital-campaigns/11111111-1111-1111-1111-111111111111")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_capital_campaign_route_rejects_invalid_status_payload() -> None:
    with _create_client() as client:
        response = client.patch(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111",
            json={"status": "NOT_A_STATUS"},
        )

    assert response.status_code == 422
