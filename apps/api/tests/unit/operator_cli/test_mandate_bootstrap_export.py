from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from tests.support.real_sqlite_session import real_sqlite_session

_TABLES = [CapitalCampaign.__table__, CapitalCampaignDefinition.__table__]


class _SessionContext:
    """Mirrors AsyncSessionLocal()'s async-context-manager shape but never closes the
    underlying session, matching the pattern used for mandate_bootstrap's own tests."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def __aenter__(self) -> AsyncSession:
        return self._db

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


async def _seed_campaign(db: AsyncSession, **overrides: Any) -> CapitalCampaign:
    defaults: dict[str, Any] = dict(
        id=2,
        owner="operator:owner",
        name="Campaign 2",
        campaign_type="crypto",
        exchange="kraken_spot:production",
        paper_account_id=None,
        strategy_id=None,
        definition_campaign_id=None,
        definition_version=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
    )
    defaults.update(overrides)
    campaign = CapitalCampaign(**defaults)
    db.add(campaign)
    await db.flush()
    await db.commit()
    return campaign


async def _seed_definition(db: AsyncSession, *, campaign_uuid: uuid.UUID, version: int, **overrides: Any) -> CapitalCampaignDefinition:
    defaults: dict[str, Any] = dict(
        campaign_id=campaign_uuid,
        name="Campaign 2 Definition",
        owner_identity="operator:owner",
        status="ACTIVE",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        base_currency="USD",
        allowed_asset_classes=["crypto"],
        allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"],
        maximum_open_positions=1,
        maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("1"),
        maximum_total_exposure=Decimal("10"),
        profitability_policy_id="pp-1",
        profitability_policy_version="1",
        risk_policy_id="rp-1",
        risk_policy_version="1",
        maximum_drawdown=Decimal("5"),
        version=version,
    )
    defaults.update(overrides)
    definition = CapitalCampaignDefinition(**defaults)
    db.add(definition)
    await db.flush()
    await db.commit()
    return definition


@pytest.mark.asyncio
async def test_campaign_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        assert result["capital_campaign_id"] == 999
        assert result["overall_status"] == "BLOCKED"
        assert result["executable"] is False
        assert result["campaign"] == {"found": False}
        assert result["definition"] is None
        for field in ("capital_campaign_id", "campaign_uuid", "paper_account_id", "base_currency"):
            assert result["fields"][field]["classification"] == "MISSING"
            assert result["fields"][field]["value"] is None


@pytest.mark.asyncio
async def test_campaign_found_without_definition_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_campaign(db, paper_account_id=uuid.uuid4())

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["overall_status"] == "BLOCKED"
        assert result["executable"] is False
        assert result["campaign"]["found"] is True
        assert result["campaign"]["id"] == 2
        assert result["campaign"]["uuid"] == str(campaign.uuid)
        assert result["campaign"]["name"] == "Campaign 2"
        assert result["campaign"]["exchange_label_raw"] == "kraken_spot:production"
        assert result["campaign"]["has_definition_pin"] is False
        assert result["campaign"]["definition_campaign_id"] is None
        assert result["campaign"]["definition_version"] is None
        assert result["definition"] is None

        assert result["fields"]["capital_campaign_id"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["capital_campaign_id"]["value"] == 2
        assert result["fields"]["campaign_uuid"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["campaign_uuid"]["value"] == str(campaign.uuid)
        assert result["fields"]["paper_account_id"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["paper_account_id"]["value"] == str(campaign.paper_account_id)
        assert result["fields"]["base_currency"]["classification"] == "MISSING"
        assert result["fields"]["base_currency"]["value"] is None
        assert "no definition" in result["fields"]["base_currency"]["notes"].lower()


@pytest.mark.asyncio
async def test_campaign_found_with_valid_definition_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_campaign(db)
        definition = await _seed_definition(db, campaign_uuid=campaign.uuid, version=1)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["campaign"]["has_definition_pin"] is True
        assert result["campaign"]["definition_campaign_id"] == str(campaign.uuid)
        assert result["campaign"]["definition_version"] == 1

        assert result["definition"]["found"] is True
        assert result["definition"]["status"] == "ACTIVE"
        assert result["definition"]["base_currency"] == "USD"
        assert result["definition"]["allowed_asset_classes"] == ["crypto"]
        assert result["definition"]["allowed_venues"] == ["kraken_spot"]
        assert result["definition"]["allowed_instruments"] == ["BTC-USD"]
        assert result["definition"]["maximum_open_positions"] == 1
        assert result["definition"]["maximum_position_size"] == str(definition.maximum_position_size)
        assert result["definition"]["maximum_total_exposure"] == str(definition.maximum_total_exposure)
        assert result["definition"]["maximum_drawdown"] == str(definition.maximum_drawdown)
        assert result["definition"]["informational_only"] is True
        assert "not automatically substitutable" in result["definition"]["notes"]
        assert "max_order_notional_usd" in result["definition"]["notes"]

        assert result["fields"]["base_currency"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["base_currency"]["value"] == "USD"


@pytest.mark.asyncio
async def test_campaign_found_with_dangling_definition_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin pointing at a definition row that doesn't exist must fail closed (MISSING),
    never silently proceed or guess a substitute value."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_campaign(db)
        # ck_capital_campaigns_definition_pin_identity requires definition_campaign_id ==
        # the campaign's own uuid when set; the dangle is version 99 having no matching
        # capital_campaign_definitions row, not an unrelated campaign identity.
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 99
        await db.commit()

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["campaign"]["has_definition_pin"] is True
        assert result["definition"]["found"] is False
        assert result["fields"]["base_currency"]["classification"] == "MISSING"
        assert result["fields"]["base_currency"]["value"] is None


@pytest.mark.asyncio
async def test_deterministic_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_campaign(db, paper_account_id=uuid.uuid4())

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert set(first.keys()) == {
            "capital_campaign_id",
            "resolved_at",
            "overall_status",
            "executable",
            "campaign",
            "definition",
            "fields",
        }
        assert set(first["fields"].keys()) == {"capital_campaign_id", "campaign_uuid", "paper_account_id", "base_currency"}
        for field_payload in first["fields"].values():
            assert set(field_payload.keys()) == {"classification", "value", "source", "notes"}
            assert field_payload["classification"] in {
                "DATABASE_DERIVED",
                "CONFIGURATION_DERIVED",
                "OWNER_INPUT_REQUIRED",
                "MISSING",
                "CONFLICTING",
            }

        first_without_timestamp = {k: v for k, v in first.items() if k != "resolved_at"}
        second_without_timestamp = {k: v for k, v in second.items() if k != "resolved_at"}
        assert first_without_timestamp == second_without_timestamp


@pytest.mark.asyncio
async def test_executable_is_always_false(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        not_found = await service.mandate_bootstrap_export(capital_campaign_id=999)
        assert not_found["executable"] is False
        assert not_found["overall_status"] == "BLOCKED"

        await _seed_campaign(db, paper_account_id=uuid.uuid4())
        campaign = await db.get(CapitalCampaign, 2)
        await _seed_definition(db, campaign_uuid=campaign.uuid, version=1)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()

        fully_resolved = await service.mandate_bootstrap_export(capital_campaign_id=2)
        # Even when every Stage-1 field resolves, executable must still be false: this
        # command can never supply owner_acknowledgements/authorization_evidence/etc.,
        # so "executable" is a hardcoded guarantee, not a computed function of field state.
        assert fully_resolved["executable"] is False
        assert fully_resolved["overall_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_no_database_mutation_occurs(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        campaign = await _seed_campaign(db, paper_account_id=uuid.uuid4())
        definition = await _seed_definition(db, campaign_uuid=campaign.uuid, version=1)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["campaign"]["found"] is True
        assert result["definition"]["found"] is True
        assert result["definition"]["base_currency"] == definition.base_currency
