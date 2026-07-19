from __future__ import annotations

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


class _SessionContext:
    """Mirrors AsyncSessionLocal()'s async-context-manager shape but never closes the
    underlying session -- same pattern used by test_mandate_bootstrap_export.py."""

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


async def _seed_fully_resolved_campaign(
    db: AsyncSession, *, campaign_id: int = 2
) -> tuple[CapitalCampaign, PaperAccount, LiveTradingProfile, ExchangeConnection, CapitalCampaignDefinition]:
    """Seeds a campaign whose full Stage 1-3 identity chain resolves uniquely: paper
    account, live trading profile, exchange connection, and a base_currency-bearing
    definition pin -- i.e. every field this validator may pull from the database."""
    paper_account = await _seed_paper_account(db)
    campaign = await _seed_campaign(db, id=campaign_id, paper_account_id=paper_account.id, exchange="kraken_spot")
    profile = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
    connection = await _seed_exchange_connection(db, provider="kraken_spot", environment="production")
    definition = await _seed_definition(db, campaign_uuid=campaign.uuid, version=1, base_currency="USD")
    campaign.definition_campaign_id = campaign.uuid
    campaign.definition_version = 1
    await db.commit()
    return campaign, paper_account, profile, connection, definition


def _valid_owner_input(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "owner_actor_id": "operator:owner-2",
        "autonomy_level": "LEVEL_1",
        "authorized_capital_usd": "1000",
        "max_order_notional_usd": "100",
        "max_open_exposure_usd": "500",
        "max_daily_deployed_usd": "500",
        "max_daily_realized_loss_usd": "50",
        "max_campaign_drawdown_usd": "200",
        "max_consecutive_losses": 3,
        "position_limit": 5,
        "price_evidence_max_age_seconds": 30,
        "max_slippage_bps": "10",
        "max_fee_bps": "5",
        "allowed_products": ["BTC-USD"],
        "allowed_order_sides": ["BUY", "SELL"],
        "allowed_strategy_versions": ["ma_crossover@1.0.0"],
        "approval_policy": "HUMAN_REQUIRED",
        "entry_policy": {},
        "exit_policy": {},
        "cooldown_policy": {},
        "operating_schedule": {},
        "reconciliation_policy": {},
        "kill_switch_policy": {},
        "owner_acknowledgements": {"ack": True},
        "authorization_evidence_summary": {"summary": "ok"},
        "authorization_evidence": {"evidence": "ok"},
        "deterministic_explanation": {"explanation": "ok"},
        "authorization_method": "manual_review",
        "actor": "operator:human",
        "reason": "Campaign 2 bootstrap",
        "idempotency_key": "campaign-2-bootstrap-001",
    }
    document.update(overrides)
    return document


_REQUIRED_FIELD_NAMES = sorted(set(service._MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES.keys()) - {"confirm"})


@pytest.mark.asyncio
async def test_valid_complete_document_is_complete_for_owner_review(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=2, owner_input=_valid_owner_input()
        )

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"
        assert result["validation"] == {
            "valid": True,
            "missing_fields": [],
            "unexpected_fields": [],
            "forbidden_override_fields": [],
            "field_errors": [],
            "cross_field_errors": [],
        }
        assert result["candidate_mandate_bootstrap_request"]["confirm"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", _REQUIRED_FIELD_NAMES)
async def test_each_missing_required_field_is_reported_and_invalid(
    monkeypatch: pytest.MonkeyPatch, missing_field: str
) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()
        del owner_input[missing_field]

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert result["validation"]["valid"] is False
        assert missing_field in result["validation"]["missing_fields"]


@pytest.mark.asyncio
async def test_unexpected_field_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(totally_unrecognized_field="surprise")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert result["validation"]["unexpected_fields"] == ["totally_unrecognized_field"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_field,value",
    [
        ("provider", "kraken_spot"),
        ("exchange_provider", "kraken_spot"),
        ("environment", "production"),
        ("exchange_environment", "production"),
        ("exchange_connection_id", str(uuid.uuid4())),
        ("live_trading_profile_id", str(uuid.uuid4())),
        ("paper_account_id", str(uuid.uuid4())),
        ("capital_campaign_id", 999),
        ("base_currency", "EUR"),
        ("campaign_uuid", str(uuid.uuid4())),
    ],
)
async def test_database_override_attempt_fails_closed(
    monkeypatch: pytest.MonkeyPatch, forbidden_field: str, value: Any
) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(**{forbidden_field: value})

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert forbidden_field in result["validation"]["forbidden_override_fields"]
        assert {"field": forbidden_field, "error": "OWNER_INPUT_ATTEMPTED_DATABASE_OVERRIDE"} in result["validation"][
            "field_errors"
        ]


@pytest.mark.asyncio
async def test_confirm_true_is_rejected_not_silently_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(confirm=True)

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "confirm", "error": "OWNER_INPUT_ATTEMPTED_EXECUTION_CONFIRM"} in result["validation"][
            "field_errors"
        ]
        assert "confirm" not in result["validation"]["unexpected_fields"]
        assert result["candidate_mandate_bootstrap_request"]["confirm"] is False


@pytest.mark.asyncio
async def test_confirm_false_is_treated_as_unexpected_field(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(confirm=False)

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "confirm" in result["validation"]["unexpected_fields"]


@pytest.mark.asyncio
async def test_strategy_selection_matches_evidence_is_informational_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign, *_ = await _seed_fully_resolved_campaign(db)
        strategy = await _seed_strategy(db, slug="ma_crossover", module_version="2.0.0", is_active=True)
        owner_input = _valid_owner_input(allowed_strategy_versions=["ma_crossover@2.0.0"])

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"
        assert result["strategy_review"]["evidence_matches_owner_selection"] is True
        assert result["strategy_review"]["owner_selected_allowed_strategy_versions"] == ["ma_crossover@2.0.0"]


@pytest.mark.asyncio
async def test_strategy_selection_differs_from_evidence_but_still_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        await _seed_strategy(db, slug="ma_crossover", module_version="2.0.0", is_active=True)
        owner_input = _valid_owner_input(allowed_strategy_versions=["a_totally_different_strategy@9.9.9"])

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"
        assert result["strategy_review"]["evidence_matches_owner_selection"] is False


@pytest.mark.asyncio
async def test_malformed_strategy_identity_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(allowed_strategy_versions=["no-delimiter-here"])

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "allowed_strategy_versions", "error": "invalid_strategy_identity"} in result["validation"][
            "field_errors"
        ]


@pytest.mark.asyncio
async def test_empty_allowed_products_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(allowed_products=[])

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "allowed_products", "error": "empty_list"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_empty_allowed_order_sides_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(allowed_order_sides=[])

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "allowed_order_sides", "error": "empty_list"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_invalid_autonomy_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(autonomy_level="LEVEL_99")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "autonomy_level", "error": "invalid_enum_value"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_invalid_approval_policy_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(approval_policy="NOT_A_REAL_POLICY")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "approval_policy", "error": "invalid_enum_value"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_nonpositive_authorized_capital_is_rejected_via_validate_mandate_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(authorized_capital_usd="0")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "invalid_authorized_capital" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
async def test_max_order_notional_exceeding_authorized_capital_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(authorized_capital_usd="100", max_order_notional_usd="200")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "max_order_exceeds_authorized_capital" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
async def test_max_open_exposure_exceeding_authorized_capital_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(authorized_capital_usd="100", max_open_exposure_usd="200")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "max_exposure_exceeds_authorized_capital" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
async def test_max_daily_deployed_exceeding_authorized_capital_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(
            authorized_capital_usd="100", max_open_exposure_usd="100", max_daily_deployed_usd="200"
        )

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "max_daily_deployed_exceeds_authorized_capital" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_name",
    ["max_daily_realized_loss_usd", "max_campaign_drawdown_usd", "max_slippage_bps", "max_fee_bps"],
)
async def test_negative_nonnegative_bound_fields_are_rejected(monkeypatch: pytest.MonkeyPatch, field_name: str) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(**{field_name: "-1"})

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert f"invalid_{field_name}" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
async def test_negative_max_consecutive_losses_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(max_consecutive_losses=-1)

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert "invalid_max_consecutive_losses" in result["validation"]["cross_field_errors"]


@pytest.mark.asyncio
async def test_malformed_policy_json_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(entry_policy="not-an-object")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "entry_policy", "error": "invalid_json_object"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_malformed_timestamp_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(mandate_expires_at="not-a-timestamp")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "mandate_expires_at", "error": "invalid_timestamp"} in result["validation"]["field_errors"]


@pytest.mark.asyncio
async def test_timestamp_without_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input(mandate_expires_at="2027-01-01T00:00:00")

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "INVALID"
        assert {"field": "mandate_expires_at", "error": "timestamp_not_timezone_aware"} in result["validation"][
            "field_errors"
        ]


@pytest.mark.asyncio
async def test_optional_expiration_fields_may_be_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()
        assert "mandate_expires_at" not in owner_input
        assert "authorization_expires_at" not in owner_input

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"
        assert result["candidate_mandate_bootstrap_request"]["mandate_expires_at"] is None
        assert result["candidate_mandate_bootstrap_request"]["authorization_expires_at"] is None


@pytest.mark.asyncio
async def test_optional_audit_correlation_id_omitted_without_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()
        assert "audit_correlation_id" not in owner_input

        result = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"
        assert result["candidate_mandate_bootstrap_request"]["audit_correlation_id"] is None


@pytest.mark.asyncio
async def test_deterministic_output_for_identical_input(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        first = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)
        second = await service.mandate_bootstrap_session_validate(capital_campaign_id=2, owner_input=owner_input)

        first.pop("source_identity")
        second.pop("source_identity")
        assert first == second


@pytest.mark.asyncio
async def test_no_database_mutation_occurs(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        await _seed_fully_resolved_campaign(db)
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_session_validate must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=2, owner_input=_valid_owner_input()
        )

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"


@pytest.mark.asyncio
async def test_mandate_bootstrap_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_session_validate must never call mandate_bootstrap()")

        monkeypatch.setattr(service, "mandate_bootstrap", _forbid)

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=2, owner_input=_valid_owner_input()
        )

        assert result["session_status"] == "COMPLETE_FOR_OWNER_REVIEW"


@pytest.mark.asyncio
async def test_campaign_not_found_is_invalid_with_database_identity_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=999, owner_input=_valid_owner_input()
        )

        assert result["session_status"] == "INVALID"
        error_fields = {entry["field"] for entry in result["validation"]["field_errors"]}
        assert "capital_campaign_id" in error_fields
        assert all(
            entry["error"] == "database_identity_unresolved"
            for entry in result["validation"]["field_errors"]
            if entry["field"] == "capital_campaign_id"
        )


@pytest.mark.asyncio
async def test_no_shell_command_generated_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=2, owner_input=_valid_owner_input()
        )

        assert "command" not in result
        assert "shell_command" not in result
        assert "cli_command" not in result


@pytest.mark.asyncio
async def test_output_top_level_keys_match_exact_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_session_validate(
            capital_campaign_id=2, owner_input=_valid_owner_input()
        )

        assert set(result.keys()) == {
            "session_status",
            "resolved_database_inputs",
            "owner_inputs",
            "candidate_mandate_bootstrap_request",
            "validation",
            "source_identity",
            "strategy_review",
        }
        assert set(result["source_identity"].keys()) == {
            "capital_campaign_id",
            "campaign_uuid",
            "definition_campaign_id",
            "definition_version",
            "export_resolved_at",
        }
        assert set(result["strategy_review"].keys()) == {
            "owner_selected_allowed_strategy_versions",
            "informational_strategy_evidence",
            "evidence_matches_owner_selection",
        }
