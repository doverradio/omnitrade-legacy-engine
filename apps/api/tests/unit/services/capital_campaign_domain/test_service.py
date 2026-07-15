from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.schemas.capital_campaign_domain import (
    CampaignCompoundingPolicy,
    CampaignProfitDistributionPolicy,
    CapitalCampaignDraftCreateRequest,
    CapitalCampaignPreviewRequest,
)
from app.services.capital_campaign_domain.service import create_campaign_draft, preview_campaign_definition


class _FakeDb:
    def __init__(self) -> None:
        self.commit_calls = 0
        self._runtime_campaigns = []

    def add(self, obj) -> None:
        self._runtime_campaigns.append(obj)

    async def flush(self) -> None:
        return None

    async def scalar(self, _statement):
        return None

    async def commit(self) -> None:
        self.commit_calls += 1


class _FakeRepository:
    def __init__(self, _db) -> None:
        self._store = {}

    async def next_version(self, *, campaign_id):
        existing = [key[1] for key in self._store if key[0] == campaign_id]
        return max(existing, default=0) + 1

    async def create(self, definition):
        key = (definition.campaign_id, definition.version)
        self._store[key] = definition
        return definition

    async def get(self, *, campaign_id, version=None):
        candidates = [value for (cid, _), value in self._store.items() if cid == campaign_id]
        if not candidates:
            return None
        if version is None:
            return sorted(candidates, key=lambda item: item.version, reverse=True)[0]
        for candidate in candidates:
            if candidate.version == version:
                return candidate
        return None


@pytest.mark.asyncio
async def test_valid_draft_campaign_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)

    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    result = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            name="Max Governed Campaign",
            description="non-live preview",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
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
            non_live_only=True,
        ),
    )

    assert result.version == 1
    assert result.status == "DRAFT"
    assert result.runtime_campaign_uuid == result.campaign_id
    assert result.runtime_definition_version == 1
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_versioned_campaign_definitions_increment(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)
    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    first = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            campaign_id=campaign_id,
            name="Campaign V1",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
            base_currency="USD",
            allowed_asset_classes=["crypto"],
            allowed_venues=["kraken_spot"],
            allowed_instruments=["BTC-USD"],
            campaign_modes=["OPPORTUNITY_SEEKING"],
            maximum_open_positions=1,
            maximum_position_size=Decimal("10"),
            minimum_position_size=Decimal("2"),
            maximum_total_exposure=Decimal("10"),
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
            non_live_only=True,
        ),
    )

    second = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            campaign_id=campaign_id,
            name="Campaign V2",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
            base_currency="USD",
            allowed_asset_classes=["crypto"],
            allowed_venues=["kraken_spot"],
            allowed_instruments=["BTC-USD"],
            campaign_modes=["OPPORTUNITY_SEEKING"],
            maximum_open_positions=1,
            maximum_position_size=Decimal("10"),
            minimum_position_size=Decimal("2"),
            maximum_total_exposure=Decimal("10"),
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
            non_live_only=True,
        ),
    )

    assert first.version == 1
    assert second.version == 2


@pytest.mark.asyncio
async def test_unsupported_instrument_rejected_on_draft_create(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)
    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError):
        await create_campaign_draft(
            db=db,
            request=CapitalCampaignDraftCreateRequest(
                name="Invalid Instruments",
                owner_identity="operator",
                status="DRAFT",
                capital_budget=Decimal("25"),
                base_currency="USD",
                allowed_asset_classes=["crypto"],
                allowed_venues=["kraken_spot"],
                allowed_instruments=["DOGE-USD"],
                campaign_modes=["OPPORTUNITY_SEEKING"],
                maximum_open_positions=1,
                maximum_position_size=Decimal("10"),
                minimum_position_size=Decimal("2"),
                maximum_total_exposure=Decimal("10"),
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
                non_live_only=True,
            ),
        )


def test_no_execution_side_effect_imports() -> None:
    root = Path(__file__).resolve().parents[4] / "app" / "services" / "capital_campaign_domain"
    source = "\n".join((root / name).read_text() for name in ["service.py", "preview_engine.py", "repository.py"])
    normalized = source.lower()

    assert "create_order" not in normalized
    assert "submit_order" not in normalized
    assert "addorder" not in normalized


def test_no_provider_order_calls() -> None:
    root = Path(__file__).resolve().parents[4] / "app" / "services" / "capital_campaign_domain"
    source = "\n".join((root / name).read_text() for name in ["service.py", "preview_engine.py", "repository.py"])
    normalized = source.lower()

    assert "exchange_connections.providers" not in normalized
    assert "kraken_spot" not in normalized
