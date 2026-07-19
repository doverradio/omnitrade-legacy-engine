from __future__ import annotations

import uuid
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
from app.services.mandates.validation import ValidationResult
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
    underlying session -- same pattern used by the Stage 6/7 test files."""

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


async def _seed_fully_resolved_campaign(
    db: AsyncSession, *, campaign_id: int = 2
) -> tuple[CapitalCampaign, PaperAccount, LiveTradingProfile, ExchangeConnection, CapitalCampaignDefinition]:
    paper_account = await _seed_paper_account(db)
    campaign = await _seed_campaign(db, id=campaign_id, paper_account_id=paper_account.id, exchange="kraken_spot")
    profile = await _seed_live_trading_profile(db, paper_account_id=paper_account.id)
    connection = await _seed_exchange_connection(db, provider="kraken_spot", environment="production")
    definition = await _seed_definition(db, campaign_uuid=campaign.uuid, version=1, base_currency="USD")
    campaign.definition_campaign_id = campaign.uuid
    campaign.definition_version = 1
    await db.commit()
    return campaign, paper_account, profile, connection, definition


def _assert_all_boundaries_pass(boundaries: dict[str, Any]) -> None:
    for key, value in boundaries.items():
        if isinstance(value, bool):
            assert value is True, f"{key} expected True, got {value!r}"
        elif isinstance(value, list):
            assert value == [], f"{key} expected empty list, got {value!r}"


@pytest.mark.asyncio
async def test_healthy_repository_reports_ready_for_stage9(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert result["overall_status"] == "READY_FOR_STAGE9"
        assert result["repository_safe_for_stage9"] is True
        assert result["write_paths"] == []
        _assert_all_boundaries_pass(result["owner_boundaries"])
        _assert_all_boundaries_pass(result["runtime_boundaries"])
        _assert_all_boundaries_pass(result["strategy_boundaries"])
        _assert_all_boundaries_pass(result["authorization_boundaries"])


@pytest.mark.asyncio
async def test_campaign_not_found_still_completes_audit_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=999)

        # The audit is a property of the code, not of whether this one campaign resolves --
        # a not-found campaign must not itself make the repository "unsafe".
        assert result["overall_status"] == "READY_FOR_STAGE9"
        assert any("999" in warning and "not found" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_no_database_mutation_occurs(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        await _seed_fully_resolved_campaign(db)
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_governance_readiness_audit must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert result["overall_status"] == "READY_FOR_STAGE9"


@pytest.mark.asyncio
async def test_no_lifecycle_functions_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_governance_readiness_audit must never call a lifecycle function")

        # Deliberately does not monkeypatch mandate_bootstrap itself: the audit's job is to
        # inspect that function's source (proving its write path stays gated), not to avoid
        # referencing it -- replacing it here would break the gate-detection check for a
        # reason unrelated to what this test asserts.
        monkeypatch.setattr(service, "create_mandate", _forbid)
        monkeypatch.setattr(service, "create_mandate_version", _forbid)
        monkeypatch.setattr(service, "authorize_mandate_version", _forbid)
        monkeypatch.setattr(service, "apply_mandate_lifecycle_action", _forbid)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert result["overall_status"] == "READY_FOR_STAGE9"


@pytest.mark.asyncio
async def test_confirm_cannot_become_true(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        boundaries = result["authorization_boundaries"]
        assert boundaries["confirm_excluded_from_owner_required_fields"] is True
        assert boundaries["confirm_hardcoded_false_in_candidate"] is True
        assert boundaries["no_confirm_cli_flag_registered"] is True
        assert boundaries["confirm_true_probe_blocked"] is True


@pytest.mark.asyncio
async def test_strategy_evidence_never_becomes_owner_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        boundaries = result["strategy_boundaries"]
        assert boundaries["allowed_strategy_versions_always_owner_input_required"] is True
        assert boundaries["strategy_evidence_marked_informational_only"] is True
        assert boundaries["evidence_match_flag_computed_after_and_independent_of_validity"] is True


@pytest.mark.asyncio
async def test_owner_fields_remain_owner_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        boundaries = result["owner_boundaries"]
        assert boundaries["forbidden_field_map_consistent_with_database_fields"] is True
        assert boundaries["no_overlap_between_required_and_forbidden_fields"] is True
        assert boundaries["override_probe_blocked_all_forbidden_fields"] is True
        assert boundaries["no_hidden_default_values_for_owner_required_fields"] is True
        assert boundaries["hidden_default_fields_found"] == []
        assert boundaries["every_required_field_still_required_probe"] is True


@pytest.mark.asyncio
async def test_repository_reports_ready_only_when_every_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the aggregation is live, not hardcoded: forcing exactly one check to fail
    must flip overall_status to NOT_READY, and clearing the injected failure must flip it
    back -- otherwise this would be indistinguishable from an audit that always reports
    READY_FOR_STAGE9 regardless of the repository's real state."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        healthy = await service.mandate_governance_readiness_audit(capital_campaign_id=2)
        assert healthy["overall_status"] == "READY_FOR_STAGE9"

        monkeypatch.setattr(
            service,
            "validate_mandate_version",
            lambda version: ValidationResult(valid=True, reason=None),
        )
        degraded = await service.mandate_governance_readiness_audit(capital_campaign_id=2)
        assert degraded["overall_status"] == "NOT_READY"
        assert degraded["repository_safe_for_stage9"] is False
        entry = next(v for v in degraded["validators_verified"] if v["name"] == "validate_mandate_version")
        assert entry["is_authoritative"] is False
        assert any(rec.startswith("failing_check:") for rec in degraded["recommendations"])


@pytest.mark.asyncio
async def test_unexpected_write_path_forces_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        real_scan = service._mandate_governance_audit_scan_calls

        def _fake_scan(fn: Any) -> list[str]:
            if fn is service.mandate_bootstrap_export:
                return ["db.add"]
            return real_scan(fn)

        monkeypatch.setattr(service, "_mandate_governance_audit_scan_calls", _fake_scan)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert result["overall_status"] == "NOT_READY"
        assert result["repository_safe_for_stage9"] is False
        assert {"function": "mandate_bootstrap_export", "forbidden_calls_found": ["db.add"]} in result["write_paths"]


@pytest.mark.asyncio
async def test_mandate_bootstrap_write_path_reported_as_gated_not_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert "mandate_bootstrap" in result["commands_inspected"]
        assert any(entry["function"] == "mandate_bootstrap" for entry in result["blocked_write_paths"])
        assert not any(entry["function"] == "mandate_bootstrap" for entry in result["write_paths"])
        assert result["authorization_boundaries"]["mandate_bootstrap_write_path_gated_behind_confirm"] is True


@pytest.mark.asyncio
async def test_validators_verified_are_authoritative_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert {entry["name"] for entry in result["validators_verified"]} == {
            "validate_mandate_version",
            "validate_autonomy_level",
            "is_strategy_identity",
        }
        assert all(entry["is_authoritative"] for entry in result["validators_verified"])


@pytest.mark.asyncio
async def test_output_top_level_keys_match_exact_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert set(result.keys()) == {
            "overall_status",
            "repository_safe_for_stage9",
            "inspection_timestamp",
            "commands_inspected",
            "validators_verified",
            "write_paths",
            "blocked_write_paths",
            "owner_boundaries",
            "runtime_boundaries",
            "strategy_boundaries",
            "authorization_boundaries",
            "warnings",
            "recommendations",
            "next_stage",
        }
        assert result["overall_status"] in {"READY_FOR_STAGE9", "NOT_READY"}


@pytest.mark.asyncio
async def test_deterministic_output_for_repeated_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        first = await service.mandate_governance_readiness_audit(capital_campaign_id=2)
        second = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        first.pop("inspection_timestamp")
        second.pop("inspection_timestamp")
        assert first == second


@pytest.mark.asyncio
async def test_next_stage_never_implies_auto_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_governance_readiness_audit(capital_campaign_id=2)

        assert "human" in result["next_stage"].lower()
        assert any("does not itself authorize" in rec.lower() or "not a gate" in rec.lower() for rec in result["recommendations"])


def test_scan_calls_detects_real_forbidden_call_not_docstring_mentions() -> None:
    async def _looks_safe_but_is_not() -> None:
        """This docstring merely mentions mandate_bootstrap() and create_mandate() in
        prose -- it must never be mistaken for an actual call."""
        db.add(object())  # noqa: F821 - deliberately undefined, only ever parsed as AST

    found = service._mandate_governance_audit_scan_calls(_looks_safe_but_is_not)
    assert found == ["db.add"]


def test_scan_calls_ignores_docstring_only_mentions() -> None:
    async def _genuinely_read_only() -> None:
        """Never calls mandate_bootstrap() or create_mandate() -- this is prose only."""
        return None

    found = service._mandate_governance_audit_scan_calls(_genuinely_read_only)
    assert found == []


def test_scan_calls_finds_nested_lifecycle_call_in_keyword_argument() -> None:
    async def _nested_call() -> None:
        await _await_db_operation(operation=create_mandate(db=None))  # noqa: F821

    found = service._mandate_governance_audit_scan_calls(_nested_call)
    assert found == ["create_mandate"]


@pytest.mark.asyncio
async def test_real_pipeline_functions_are_write_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct, unmonkeypatched sanity check that the two real read-only functions in
    this repository right now contain zero forbidden calls -- independent of the full
    audit's aggregation logic."""
    export_found = service._mandate_governance_audit_scan_calls(service.mandate_bootstrap_export)
    validate_found = service._mandate_governance_audit_scan_calls(service.mandate_bootstrap_session_validate)
    assert export_found == []
    assert validate_found == []
