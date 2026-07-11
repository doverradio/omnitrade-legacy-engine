from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.schemas.capital_campaign_profit import CapitalCampaignProfitCycleResponse, CapitalCampaignProfitPolicyResponse


def _policy_response() -> CapitalCampaignProfitPolicyResponse:
    return CapitalCampaignProfitPolicyResponse(
        policy_id=1,
        policy_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        capital_campaign_id=1,
        policy_type="FULL_COMPOUND",
        profit_target_amount=Decimal("5"),
        profit_target_percent=None,
        compound_percent=Decimal("100"),
        withdraw_percent=Decimal("0"),
        protected_principal_amount=None,
        minimum_realized_profit=Decimal("0"),
        maximum_campaign_capital=None,
        minimum_cash_reserve=Decimal("0"),
        fee_reserve_percent=Decimal("0"),
        tax_reserve_percent=Decimal("0"),
        cooldown_hours=0,
        require_operator_approval=True,
        is_active=True,
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )


def _cycle_response() -> CapitalCampaignProfitCycleResponse:
    return CapitalCampaignProfitCycleResponse(
        cycle_id=1,
        cycle_uuid=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        capital_campaign_id=1,
        profit_policy_id=1,
        cycle_number=1,
        opening_capital=Decimal("25"),
        opening_equity=Decimal("30"),
        realized_profit=Decimal("5"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        eligible_profit=Decimal("5"),
        compound_amount=Decimal("5"),
        withdrawal_amount=Decimal("0"),
        reserve_amount=Decimal("0"),
        closing_campaign_capital=Decimal("30"),
        target_reached=True,
        status="REVIEW_REQUIRED",
        settlement_state="SETTLEMENT_UNKNOWN",
        calculation_snapshot={"explanation": "Accounting recommendation only"},
        calculated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        approved_at=None,
        completed_at=None,
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )


def _create_client() -> TestClient:
    app = create_app()

    async def _override_db():
        yield object()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_profit_policy_and_cycle_routes(monkeypatch) -> None:
    async def _policy_stub(*_args, **_kwargs):
        return _policy_response()

    async def _evaluate_stub(*_args, **_kwargs):
        return _cycle_response()

    async def _list_cycles_stub(*_args, **_kwargs):
        return [_cycle_response()]

    async def _cycle_stub(*_args, **_kwargs):
        return _cycle_response()

    monkeypatch.setattr("app.api.routes.capital_campaigns.upsert_profit_policy", _policy_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.get_active_profit_policy", _policy_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.evaluate_profit_cycle", _evaluate_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.list_profit_cycles", _list_cycles_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.get_profit_cycle", _cycle_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.approve_profit_cycle", _cycle_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.reject_profit_cycle", _cycle_stub)

    with _create_client() as client:
        policy_post = client.post(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-policy",
            json={"policy_type": "FULL_COMPOUND"},
        )
        assert policy_post.status_code == 200

        policy_get = client.get("/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-policy")
        assert policy_get.status_code == 200

        evaluate = client.post(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-cycles/evaluate",
            json={"force_new_cycle": False, "actor": "operator"},
        )
        assert evaluate.status_code == 200

        listing = client.get("/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-cycles")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["status"] == "REVIEW_REQUIRED"

        detail = client.get("/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-cycles/22222222-2222-2222-2222-222222222222")
        assert detail.status_code == 200

        approve = client.post(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-cycles/22222222-2222-2222-2222-222222222222/approve",
            json={"actor": "operator"},
        )
        assert approve.status_code == 200

        reject = client.post(
            "/capital-campaigns/11111111-1111-1111-1111-111111111111/profit-cycles/22222222-2222-2222-2222-222222222222/reject",
            json={"actor": "operator", "reason": "manual reject"},
        )
        assert reject.status_code == 200


def test_profit_policy_route_rejects_invalid_campaign_uuid() -> None:
    with _create_client() as client:
        response = client.get("/capital-campaigns/not-a-uuid/profit-policy")
    assert response.status_code == 400
