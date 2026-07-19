from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_TABLES = [
    CapitalCampaign.__table__,
    CapitalCampaignDefinition.__table__,
    PaperAccount.__table__,
    LiveTradingProfile.__table__,
    ExchangeConnection.__table__,
    Strategy.__table__,
    CanonicalPreviewPackage.__table__,
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

# The Stage 1-4 identity-chain/strategy-evidence fields, kept separate from Stage 5's
# static owner-input manifest (service._MANDATE_BOOTSTRAP_EXPORT_STATIC_MANDATE_FIELDS)
# so the expected full field set is assembled the same way the service does, without
# hardcoding a duplicate list that could silently drift.
_STAGE_1_TO_4_FIELD_NAMES = {
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
    "allowed_strategy_versions",
}
_ALL_MANDATE_BOOTSTRAP_EXPORT_FIELD_NAMES = _STAGE_1_TO_4_FIELD_NAMES | set(
    service._MANDATE_BOOTSTRAP_EXPORT_STATIC_MANDATE_FIELDS.keys()
)


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


async def _seed_strategy(db: AsyncSession, **overrides: Any) -> Strategy:
    defaults: dict[str, Any] = dict(
        id=uuid.uuid4(),
        name="MA Crossover",
        slug=f"ma_crossover_{uuid.uuid4().hex[:8]}",
        module_version="1.0.0",
        is_active=False,
    )
    defaults.update(overrides)
    strategy = Strategy(**defaults)
    db.add(strategy)
    await db.flush()
    await db.commit()
    return strategy


async def _seed_canonical_preview_package(
    db: AsyncSession, *, campaign_id: uuid.UUID, campaign_version: int, strategy_id: uuid.UUID, **overrides: Any
) -> CanonicalPreviewPackage:
    now = datetime.now(timezone.utc)
    defaults: dict[str, Any] = dict(
        package_id=uuid.uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("5"),
        risk_approved_amount=Decimal("5"),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        parameter_set_id=uuid.uuid4(),
        parameter_set_version="1",
        decision_record_id=uuid.uuid4(),
        risk_event_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=now,
        package_state="READY",
        generated_at=now,
        idempotency_key=f"pkg-{uuid.uuid4()}",
        input_fingerprint=f"fp-{uuid.uuid4()}",
    )
    defaults.update(overrides)
    package = CanonicalPreviewPackage(**defaults)
    db.add(package)
    await db.flush()
    await db.commit()
    return package


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
        assert result["strategy_evidence"] is None
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
        # allowed_strategy_versions is always OWNER_INPUT_REQUIRED, even when the campaign
        # itself is not found -- it is never derived from this function's own findings.
        assert result["fields"]["allowed_strategy_versions"] == {
            "classification": "OWNER_INPUT_REQUIRED",
            "value": None,
            "source": None,
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD["notes"],
        }
        # Campaign-not-found must still produce the complete manifest, per Stage 5 --
        # the remaining owner-input fields are properties of the mandate-bootstrap
        # contract itself, independent of whether this specific campaign was found.
        assert set(result["fields"].keys()) == _ALL_MANDATE_BOOTSTRAP_EXPORT_FIELD_NAMES
        assert "owner_input_summary" in result
        assert result["owner_input_summary"]["total_required"] > 0
        assert result["owner_input_summary"]["unresolved_count"] == result["owner_input_summary"]["total_required"]
        # Stage 6: campaign-not-found must still produce the complete worksheet too --
        # it describes properties of the mandate-bootstrap contract itself, not this
        # specific campaign.
        assert len(result["owner_decision_worksheet"]) == result["owner_input_summary"]["total_required"] == 32
        assert all(entry["current_value"] is None for entry in result["owner_decision_worksheet"])
        assert result["worksheet_summary"]["field_count"] == 32


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
            "strategy_evidence",
            "fields",
            "owner_input_summary",
            "owner_decision_worksheet",
            "worksheet_summary",
        }
        assert set(first["fields"].keys()) == _ALL_MANDATE_BOOTSTRAP_EXPORT_FIELD_NAMES
        for field_payload in first["fields"].values():
            assert set(field_payload.keys()) == {"classification", "value", "source", "notes"}
            assert field_payload["classification"] in {
                "DATABASE_DERIVED",
                "CONFIGURATION_DERIVED",
                "OWNER_INPUT_REQUIRED",
                "RUNTIME_DERIVED",
                "NOT_REQUIRED",
                "MISSING",
                "CONFLICTING",
            }
        assert set(first["owner_input_summary"].keys()) == {
            "total_required",
            "resolved_count",
            "unresolved_count",
            "unresolved_fields",
        }
        for worksheet_entry in first["owner_decision_worksheet"]:
            assert set(worksheet_entry.keys()) == {
                "field",
                "classification",
                "current_value",
                "required",
                "input_type",
                "accepted_values",
                "example_format",
                "description",
                "source_contract",
            }
        assert set(first["worksheet_summary"].keys()) == {
            "field_count",
            "enum_constrained_count",
            "structured_json_count",
            "numeric_count",
            "text_count",
            "boolean_count",
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


# ---------------------------------------------------------------------------
# Stage 4 (strategy evidence -- informational only, allowed_strategy_versions
# always OWNER_INPUT_REQUIRED)
# ---------------------------------------------------------------------------


async def _seed_pinned_campaign(db: AsyncSession, *, campaign_id: int = 2) -> CapitalCampaign:
    campaign = await _seed_campaign(db, id=campaign_id)
    campaign.definition_campaign_id = campaign.uuid
    campaign.definition_version = 1
    await db.commit()
    return campaign


@pytest.mark.asyncio
async def test_null_campaign_strategy_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_campaign(db, strategy_id=None)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["legacy_campaign_strategy_reference"] is None


@pytest.mark.asyncio
async def test_valid_legacy_campaign_strategy_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="legacy_slug", module_version="1.0.0", is_active=False)
        await _seed_campaign(db, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["legacy_campaign_strategy_reference"] == {
            "source": "legacy_campaign_strategy_reference",
            "found": True,
            "id": str(strategy.id),
            "name": "MA Crossover",
            "slug": "legacy_slug",
            "module_version": "1.0.0",
            "is_active": False,
            "canonical_identity": build_strategy_identity(slug="legacy_slug", module_version="1.0.0"),
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_LEGACY_STRATEGY_REFERENCE_NOTE,
        }


@pytest.mark.asyncio
async def test_dangling_legacy_strategy_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        dangling_strategy_id = uuid.uuid4()
        await _seed_campaign(db, strategy_id=dangling_strategy_id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        reference = result["strategy_evidence"]["legacy_campaign_strategy_reference"]
        assert reference["source"] == "legacy_campaign_strategy_reference"
        assert reference["found"] is False
        assert "no matching strategies row exists" in reference["notes"]


@pytest.mark.asyncio
async def test_zero_globally_active_strategies(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_strategy(db, is_active=False)
        await _seed_campaign(db)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["global_active_strategy"] == {
            "source": "global_active_strategy",
            "items": [],
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_GLOBAL_ACTIVE_STRATEGY_NOTE,
        }


@pytest.mark.asyncio
async def test_one_globally_active_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        active = await _seed_strategy(db, slug="active_one", module_version="2.0.0", is_active=True)
        await _seed_campaign(db)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["global_active_strategy"]["items"] == [
            {
                "id": str(active.id),
                "name": "MA Crossover",
                "slug": "active_one",
                "module_version": "2.0.0",
                "is_active": True,
                "canonical_identity": build_strategy_identity(slug="active_one", module_version="2.0.0"),
            }
        ]


@pytest.mark.asyncio
async def test_multiple_globally_active_strategies_deterministic_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple active rows must remain visible as evidence, without an implicit winner,
    ordered by primary key -- not creation order or any notion of 'best'."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        high_id = uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe")
        low_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        # Seed the higher id first so "creation order" and "id order" disagree.
        await _seed_strategy(db, id=high_id, slug="strategy_high", is_active=True)
        await _seed_strategy(db, id=low_id, slug="strategy_low", is_active=True)
        await _seed_campaign(db)

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        first_ids = [item["id"] for item in first["strategy_evidence"]["global_active_strategy"]["items"]]
        second_ids = [item["id"] for item in second["strategy_evidence"]["global_active_strategy"]["items"]]
        assert first_ids == second_ids == sorted(first_ids)
        assert len(first_ids) == 2


@pytest.mark.asyncio
async def test_campaign_definition_top_level_strategy_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        await _seed_definition(
            db,
            campaign_uuid=campaign.uuid,
            version=1,
            metadata_evidence={"canonical_strategy_identity": "top_level_hint@1.0.0"},
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["campaign_definition_metadata_hint"] == {
            "source": "campaign_definition_metadata_hint",
            "preferred_strategy_identity": "top_level_hint@1.0.0",
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_METADATA_HINT_NOTE,
        }


@pytest.mark.asyncio
async def test_campaign_definition_nested_strategy_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        await _seed_definition(
            db,
            campaign_uuid=campaign.uuid,
            version=1,
            metadata_evidence={"strategy": {"selected_strategy_identity": "nested_hint@2.0.0"}},
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["campaign_definition_metadata_hint"] == {
            "source": "campaign_definition_metadata_hint",
            "preferred_strategy_identity": "nested_hint@2.0.0",
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_METADATA_HINT_NOTE,
        }


@pytest.mark.asyncio
async def test_malformed_unrelated_metadata_ignored_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        await _seed_definition(
            db,
            campaign_uuid=campaign.uuid,
            version=1,
            metadata_evidence={
                "unrelated_key": "top-secret-junk-value",
                "strategy": "not-a-dict-should-be-ignored",
                "another_unrelated_blob": {"nested": "also-junk"},
            },
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["campaign_definition_metadata_hint"] is None
        serialized = repr(result)
        assert "top-secret-junk-value" not in serialized
        assert "not-a-dict-should-be-ignored" not in serialized
        assert "also-junk" not in serialized


@pytest.mark.asyncio
async def test_no_canonical_preview_package(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_pinned_campaign(db)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["canonical_preview_package_continuity"] is None


@pytest.mark.asyncio
async def test_valid_campaign_version_specific_preview_package_continuity(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        strategy = await _seed_strategy(db, slug="continuity_strategy", module_version="3.0.0")
        package = await _seed_canonical_preview_package(
            db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id, strategy_version="3.0.0"
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["canonical_preview_package_continuity"] == {
            "source": "canonical_preview_package_continuity",
            "package_id": str(package.package_id),
            "strategy_id": str(strategy.id),
            "strategy_version": "3.0.0",
            "canonical_identity": build_strategy_identity(slug="continuity_strategy", module_version="3.0.0"),
            "package_state": "READY",
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_PACKAGE_CONTINUITY_NOTE,
        }


@pytest.mark.asyncio
async def test_package_from_another_campaign_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_pinned_campaign(db)
        strategy = await _seed_strategy(db)
        other_campaign_uuid = uuid.uuid4()
        await _seed_canonical_preview_package(db, campaign_id=other_campaign_uuid, campaign_version=1, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["canonical_preview_package_continuity"] is None


@pytest.mark.asyncio
async def test_package_from_another_version_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        strategy = await _seed_strategy(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=2, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["canonical_preview_package_continuity"] is None


@pytest.mark.asyncio
async def test_evidence_never_becomes_allowed_strategy_versions_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """With all four evidence sources populated simultaneously, allowed_strategy_versions
    must still be OWNER_INPUT_REQUIRED with value=null -- evidence is never converted."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        legacy_strategy = await _seed_strategy(db, slug="legacy", module_version="1.0.0", is_active=False)
        active_strategy = await _seed_strategy(db, slug="active", module_version="1.0.0", is_active=True)
        campaign = await _seed_campaign(db, strategy_id=legacy_strategy.id)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()
        await _seed_definition(
            db,
            campaign_uuid=campaign.uuid,
            version=1,
            metadata_evidence={"canonical_strategy_identity": "hinted@9.9.9"},
        )
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=active_strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        evidence = result["strategy_evidence"]
        assert evidence["legacy_campaign_strategy_reference"] is not None
        assert len(evidence["global_active_strategy"]["items"]) == 1
        assert evidence["campaign_definition_metadata_hint"] is not None
        assert evidence["canonical_preview_package_continuity"] is not None

        assert result["fields"]["allowed_strategy_versions"] == {
            "classification": "OWNER_INPUT_REQUIRED",
            "value": None,
            "source": None,
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD["notes"],
        }


@pytest.mark.asyncio
async def test_allowed_strategy_versions_always_owner_input_required(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        not_found = await service.mandate_bootstrap_export(capital_campaign_id=999)
        assert not_found["fields"]["allowed_strategy_versions"]["classification"] == "OWNER_INPUT_REQUIRED"

        await _seed_campaign(db)
        no_pin = await service.mandate_bootstrap_export(capital_campaign_id=2)
        assert no_pin["fields"]["allowed_strategy_versions"]["classification"] == "OWNER_INPUT_REQUIRED"
        assert no_pin["fields"]["allowed_strategy_versions"]["value"] is None


@pytest.mark.asyncio
async def test_no_database_mutation_occurs_stage4(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        legacy_strategy = await _seed_strategy(db, slug="legacy4", is_active=False)
        active_strategy = await _seed_strategy(db, slug="active4", is_active=True)
        campaign = await _seed_campaign(db, strategy_id=legacy_strategy.id)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()
        await _seed_definition(
            db, campaign_uuid=campaign.uuid, version=1, metadata_evidence={"strategy_identity": "hint4@1.0.0"}
        )
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=active_strategy.id)

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["legacy_campaign_strategy_reference"]["found"] is True
        assert len(result["strategy_evidence"]["global_active_strategy"]["items"]) == 1
        assert result["strategy_evidence"]["campaign_definition_metadata_hint"] is not None
        assert result["strategy_evidence"]["canonical_preview_package_continuity"] is not None


@pytest.mark.asyncio
async def test_executable_and_overall_status_remain_fixed_with_stage4_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["executable"] is False
        assert result["overall_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_deterministic_serialized_output_stage4(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        legacy_strategy = await _seed_strategy(db, slug="legacy_det", is_active=False)
        active_strategy = await _seed_strategy(db, slug="active_det", is_active=True)
        campaign = await _seed_campaign(db, strategy_id=legacy_strategy.id)
        campaign.definition_campaign_id = campaign.uuid
        campaign.definition_version = 1
        await db.commit()
        await _seed_definition(
            db, campaign_uuid=campaign.uuid, version=1, metadata_evidence={"strategy_identity": "det@1.0.0"}
        )
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=active_strategy.id)

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        first_without_timestamp = {k: v for k, v in first.items() if k != "resolved_at"}
        second_without_timestamp = {k: v for k, v in second.items() if k != "resolved_at"}
        assert first_without_timestamp == second_without_timestamp


# ---------------------------------------------------------------------------
# Stage 6 (owner decision worksheet + worksheet_summary)
# ---------------------------------------------------------------------------

# No financial/authorization value must ever appear as an example_format or
# accepted_values entry anywhere in the worksheet.
_FORBIDDEN_RECOMMENDATION_SUBSTRINGS = (
    "25.00",
    "1000",
    "5000",
    "BTC-USD,ETH-USD",
    "BUY,SELL",
    "ma_crossover@1.0.0",
)


@pytest.mark.asyncio
async def test_every_owner_input_required_field_appears_exactly_once_in_worksheet(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        worksheet_fields = [entry["field"] for entry in result["owner_decision_worksheet"]]
        owner_required_fields = {
            name for name, field in result["fields"].items() if field["classification"] == "OWNER_INPUT_REQUIRED"
        }
        assert len(worksheet_fields) == len(set(worksheet_fields))  # no duplicates
        assert set(worksheet_fields) == owner_required_fields


@pytest.mark.asyncio
async def test_no_non_owner_input_field_appears_in_worksheet(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        worksheet_fields = {entry["field"] for entry in result["owner_decision_worksheet"]}
        for excluded in ("capital_campaign_id", "campaign_uuid", "paper_account_id", "base_currency", "mandate_expires_at", "authorization_expires_at", "audit_correlation_id"):
            assert excluded not in worksheet_fields


@pytest.mark.asyncio
async def test_all_current_values_remain_null(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert all(entry["current_value"] is None for entry in result["owner_decision_worksheet"])


@pytest.mark.asyncio
async def test_accepted_values_come_only_from_repository_defined_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        by_field = {entry["field"]: entry for entry in result["owner_decision_worksheet"]}

        # autonomy_level: AUTONOMY_LEVELS + CHECK ck_ac_mandates_autonomy_level
        assert set(by_field["autonomy_level"]["accepted_values"]) == {"LEVEL_0", "LEVEL_1", "LEVEL_2", "LEVEL_3"}
        # approval_policy: CHECK ck_ac_mandate_versions_approval_policy
        assert set(by_field["approval_policy"]["accepted_values"]) == {"HUMAN_REQUIRED", "MANDATE_ALLOWED"}

        # No validator defines a fixed enum for these -- accepted_values must be null.
        for field_name in (
            "allowed_products",
            "allowed_order_sides",
            "allowed_strategy_versions",
            "authorized_capital_usd",
            "authorization_method",
            "owner_actor_id",
            "actor",
            "reason",
            "idempotency_key",
            "confirm",
            "entry_policy",
            "owner_acknowledgements",
        ):
            assert by_field[field_name]["accepted_values"] is None, field_name


@pytest.mark.asyncio
async def test_no_financial_recommendation_appears_in_worksheet(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        serialized = repr(result["owner_decision_worksheet"])
        for forbidden in _FORBIDDEN_RECOMMENDATION_SUBSTRINGS:
            assert forbidden not in serialized

        risk_fields = (
            "authorized_capital_usd",
            "max_order_notional_usd",
            "max_open_exposure_usd",
            "max_daily_deployed_usd",
            "max_daily_realized_loss_usd",
            "max_campaign_drawdown_usd",
            "max_slippage_bps",
            "max_fee_bps",
        )
        by_field = {entry["field"]: entry for entry in result["owner_decision_worksheet"]}
        for field_name in risk_fields:
            entry = by_field[field_name]
            assert entry["current_value"] is None
            # example_format must be structural placeholder text, never a number.
            assert entry["example_format"].startswith("<") and entry["example_format"].endswith(">")


@pytest.mark.asyncio
async def test_strategy_evidence_never_fills_allowed_strategy_versions_worksheet_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(
            db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id, strategy_version="1.0.0"
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["global_active_strategy"]["items"][0]["canonical_identity"] == "ma_crossover@1.0.0"
        assert result["strategy_evidence"]["canonical_preview_package_continuity"]["canonical_identity"] == "ma_crossover@1.0.0"

        entry = next(e for e in result["owner_decision_worksheet"] if e["field"] == "allowed_strategy_versions")
        assert entry["current_value"] is None
        assert entry["accepted_values"] is None
        assert "ma_crossover@1.0.0" not in entry["example_format"]
        assert "ma_crossover" not in repr(entry).lower()


@pytest.mark.asyncio
async def test_json_policy_structures_match_existing_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        by_field = {entry["field"]: entry for entry in result["owner_decision_worksheet"]}
        policy_fields = (
            "entry_policy",
            "exit_policy",
            "cooldown_policy",
            "operating_schedule",
            "reconciliation_policy",
            "kill_switch_policy",
            "owner_acknowledgements",
            "authorization_evidence_summary",
            "authorization_evidence",
            "deterministic_explanation",
        )
        for field_name in policy_fields:
            entry = by_field[field_name]
            assert entry["input_type"] == "json_object"
            assert entry["accepted_values"] is None
            # _parse_mandate_policy_bundle only enforces "must be a JSON object" --
            # no historical policy contents or prior mandate values may appear.
            assert "prior" not in entry["description"].lower() or "never derivable" in entry["description"].lower()


@pytest.mark.asyncio
async def test_worksheet_ordering_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        first = await service.mandate_bootstrap_export(capital_campaign_id=999)
        second = await service.mandate_bootstrap_export(capital_campaign_id=999)

        first_order = [entry["field"] for entry in first["owner_decision_worksheet"]]
        second_order = [entry["field"] for entry in second["owner_decision_worksheet"]]
        assert first_order == second_order == sorted(first_order)


@pytest.mark.asyncio
async def test_summary_counts_derive_from_worksheet_contents(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        worksheet = result["owner_decision_worksheet"]
        summary = result["worksheet_summary"]

        assert summary["field_count"] == len(worksheet)
        assert summary["enum_constrained_count"] == sum(1 for e in worksheet if e["accepted_values"])
        assert summary["structured_json_count"] == sum(1 for e in worksheet if e["input_type"] == "json_object")
        assert summary["numeric_count"] == sum(1 for e in worksheet if e["input_type"] in {"decimal", "integer"})
        assert summary["text_count"] == sum(1 for e in worksheet if e["input_type"] in {"text", "csv_list", "enum"})
        assert summary["boolean_count"] == sum(1 for e in worksheet if e["input_type"] == "boolean")
        # All six counters must partition the worksheet with no double-counting and no gaps.
        assert (
            summary["structured_json_count"]
            + summary["numeric_count"]
            + summary["text_count"]
            + summary["boolean_count"]
            == summary["field_count"]
        )


@pytest.mark.asyncio
async def test_campaign_not_found_still_produces_complete_worksheet(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        assert len(result["owner_decision_worksheet"]) == 32
        assert result["worksheet_summary"]["field_count"] == 32
        assert all(entry["current_value"] is None for entry in result["owner_decision_worksheet"])


@pytest.mark.asyncio
async def test_no_database_mutation_occurs_stage6(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["worksheet_summary"]["field_count"] == 32


@pytest.mark.asyncio
async def test_executable_and_overall_status_remain_fixed_with_stage6_worksheet(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        assert result["executable"] is False
        assert result["overall_status"] == "BLOCKED"

# mandate_bootstrap()'s own parameter names that differ from this export's field keys
# (the underlying value is identical -- provider/environment are just named
# exchange_provider/exchange_environment here, consistent with Stages 1-3).
_MANDATE_BOOTSTRAP_PARAM_TO_EXPORT_FIELD = {
    "provider": "exchange_provider",
    "environment": "exchange_environment",
}

_GENUINE_OWNER_DECISION_FIELDS = (
    "owner_actor_id",
    "autonomy_level",
    "authorized_capital_usd",
    "max_order_notional_usd",
    "max_open_exposure_usd",
    "max_daily_deployed_usd",
    "max_daily_realized_loss_usd",
    "max_campaign_drawdown_usd",
    "max_consecutive_losses",
    "position_limit",
    "price_evidence_max_age_seconds",
    "max_slippage_bps",
    "max_fee_bps",
    "allowed_products",
    "allowed_order_sides",
    "allowed_strategy_versions",
    "approval_policy",
    "entry_policy",
    "exit_policy",
    "cooldown_policy",
    "operating_schedule",
    "reconciliation_policy",
    "kill_switch_policy",
    "owner_acknowledgements",
    "authorization_evidence_summary",
    "authorization_evidence",
    "deterministic_explanation",
    "authorization_method",
    "actor",
    "reason",
    "idempotency_key",
    "confirm",
)


@pytest.mark.asyncio
async def test_every_mandate_bootstrap_contract_field_appears_in_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-checks the live mandate_bootstrap() signature (not a hand-copied list) --
    fails loudly if the real contract and this export's manifest ever diverge."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        mandate_bootstrap_params = set(inspect.signature(service.mandate_bootstrap).parameters.keys())
        exported_field_names = set(result["fields"].keys())

        missing = [
            param
            for param in mandate_bootstrap_params
            if _MANDATE_BOOTSTRAP_PARAM_TO_EXPORT_FIELD.get(param, param) not in exported_field_names
        ]
        assert missing == [], f"mandate_bootstrap() parameters missing from export fields: {missing}"


@pytest.mark.asyncio
async def test_every_genuine_owner_decision_is_owner_input_required(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        for field_name in _GENUINE_OWNER_DECISION_FIELDS:
            assert result["fields"][field_name]["classification"] == "OWNER_INPUT_REQUIRED", field_name
            assert result["fields"][field_name]["value"] is None, field_name


@pytest.mark.asyncio
async def test_no_owner_input_silently_filled_from_campaign_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign = await _seed_pinned_campaign(db)
        await _seed_definition(
            db,
            campaign_uuid=campaign.uuid,
            version=1,
            maximum_position_size=Decimal("999"),
            maximum_total_exposure=Decimal("888"),
            maximum_drawdown=Decimal("777"),
            allowed_instruments=["BTC-USD", "ETH-USD"],
            allowed_asset_classes=["crypto"],
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        # None of the mandate's own risk-limit/scope fields may pick up the definition's
        # numbers, no matter how suggestively similar they look.
        for field_name in (
            "authorized_capital_usd",
            "max_order_notional_usd",
            "max_open_exposure_usd",
            "max_campaign_drawdown_usd",
            "allowed_products",
        ):
            assert result["fields"][field_name]["classification"] == "OWNER_INPUT_REQUIRED"
            assert result["fields"][field_name]["value"] is None


@pytest.mark.asyncio
async def test_no_owner_input_silently_filled_from_strategy_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id, strategy_version="1.0.0")

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["strategy_evidence"]["global_active_strategy"]["items"][0]["canonical_identity"] == "ma_crossover@1.0.0"
        assert result["strategy_evidence"]["canonical_preview_package_continuity"]["canonical_identity"] == "ma_crossover@1.0.0"
        assert result["fields"]["allowed_strategy_versions"]["classification"] == "OWNER_INPUT_REQUIRED"
        assert result["fields"]["allowed_strategy_versions"]["value"] is None
        assert result["fields"]["approval_policy"]["value"] is None


@pytest.mark.asyncio
async def test_allowed_strategy_versions_remains_null_despite_matching_production_like_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact production scenario described for campaign 2: global active
    strategy AND canonical preview package continuity both agree on ma_crossover@1.0.0.
    allowed_strategy_versions must still be OWNER_INPUT_REQUIRED / null."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(
            db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id, strategy_version="1.0.0"
        )

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["fields"]["allowed_strategy_versions"] == {
            "classification": "OWNER_INPUT_REQUIRED",
            "value": None,
            "source": None,
            "notes": service._MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD["notes"],
        }


@pytest.mark.asyncio
async def test_optional_expiration_fields_correct_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        for field_name in ("mandate_expires_at", "authorization_expires_at"):
            assert result["fields"][field_name]["classification"] == "NOT_REQUIRED"
            assert result["fields"][field_name]["value"] is None


@pytest.mark.asyncio
async def test_audit_correlation_id_dual_behavior_described_without_generating_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        field = result["fields"]["audit_correlation_id"]
        assert field["classification"] == "RUNTIME_DERIVED"
        assert field["value"] is None
        assert "omitted" in field["notes"]
        assert "uuid4" in field["notes"].lower() or "UUID4" in field["notes"]
        assert "explicitly supplied" in field["notes"]


@pytest.mark.asyncio
async def test_owner_input_summary_counts_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        owner_required_field_names = {
            name for name, field in result["fields"].items() if field["classification"] == "OWNER_INPUT_REQUIRED"
        }
        summary = result["owner_input_summary"]
        assert summary["total_required"] == len(owner_required_field_names) == 32
        assert summary["unresolved_count"] == len(owner_required_field_names)
        assert summary["resolved_count"] == 0
        assert set(summary["unresolved_fields"]) == owner_required_field_names


@pytest.mark.asyncio
async def test_owner_input_summary_excludes_not_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        unresolved = set(result["owner_input_summary"]["unresolved_fields"])
        assert "mandate_expires_at" not in unresolved
        assert "authorization_expires_at" not in unresolved
        assert "audit_correlation_id" not in unresolved  # RUNTIME_DERIVED, not OWNER_INPUT_REQUIRED
        # DATABASE_DERIVED fields (already resolved in Stages 1-3) must never appear either.
        assert "capital_campaign_id" not in unresolved


@pytest.mark.asyncio
async def test_owner_input_summary_unresolved_list_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        first = await service.mandate_bootstrap_export(capital_campaign_id=999)
        second = await service.mandate_bootstrap_export(capital_campaign_id=999)

        first_unresolved = first["owner_input_summary"]["unresolved_fields"]
        second_unresolved = second["owner_input_summary"]["unresolved_fields"]
        assert first_unresolved == second_unresolved
        assert first_unresolved == sorted(first_unresolved)


@pytest.mark.asyncio
async def test_no_database_mutation_occurs_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_export must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_export(capital_campaign_id=2)

        assert result["owner_input_summary"]["total_required"] == 32


@pytest.mark.asyncio
async def test_executable_and_overall_status_remain_fixed_with_stage5_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        result = await service.mandate_bootstrap_export(capital_campaign_id=999)

        assert result["executable"] is False
        assert result["overall_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_deterministic_serialized_output_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="1.0.0", is_active=True)
        campaign = await _seed_pinned_campaign(db)
        await _seed_canonical_preview_package(db, campaign_id=campaign.uuid, campaign_version=1, strategy_id=strategy.id)

        first = await service.mandate_bootstrap_export(capital_campaign_id=2)
        second = await service.mandate_bootstrap_export(capital_campaign_id=2)

        first_without_timestamp = {k: v for k, v in first.items() if k != "resolved_at"}
        second_without_timestamp = {k: v for k, v in second.items() if k != "resolved_at"}
        assert first_without_timestamp == second_without_timestamp
