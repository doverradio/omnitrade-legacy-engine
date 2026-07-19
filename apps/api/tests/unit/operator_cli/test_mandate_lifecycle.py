from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
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
    AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__,
    AutonomousCapitalMandateAuthorization.__table__,
    AuditLog.__table__,
]


class _SessionContext:
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


def _valid_authorize_input(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "actor": "operator:human",
        "reason": "First authorization of campaign 2 draft mandate",
        "authorization_method": "manual_review",
        "owner_acknowledgements": {"ack": True},
        "authorization_evidence": {"evidence": "reviewed"},
        "deterministic_explanation": {"explanation": "owner authorized after manual review"},
        "idempotency_key": "campaign-2-authorize-001",
    }
    document.update(overrides)
    return document


async def _table_rows(db: AsyncSession, model: Any) -> list[Any]:
    return list((await db.execute(select(model))).scalars().all())


async def _create_draft_mandate(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
    await _seed_fully_resolved_campaign(db)
    return await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=_valid_owner_input())


@pytest.mark.asyncio
async def test_authorize_succeeds_from_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        assert created["overall_status"] == "CREATED"

        result = await service.mandate_lifecycle_authorize(
            mandate_id=uuid.UUID(created["mandate_id"]),
            mandate_version_id=uuid.UUID(created["mandate_version_id"]),
            owner_input=_valid_authorize_input(),
        )

        assert result["overall_status"] == "AUTHORIZED"
        assert result["mandate_status"] == "AUTHORIZED"
        assert result["write_summary"]["submitted_for_authorization"] is True
        assert result["write_summary"]["authorized"] is True

        mandate = await db.get(AutonomousCapitalMandate, uuid.UUID(created["mandate_id"]))
        version = await db.get(AutonomousCapitalMandateVersion, uuid.UUID(created["mandate_version_id"]))
        assert mandate.status == "AUTHORIZED"
        assert version.is_authorized is True
        assert version.is_active is False
        assert len(await _table_rows(db, AutonomousCapitalMandateAuthorization)) == 1


@pytest.mark.asyncio
async def test_authorize_missing_fields_is_zero_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        owner_input = _valid_authorize_input()
        del owner_input["authorization_method"]

        result = await service.mandate_lifecycle_authorize(
            mandate_id=uuid.UUID(created["mandate_id"]),
            mandate_version_id=uuid.UUID(created["mandate_version_id"]),
            owner_input=owner_input,
        )

        assert result["overall_status"] == "FAILED_VALIDATION"
        assert "authorization_method" in result["validation"]["missing_fields"]

        mandate = await db.get(AutonomousCapitalMandate, uuid.UUID(created["mandate_id"]))
        assert mandate.status == "DRAFT"
        assert await _table_rows(db, AutonomousCapitalMandateAuthorization) == []


@pytest.mark.asyncio
async def test_authorize_mandate_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_lifecycle_authorize(
            mandate_id=uuid.uuid4(),
            mandate_version_id=uuid.uuid4(),
            owner_input=_valid_authorize_input(),
        )

        assert result["overall_status"] == "FAILED_VALIDATION"
        assert result["validation"]["field_errors"][0]["error"] == "mandate_not_found"


@pytest.mark.asyncio
async def test_authorize_version_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        result = await service.mandate_lifecycle_authorize(
            mandate_id=uuid.UUID(created["mandate_id"]),
            mandate_version_id=uuid.uuid4(),
            owner_input=_valid_authorize_input(),
        )

        assert result["overall_status"] == "FAILED_VALIDATION"
        assert result["validation"]["field_errors"][0]["error"] == "mandate_version_not_found_or_mismatched"


@pytest.mark.asyncio
async def test_authorize_rejects_mandate_not_in_authorizable_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])

        first = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )
        assert first["overall_status"] == "AUTHORIZED"

        activated = await service.mandate_lifecycle_activate(
            mandate_id=mandate_id, actor="operator:human", reason="activate", idempotency_key="campaign-2-activate-001"
        )
        assert activated["overall_status"] == "ACTIVE"

        second = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id,
            mandate_version_id=version_id,
            owner_input=_valid_authorize_input(idempotency_key="campaign-2-authorize-002"),
        )
        assert second["overall_status"] == "FAILED_VALIDATION"
        assert second["validation"]["field_errors"][0]["error"] == "mandate_not_in_authorizable_state"


@pytest.mark.asyncio
async def test_authorize_idempotent_rerun_same_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])
        owner_input = _valid_authorize_input()

        first = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=owner_input
        )
        second = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=owner_input
        )

        assert first["overall_status"] == "AUTHORIZED"
        assert second["overall_status"] == "AUTHORIZED"
        assert first["mandate_authorization_id"] == second["mandate_authorization_id"]
        assert len(await _table_rows(db, AutonomousCapitalMandateAuthorization)) == 1


@pytest.mark.asyncio
async def test_authorize_conflict_on_different_evidence_same_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])

        first = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )
        assert first["overall_status"] == "AUTHORIZED"

        conflicting = _valid_authorize_input(authorization_evidence={"evidence": "a completely different review"})
        second = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=conflicting
        )

        assert second["overall_status"] == "CONFLICT"
        assert second["conflict"]["reason"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_AUTHORIZATION_EVIDENCE"
        assert "authorization_evidence" in second["conflict"]["mismatched_fields"]
        assert len(await _table_rows(db, AutonomousCapitalMandateAuthorization)) == 1


@pytest.mark.asyncio
async def test_status_ok_after_authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])

        await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )

        result = await service.mandate_lifecycle_status(mandate_id=mandate_id)

        assert result["overall_status"] == "OK"
        assert result["mandate_status"] == "AUTHORIZED"
        assert result["mandate_version_id"] == str(version_id)
        assert result["is_authorized"] is True
        assert result["is_active"] is False
        assert result["latest_authorization_state"] == "AUTHORIZED"
        assert result["identity_coherent"] is True
        assert result["audit_coherent"] is True
        assert result["conflicts"] == []


@pytest.mark.asyncio
async def test_status_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        result = await service.mandate_lifecycle_status(mandate_id=uuid.uuid4())

        assert result["overall_status"] == "NOT_FOUND"
        assert result["mandate_status"] is None


@pytest.mark.asyncio
async def test_status_performs_zero_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_lifecycle_status must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_lifecycle_status(mandate_id=uuid.UUID(created["mandate_id"]))
        assert result["overall_status"] == "OK"


@pytest.mark.asyncio
async def test_activate_succeeds_after_authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])
        await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )

        result = await service.mandate_lifecycle_activate(
            mandate_id=mandate_id, actor="operator:human", reason="activate for proving", idempotency_key="campaign-2-activate-001"
        )

        assert result["overall_status"] == "ACTIVE"
        assert result["mandate_status"] == "ACTIVE"
        assert result["governing_mandate_version_id"] == str(version_id)

        mandate = await db.get(AutonomousCapitalMandate, mandate_id)
        version = await db.get(AutonomousCapitalMandateVersion, version_id)
        assert mandate.status == "ACTIVE"
        assert version.is_active is True


@pytest.mark.asyncio
async def test_activate_fails_without_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        result = await service.mandate_lifecycle_activate(
            mandate_id=uuid.UUID(created["mandate_id"]),
            actor="operator:human",
            reason="attempt activate too early",
            idempotency_key="campaign-2-activate-too-early",
        )

        assert result["overall_status"] == "FAILED_VALIDATION"
        mandate = await db.get(AutonomousCapitalMandate, uuid.UUID(created["mandate_id"]))
        assert mandate.status == "DRAFT"


@pytest.mark.asyncio
async def test_activate_idempotent_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])
        await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )

        first = await service.mandate_lifecycle_activate(
            mandate_id=mandate_id, actor="operator:human", reason="activate", idempotency_key="campaign-2-activate-001"
        )
        second = await service.mandate_lifecycle_activate(
            mandate_id=mandate_id, actor="operator:human", reason="activate", idempotency_key="campaign-2-activate-001"
        )

        assert first["overall_status"] == "ACTIVE"
        assert second["overall_status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_commission_authorize_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        result = await service.mandate_lifecycle_commission(
            capital_campaign_id=2,
            mandate_id=uuid.UUID(created["mandate_id"]),
            action="AUTHORIZE",
            mandate_version_id=uuid.UUID(created["mandate_version_id"]),
            owner_input=_valid_authorize_input(),
        )

        assert result["overall_status"] == "COMMISSIONED"
        assert result["lifecycle_status"]["overall_status"] == "OK"
        assert result["lifecycle_status"]["mandate_status"] == "AUTHORIZED"


@pytest.mark.asyncio
async def test_commission_activate_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])
        await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )

        result = await service.mandate_lifecycle_commission(
            capital_campaign_id=2,
            mandate_id=mandate_id,
            action="ACTIVATE",
            actor="operator:human",
            reason="activate for proving",
            idempotency_key="campaign-2-activate-001",
        )

        assert result["overall_status"] == "COMMISSIONED"
        assert result["lifecycle_status"]["mandate_status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_commission_aborts_with_zero_writes_when_audit_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        monkeypatch.setattr(
            service,
            "validate_mandate_version",
            lambda version: ValidationResult(valid=True, reason=None),
        )

        result = await service.mandate_lifecycle_commission(
            capital_campaign_id=2,
            mandate_id=uuid.UUID(created["mandate_id"]),
            action="AUTHORIZE",
            mandate_version_id=uuid.UUID(created["mandate_version_id"]),
            owner_input=_valid_authorize_input(),
        )

        assert result["overall_status"] == "ABORTED_NOT_READY"
        assert await _table_rows(db, AutonomousCapitalMandateAuthorization) == []


@pytest.mark.asyncio
async def test_authorization_activation_never_crosses_into_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)
        mandate_id = uuid.UUID(created["mandate_id"])
        version_id = uuid.UUID(created["mandate_version_id"])

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must never touch mandate_bootstrap or the autonomous cycle")

        monkeypatch.setattr(service, "run_autonomous_preview_cycle", _forbid)

        authorized = await service.mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=version_id, owner_input=_valid_authorize_input()
        )
        assert authorized["overall_status"] == "AUTHORIZED"

        activated = await service.mandate_lifecycle_activate(
            mandate_id=mandate_id, actor="operator:human", reason="activate", idempotency_key="campaign-2-activate-001"
        )
        assert activated["overall_status"] == "ACTIVE"


def test_scan_calls_mandate_lifecycle_authorize_only_expected() -> None:
    found = set(service._mandate_governance_audit_scan_calls(service.mandate_lifecycle_authorize))
    assert found == {"apply_mandate_lifecycle_action", "authorize_mandate_version"}


def test_scan_calls_mandate_lifecycle_activate_only_expected() -> None:
    found = set(service._mandate_governance_audit_scan_calls(service.mandate_lifecycle_activate))
    assert found == {"apply_mandate_lifecycle_action"}
    assert "authorize_mandate_version" not in found
    assert "create_mandate" not in found
    assert "mandate_bootstrap" not in found


def test_scan_calls_mandate_lifecycle_status_is_write_free() -> None:
    found = service._mandate_governance_audit_scan_calls(service.mandate_lifecycle_status)
    assert found == []


def test_scan_calls_mandate_lifecycle_commission_has_no_direct_forbidden_calls() -> None:
    found = service._mandate_governance_audit_scan_calls(service.mandate_lifecycle_commission)
    assert found == []


@pytest.mark.asyncio
async def test_authorize_output_top_level_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        result = await service.mandate_lifecycle_authorize(
            mandate_id=uuid.UUID(created["mandate_id"]),
            mandate_version_id=uuid.UUID(created["mandate_version_id"]),
            owner_input=_valid_authorize_input(),
        )

        for key in (
            "overall_status",
            "mandate_id",
            "mandate_version_id",
            "mandate_authorization_id",
            "mandate_status",
            "write_summary",
            "next_required_action",
        ):
            assert key in result


@pytest.mark.asyncio
async def test_status_output_top_level_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        created = await _create_draft_mandate(db, monkeypatch)

        result = await service.mandate_lifecycle_status(mandate_id=uuid.UUID(created["mandate_id"]))

        for key in (
            "overall_status",
            "mandate_id",
            "mandate_status",
            "mandate_version_id",
            "mandate_version_number",
            "is_authorized",
            "is_active",
            "latest_authorization_state",
            "identity_coherent",
            "audit_coherent",
            "conflicts",
            "warnings",
        ):
            assert key in result
