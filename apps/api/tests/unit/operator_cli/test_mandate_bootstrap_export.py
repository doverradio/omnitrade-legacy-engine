from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from tests.support.real_sqlite_session import real_sqlite_session

_TABLES = [
    CapitalCampaign.__table__,
    CapitalCampaignDefinition.__table__,
    PaperAccount.__table__,
    LiveTradingProfile.__table__,
    ExchangeConnection.__table__,
]

# Fields that must never appear anywhere in mandate-bootstrap-export output.
# "name" is deliberately excluded from this blanket check: CapitalCampaign.name is a
# legitimate Stage 1 field. PaperAccount.name's absence is instead proven by the exact
# dict-equality assertions in test_paper_account_found_and_active/_but_inactive below.
_FORBIDDEN_KEYS = {
    "starting_balance",
    "current_cash_balance",
    "owner_user_id",
    "credentials_encrypted",
    "api_key_masked",
    "api_secret_masked",
    "passphrase_configured",
    "balances",
    "total_equity_usd",
    "account_status",
    "last_api_error",
}


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
        exchange="kraken_spot",
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


async def _seed_paper_account(db: AsyncSession, **overrides: Any) -> PaperAccount:
    defaults: dict[str, Any] = dict(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Campaign 2 Paper Account",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("18.42"),
        is_active=True,
    )
    defaults.update(overrides)
    account = PaperAccount(**defaults)
    db.add(account)
    await db.flush()
    await db.commit()
    return account


async def _seed_live_trading_profile(db: AsyncSession, *, paper_account_id: uuid.UUID, **overrides: Any) -> LiveTradingProfile:
    defaults: dict[str, Any] = dict(
        id=uuid.uuid4(),
        paper_account_id=paper_account_id,
        provenance_metadata={},
    )
    defaults.update(overrides)
    profile = LiveTradingProfile(**defaults)
    db.add(profile)
    await db.flush()
    await db.commit()
    return profile


async def _seed_exchange_connection(db: AsyncSession, **overrides: Any) -> ExchangeConnection:
    defaults: dict[str, Any] = dict(
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        connection_name="kraken-campaign-2",
        environment="production",
        status="connected",
        credentials_encrypted="encrypted-blob",
        api_key_masked="****1234",
        api_secret_masked="****5678",
        credentials_valid=True,
        api_permissions=["trade", "view"],
    )
    defaults.update(overrides)
    connection = ExchangeConnection(**defaults)
    db.add(connection)
    await db.flush()
    await db.commit()
    return connection


def _assert_no_forbidden_keys(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in _FORBIDDEN_KEYS, f"forbidden key {key!r} present in output"
            _assert_no_forbidden_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_forbidden_keys(item)


# ---------------------------------------------------------------------------
# Stage 1 (campaign + definition) -- updated to seed a real paper account so
# these tests exercise the "found" paper-account/live-trading-profile path
# rather than incidentally depending on Stage 2 dangling-reference behavior.
# ---------------------------------------------------------------------------


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
        assert result["paper_account"] is None
        assert result["live_trading_profile"] is None
        assert result["live_trading_profile_candidates"] == []
        assert result["exchange_connection"] is None
        assert result["exchange_connection_candidates"] == []
        for field in (
            "capital_campaign_id",
            "campaign_uuid",
            "paper_account_id",
            "base_currency",
            "paper_account_asset_class",
            "paper_account_is_active",
            "live_trading_profile_id",
            "exchange_connection_id",
            "exchange_provider",
            "exchange_environment",
        ):
            assert result["fields"][field]["classification"] == "MISSING"
            assert result["fields"][field]["value"] is None


@pytest.mark.asyncio
async def test_campaign_found_without_definition_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        campaign = await _seed_campaign(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["overall_status"] == "BLOCKED"
        assert result["executable"] is False
        assert result["campaign"]["found"] is True
        assert result["campaign"]["id"] == 2
        assert result["campaign"]["uuid"] == str(campaign.uuid)
        assert result["campaign"]["name"] == "Campaign 2"
        assert result["campaign"]["exchange_label_raw"] == "kraken_spot"
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
        paper_account = await _seed_paper_account(db)
        campaign = await _seed_campaign(db, paper_account_id=paper_account.id)
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
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert set(first.keys()) == {
            "capital_campaign_id",
            "resolved_at",
            "overall_status",
            "executable",
            "campaign",
            "definition",
            "paper_account",
            "live_trading_profile",
            "live_trading_profile_candidates",
            "exchange_connection",
            "exchange_connection_candidates",
            "fields",
        }
        assert set(first["fields"].keys()) == {
            "capital_campaign_id",
            "campaign_uuid",
            "paper_account_id",
            "base_currency",
            "paper_account_asset_class",
            "paper_account_is_active",
            "live_trading_profile_id",
            "exchange_connection_id",
            "exchange_provider",
            "exchange_environment",
        }
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

        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        campaign = await db.get(CapitalCampaign, 2)
        await _seed_definition(db, campaign_uuid=campaign.uuid, version=1)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()
        await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

        fully_resolved = await service.mandate_bootstrap_export(capital_campaign_id=2)
        # Even when every Stage-1/Stage-2 field resolves uniquely, executable must still
        # be false: this command can never supply owner_acknowledgements/
        # authorization_evidence/etc., so "executable" is a hardcoded guarantee, not a
        # computed function of field state.
        assert fully_resolved["fields"]["live_trading_profile_id"]["classification"] == "DATABASE_DERIVED"
        assert fully_resolved["executable"] is False
        assert fully_resolved["overall_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_no_database_mutation_occurs(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        paper_account = await _seed_paper_account(db)
        campaign = await _seed_campaign(db, paper_account_id=paper_account.id)
        definition = await _seed_definition(db, campaign_uuid=campaign.uuid, version=1)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()
        await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

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
        assert result["live_trading_profile"]["found"] is True


# ---------------------------------------------------------------------------
# Stage 2 (paper account + live trading profile resolution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_paper_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """campaign.paper_account_id set but no matching paper_accounts row -- must fail
    closed (MISSING), never crash and never guess."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        dangling_id = uuid.uuid4()
        await _seed_campaign(db, paper_account_id=dangling_id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["paper_account"] == {
            "found": False,
            "notes": "capital_campaigns.paper_account_id is set but no matching paper_accounts row exists.",
        }
        assert result["fields"]["paper_account_asset_class"]["classification"] == "MISSING"
        assert result["fields"]["paper_account_is_active"]["classification"] == "MISSING"
        assert result["live_trading_profile"] is None
        assert result["live_trading_profile_candidates"] == []
        assert result["fields"]["live_trading_profile_id"]["classification"] == "MISSING"


@pytest.mark.asyncio
async def test_paper_account_found_and_active(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db, asset_class="crypto", is_active=True)
        await _seed_campaign(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["paper_account"] == {
            "found": True,
            "id": str(paper_account.id),
            "asset_class": "crypto",
            "is_active": True,
        }
        assert result["fields"]["paper_account_asset_class"] == {
            "classification": "DATABASE_DERIVED",
            "value": "crypto",
            "source": "paper_accounts.asset_class",
            "notes": None,
        }
        assert result["fields"]["paper_account_is_active"] == {
            "classification": "DATABASE_DERIVED",
            "value": True,
            "source": "paper_accounts.is_active",
            "notes": None,
        }


@pytest.mark.asyncio
async def test_paper_account_found_but_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db, is_active=False)
        await _seed_campaign(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["paper_account"]["is_active"] is False
        assert result["fields"]["paper_account_is_active"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["paper_account_is_active"]["value"] is False


@pytest.mark.asyncio
async def test_zero_matching_live_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        # A profile bound to a *different* paper account must never be treated as a match.
        other_paper_account = await _seed_paper_account(db)
        await _seed_live_trading_profile(db, paper_account_id=other_paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["live_trading_profile"] is None
        assert result["live_trading_profile_candidates"] == []
        assert result["fields"]["live_trading_profile_id"]["classification"] == "MISSING"
        assert result["fields"]["live_trading_profile_id"]["value"] is None


@pytest.mark.asyncio
async def test_exactly_one_matching_live_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        profile = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["live_trading_profile_id"] == {
            "classification": "DATABASE_DERIVED",
            "value": str(profile.id),
            "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
            "notes": None,
        }
        assert result["live_trading_profile"] == {
            "found": True,
            "id": str(profile.id),
            "paper_account_id": str(paper_account.id),
            "operating_mode": "paper",
            "lifecycle_state": "draft",
            "approval_state": "not_requested",
            "live_opt_in": False,
            "human_approval_recorded": False,
            "governance_approved": False,
            "risk_authority_model": "risk_engine_final",
            "autonomous_capital_allocation": False,
            "autonomous_strategy_evolution": False,
            "automatic_promotion_enabled": False,
        }
        assert result["live_trading_profile_candidates"] == [
            {
                "id": str(profile.id),
                "operating_mode": "paper",
                "lifecycle_state": "draft",
                "approval_state": "not_requested",
            }
        ]


@pytest.mark.asyncio
async def test_multiple_matching_live_profiles_fail_closed_as_conflicting(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        profile_a = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
        profile_b = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["live_trading_profile_id"]["classification"] == "CONFLICTING"
        assert result["fields"]["live_trading_profile_id"]["value"] is None
        assert "2 live_trading_profiles rows" in result["fields"]["live_trading_profile_id"]["notes"]
        assert result["live_trading_profile"] is None

        expected_ids = sorted([str(profile_a.id), str(profile_b.id)])
        actual_ids = sorted(candidate["id"] for candidate in result["live_trading_profile_candidates"])
        assert actual_ids == expected_ids
        assert len(result["live_trading_profile_candidates"]) == 2


@pytest.mark.asyncio
async def test_deterministic_candidate_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate order must be a stable function of primary key, not creation order or
    'newest first' -- proven by seeding in reverse-id order and asserting ascending output."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)

        low_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        high_id = uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe")
        # Seed the higher id first so "creation order" and "id order" disagree.
        await _seed_live_trading_profile(db, id=high_id, paper_account_id=paper_account.id)
        await _seed_live_trading_profile(db, id=low_id, paper_account_id=paper_account.id)

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        candidate_ids_first = [c["id"] for c in first["live_trading_profile_candidates"]]
        candidate_ids_second = [c["id"] for c in second["live_trading_profile_candidates"]]
        assert candidate_ids_first == candidate_ids_second
        assert candidate_ids_first == sorted(candidate_ids_first)


@pytest.mark.asyncio
async def test_no_financial_balances_or_owner_user_id_appear_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(
            db, starting_balance=Decimal("999999"), current_cash_balance=Decimal("123456.78")
        )
        await _seed_campaign(db, paper_account_id=paper_account.id)
        await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
        # A second, conflicting profile too, to also cover the candidate-listing path.
        other_paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, id=3, paper_account_id=other_paper_account.id)
        await _seed_live_trading_profile(db, paper_account_id=other_paper_account.id)
        await _seed_live_trading_profile(db, paper_account_id=other_paper_account.id)

        result_unique = await service.mandate_bootstrap_export(capital_campaign_id=2)
        result_conflicting = await service.mandate_bootstrap_export(capital_campaign_id=3)

        _assert_no_forbidden_keys(result_unique)
        _assert_no_forbidden_keys(result_conflicting)

        # Also assert the actual sensitive values never appear anywhere in the payload,
        # not just that the key names are absent.
        serialized_unique = repr(result_unique)
        assert "999999" not in serialized_unique
        assert "123456.78" not in serialized_unique
        assert str(paper_account.owner_user_id) not in serialized_unique


@pytest.mark.asyncio
async def test_no_database_mutation_occurs_stage2(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        profile_a = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
        await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["live_trading_profile_id"]["classification"] == "CONFLICTING"
        assert len(result["live_trading_profile_candidates"]) == 2
        _ = profile_a  # seeded only to establish the conflicting pair


@pytest.mark.asyncio
async def test_executable_remains_false_with_stage2_data_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id)
        await _seed_live_trading_profile(db, paper_account_id=paper_account.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["live_trading_profile_id"]["classification"] == "DATABASE_DERIVED"
        assert result["executable"] is False
        assert result["overall_status"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Stage 3 (exchange connection resolution)
# ---------------------------------------------------------------------------


async def _seed_resolved_chain(db: AsyncSession, *, campaign_id: int = 2, exchange: str = "kraken_spot") -> tuple[CapitalCampaign, PaperAccount, LiveTradingProfile]:
    """Seeds a campaign whose paper account and live trading profile both resolve
    uniquely -- the precondition Stage 3 requires before it will even attempt exchange
    connection resolution."""
    paper_account = await _seed_paper_account(db)
    campaign = await _seed_campaign(db, id=campaign_id, paper_account_id=paper_account.id, exchange=exchange)
    profile = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
    return campaign, paper_account, profile


@pytest.mark.asyncio
async def test_zero_exchange_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["exchange_provider"] == {
            "classification": "DATABASE_DERIVED",
            "value": "kraken_spot",
            "source": "capital_campaigns.exchange (parsed)",
            "notes": None,
        }
        assert result["fields"]["exchange_environment"]["value"] == "production"
        assert result["fields"]["exchange_connection_id"]["classification"] == "MISSING"
        assert result["fields"]["exchange_connection_id"]["value"] is None
        assert result["exchange_connection"] is None
        assert result["exchange_connection_candidates"] == []


@pytest.mark.asyncio
async def test_exchange_connection_not_attempted_without_unique_live_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching exchange_connections row must still be ignored if the live trading
    profile itself did not resolve uniquely -- proves the resolution is gated on the
    live trading profile, not just on the parsed provider/environment."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, paper_account_id=paper_account.id, exchange="kraken_spot")
        # Zero live trading profiles -> live_trading_profile_id is MISSING, not resolved.
        await _seed_exchange_connection(db, provider="kraken_spot", environment="production")

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["live_trading_profile_id"]["classification"] == "MISSING"
        assert result["fields"]["exchange_connection_id"]["classification"] == "MISSING"
        assert "live_trading_profile_id is not uniquely resolved" in result["fields"]["exchange_connection_id"]["notes"]
        assert result["exchange_connection"] is None
        assert result["exchange_connection_candidates"] == []


@pytest.mark.asyncio
async def test_exactly_one_exchange_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)
        connection = await _seed_exchange_connection(
            db, provider="kraken_spot", environment="production", status="connected", credentials_valid=True, api_permissions=["trade", "view"]
        )
        # A same-provider, different-environment row must never match (never infer by provider alone).
        await _seed_exchange_connection(db, provider="kraken_spot", environment="sandbox")
        # A different-provider row at the same environment must never match either.
        await _seed_exchange_connection(db, provider="coinbase_advanced", environment="production")

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["exchange_connection_id"] == {
            "classification": "DATABASE_DERIVED",
            "value": str(connection.exchange_connection_id),
            "source": "exchange_connections WHERE provider = ? AND environment = ?",
            "notes": None,
        }
        assert result["exchange_connection"] == {
            "found": True,
            "id": str(connection.exchange_connection_id),
            "provider": "kraken_spot",
            "environment": "production",
            "connection_status": "connected",
            "authentication_state": True,
            "capability_profile": ["trade", "view"],
            "trading_enabled": None,
            "withdrawals_enabled": None,
            "supports_market_orders": None,
            "supports_limit_orders": None,
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_CAPABILITY_FLAGS_NOTES,
        }
        assert result["exchange_connection_candidates"] == [
            {
                "id": str(connection.exchange_connection_id),
                "provider": "kraken_spot",
                "environment": "production",
                "connection_status": "connected",
            }
        ]


@pytest.mark.asyncio
async def test_conflicting_exchange_connections_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)
        connection_a = await _seed_exchange_connection(db, provider="kraken_spot", environment="production")
        connection_b = await _seed_exchange_connection(db, provider="kraken_spot", environment="production")

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["exchange_connection_id"]["classification"] == "CONFLICTING"
        assert result["fields"]["exchange_connection_id"]["value"] is None
        assert "2 exchange_connections rows" in result["fields"]["exchange_connection_id"]["notes"]
        assert result["exchange_connection"] is None

        expected_ids = sorted([str(connection_a.exchange_connection_id), str(connection_b.exchange_connection_id)])
        actual_ids = sorted(candidate["id"] for candidate in result["exchange_connection_candidates"])
        assert actual_ids == expected_ids
        assert len(result["exchange_connection_candidates"]) == 2


@pytest.mark.asyncio
async def test_deterministic_exchange_connection_candidate_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate order must be a stable function of primary key, not creation order or
    'newest first' -- proven by seeding in reverse-id order and asserting stable output."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)

        low_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        high_id = uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe")
        # Seed the higher id first so "creation order" and "id order" disagree.
        await _seed_exchange_connection(db, exchange_connection_id=high_id, provider="kraken_spot", environment="production")
        await _seed_exchange_connection(db, exchange_connection_id=low_id, provider="kraken_spot", environment="production")

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        candidate_ids_first = [c["id"] for c in first["exchange_connection_candidates"]]
        candidate_ids_second = [c["id"] for c in second["exchange_connection_candidates"]]
        assert candidate_ids_first == candidate_ids_second
        assert candidate_ids_first == sorted(candidate_ids_first)


@pytest.mark.asyncio
async def test_exchange_connection_output_is_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)
        await _seed_exchange_connection(
            db,
            provider="kraken_spot",
            environment="production",
            credentials_encrypted="TOP-SECRET-ENCRYPTED-BLOB",
            api_key_masked="sk-live-abcdef",
            api_secret_masked="secret-xyz",
            passphrase_configured=True,
        )
        # A conflicting second connection too, to cover the candidate-listing path.
        other_paper_account = await _seed_paper_account(db)
        await _seed_campaign(db, id=3, paper_account_id=other_paper_account.id, exchange="coinbase_advanced_sandbox")
        await _seed_live_trading_profile(db, paper_account_id=other_paper_account.id)
        await _seed_exchange_connection(
            db,
            provider="coinbase_advanced",
            environment="sandbox",
            credentials_encrypted="ANOTHER-SECRET",
            api_key_masked="sk-live-999",
            api_secret_masked="secret-999",
        )
        await _seed_exchange_connection(
            db,
            provider="coinbase_advanced",
            environment="sandbox",
            credentials_encrypted="YET-ANOTHER-SECRET",
            api_key_masked="sk-live-000",
            api_secret_masked="secret-000",
        )

        result_unique = await service.mandate_bootstrap_export(capital_campaign_id=2)
        result_conflicting = await service.mandate_bootstrap_export(capital_campaign_id=3)

        _assert_no_forbidden_keys(result_unique)
        _assert_no_forbidden_keys(result_conflicting)

        for payload in (result_unique, result_conflicting):
            serialized = repr(payload)
            assert "TOP-SECRET-ENCRYPTED-BLOB" not in serialized
            assert "sk-live-abcdef" not in serialized
            assert "secret-xyz" not in serialized
            assert "ANOTHER-SECRET" not in serialized
            assert "YET-ANOTHER-SECRET" not in serialized
            assert "sk-live-999" not in serialized
            assert "sk-live-000" not in serialized


@pytest.mark.asyncio
async def test_no_database_mutation_occurs_stage3(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        await _seed_resolved_chain(db)
        connection = await _seed_exchange_connection(db, provider="kraken_spot", environment="production")

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["exchange_connection_id"]["classification"] == "DATABASE_DERIVED"
        assert result["fields"]["exchange_connection_id"]["value"] == str(connection.exchange_connection_id)


@pytest.mark.asyncio
async def test_executable_remains_false_with_stage3_data_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_resolved_chain(db)
        await _seed_exchange_connection(db, provider="kraken_spot", environment="production")

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["exchange_connection_id"]["classification"] == "DATABASE_DERIVED"
        assert result["executable"] is False
        assert result["overall_status"] == "BLOCKED"
