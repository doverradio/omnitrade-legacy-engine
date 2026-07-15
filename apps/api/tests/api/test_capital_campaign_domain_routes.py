from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.schemas.capital_campaign_domain import (
    CampaignAccountingState,
    CampaignCompoundingPolicy,
    CampaignProfitDistributionPolicy,
    CapitalCampaignDefinitionListResponse,
    CapitalCampaignDefinitionResponse,
    CapitalCampaignPreviewResponse,
)


def _campaign_response() -> CapitalCampaignDefinitionResponse:
    return CapitalCampaignDefinitionResponse(
        campaign_id=UUID("11111111-1111-1111-1111-111111111111"),
        version=1,
        runtime_campaign_uuid=UUID("11111111-1111-1111-1111-111111111111"),
        runtime_definition_version=1,
        name="Maximum Governed Crypto Growth",
        description="preview campaign",
        owner_identity="operator",
        status="DRAFT",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        base_currency="USD",
        allowed_asset_classes=["crypto"],
        allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        campaign_modes=["OPPORTUNITY_SEEKING"],
        maximum_open_positions=2,
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        profitability_policy_id="pfp-1.1",
        profitability_policy_version="1.0.0",
        risk_policy_id="risk-v1",
        risk_policy_version="1.0.0",
        compounding_policy=CampaignCompoundingPolicy(
            policy_type="REINVEST_PERCENTAGE",
            reinvestment_percentage=Decimal("50"),
            profit_distribution_percentage=Decimal("30"),
            reserve_percentage=Decimal("20"),
            cumulative_profit_target=Decimal("20"),
            maximum_campaign_loss=Decimal("5"),
            campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        ),
        profit_distribution_policy=CampaignProfitDistributionPolicy(
            reinvestment_percentage=Decimal("50"),
            profit_distribution_percentage=Decimal("30"),
            reserve_percentage=Decimal("20"),
        ),
        aggression_mode="BALANCED",
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("5"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("0"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("25"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("25"),
        ),
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        activated_at=None,
        paused_at=None,
        completed_at=None,
        metadata_evidence={"source": "test"},
    )


def _preview_response() -> CapitalCampaignPreviewResponse:
    return CapitalCampaignPreviewResponse(
        campaign_id=UUID("11111111-1111-1111-1111-111111111111"),
        campaign_version=1,
        aggression_mode="BALANCED",
        no_action=True,
        no_action_reason="no_opportunity_meets_fee_adjusted_policy_and_risk_requirements",
        proposed_opportunities=[],
        rejected_opportunities=[],
        remaining_cash=Decimal("25"),
        expected_fees=Decimal("0"),
        expected_slippage=Decimal("0"),
        expected_net_edge=Decimal("0"),
        campaign_policy_checks=["ok"],
        risk_checks=["ok"],
        decision_evidence={"sample": True},
        evaluated_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


def _client() -> TestClient:
    app = create_app()

    async def _override_db():
        yield object()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_domain_routes_shape(monkeypatch) -> None:
    async def _create_stub(*_args, **_kwargs):
        return _campaign_response()

    async def _get_stub(*_args, **_kwargs):
        return _campaign_response()

    async def _list_stub(*_args, **_kwargs):
        return CapitalCampaignDefinitionListResponse(items=[_campaign_response()])

    async def _preview_stub(*_args, **_kwargs):
        return _preview_response()

    monkeypatch.setattr("app.api.routes.capital_campaigns.create_campaign_draft", _create_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.get_campaign_definition", _get_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.list_campaign_definitions", _list_stub)
    monkeypatch.setattr("app.api.routes.capital_campaigns.preview_campaign_definition", _preview_stub)

    with _client() as client:
        created = client.post(
            "/capital-campaigns/domain/drafts",
            json={
                "name": "Campaign",
                "owner_identity": "operator",
                "status": "DRAFT",
                "capital_budget": "25",
                "base_currency": "USD",
                "allowed_asset_classes": ["crypto"],
                "allowed_venues": ["kraken_spot"],
                "allowed_instruments": ["BTC-USD"],
                "campaign_modes": ["OPPORTUNITY_SEEKING"],
                "maximum_open_positions": 1,
                "maximum_position_size": "10",
                "minimum_position_size": "2",
                "maximum_total_exposure": "10",
                "profitability_policy_id": "pfp-1.1",
                "profitability_policy_version": "1.0.0",
                "risk_policy_id": "risk-v1",
                "risk_policy_version": "1.0.0",
                "compounding_policy": {
                    "policy_type": "REINVEST_PERCENTAGE",
                    "reinvestment_percentage": "50",
                    "profit_distribution_percentage": "30",
                    "reserve_percentage": "20",
                    "cumulative_profit_target": "20",
                    "maximum_campaign_loss": "5",
                    "campaign_end_date": "2026-12-31T00:00:00+00:00"
                },
                "profit_distribution_policy": {
                    "reinvestment_percentage": "50",
                    "profit_distribution_percentage": "30",
                    "reserve_percentage": "20"
                },
                "aggression_mode": "BALANCED",
                "non_live_only": True,
            },
        )
        assert created.status_code == 201

        listing = client.get("/capital-campaigns/domain")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["name"] == "Maximum Governed Crypto Growth"

        detail = client.get("/capital-campaigns/domain/11111111-1111-1111-1111-111111111111")
        assert detail.status_code == 200
        assert detail.json()["campaign_id"] == "11111111-1111-1111-1111-111111111111"

        preview = client.post(
            "/capital-campaigns/domain/11111111-1111-1111-1111-111111111111/preview",
            json={
                "candidate_instruments": ["BTC-USD"],
                "strategy_evidence": [],
                "lifecycle_snapshots": [],
                "risk_preview": [],
            },
        )
        assert preview.status_code == 200

        explain = client.post(
            "/capital-campaigns/domain/11111111-1111-1111-1111-111111111111/preview/explain",
            json={
                "candidate_instruments": ["BTC-USD"],
                "strategy_evidence": [],
                "lifecycle_snapshots": [],
                "risk_preview": [],
            },
        )
        assert explain.status_code == 200


def test_domain_routes_reject_invalid_campaign_id() -> None:
    with _client() as client:
        response = client.get("/capital-campaigns/domain/not-a-uuid")
    assert response.status_code == 400
