from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
from app.services.mandates.lifecycle import create_mandate_version as _real_create_mandate_version
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


async def _table_rows(db: AsyncSession, model: Any) -> list[Any]:
    return list((await db.execute(select(model))).scalars().all())


@pytest.mark.asyncio
async def test_status_command_performs_zero_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        await _seed_fully_resolved_campaign(db)
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_create_status must never mutate the database")

        monkeypatch.setattr(db, "add", _forbid)
        monkeypatch.setattr(db, "commit", _forbid)
        monkeypatch.setattr(db, "flush", _forbid)
        monkeypatch.setattr(db, "delete", _forbid)

        result = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key="campaign-2-bootstrap-001"
        )

        assert result["overall_status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_not_started_before_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key="never-used-key"
        )

        assert result["overall_status"] == "NOT_STARTED"
        assert result["mandate_id"] is None
        assert result["mandate_version_id"] is None
        assert result["creation_complete"] is False
        assert result["recovery_required"] is False
        assert result["conflicts"] == []


@pytest.mark.asyncio
async def test_complete_draft_after_successful_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        created = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert created["overall_status"] == "CREATED"

        result = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )

        assert result["overall_status"] == "COMPLETE_DRAFT"
        assert result["mandate_id"] == created["mandate_id"]
        assert result["mandate_version_id"] == created["mandate_version_id"]
        assert result["mandate_status"] == "DRAFT"
        assert result["mandate_version_number"] == 1
        assert result["identity_coherent"] is True
        assert result["audit_coherent"] is True
        assert result["creation_complete"] is True
        assert result["recovery_required"] is False
        assert result["conflicts"] == []
        assert result["mandate_audit_event"]["action"] == "MANDATE_CREATED"
        assert result["mandate_version_audit_event"]["action"] == "MANDATE_VERSION_CREATED"


@pytest.mark.asyncio
async def test_interruption_produces_partial_recoverable_and_rerun_repairs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        async def _simulated_crash(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated process interruption before version commit")

        monkeypatch.setattr(service, "create_mandate_version", _simulated_crash)
        with pytest.raises(RuntimeError):
            await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)

        # Real crash-recovery signature: mandate row committed, zero version rows.
        assert len(await _table_rows(db, AutonomousCapitalMandate)) == 1
        assert await _table_rows(db, AutonomousCapitalMandateVersion) == []

        status_during_outage = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )
        assert status_during_outage["overall_status"] == "PARTIAL_RECOVERABLE"
        assert status_during_outage["recovery_required"] is True
        assert status_during_outage["creation_complete"] is False
        assert status_during_outage["mandate_id"] is not None
        assert status_during_outage["mandate_version_id"] is None

        # Restore the real function and rerun with the SAME owner-input document.
        monkeypatch.setattr(service, "create_mandate_version", _real_create_mandate_version)
        repaired = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)

        assert repaired["overall_status"] == "CREATED"
        assert repaired["mandate_id"] == status_during_outage["mandate_id"]

        mandates = await _table_rows(db, AutonomousCapitalMandate)
        versions = await _table_rows(db, AutonomousCapitalMandateVersion)
        assert len(mandates) == 1  # no duplicate mandate created by the repair
        assert len(versions) == 1  # exactly one initial version created by the repair

        status_after_repair = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )
        assert status_after_repair["overall_status"] == "COMPLETE_DRAFT"


@pytest.mark.asyncio
async def test_same_key_different_mandate_input_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        first = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert first["overall_status"] == "CREATED"

        conflicting_input = _valid_owner_input(owner_actor_id="a-completely-different-owner")
        second = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=conflicting_input)

        assert second["overall_status"] == "CONFLICT"
        assert second["conflict"]["reason"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_MANDATE_INPUT"
        assert "owner_actor_id" in second["conflict"]["mismatched_fields"]
        assert second["write_summary"] == {"mandate_created": False, "mandate_version_created": False}

        # Zero new writes: still exactly the one original mandate/version.
        assert len(await _table_rows(db, AutonomousCapitalMandate)) == 1
        assert len(await _table_rows(db, AutonomousCapitalMandateVersion)) == 1


@pytest.mark.asyncio
async def test_same_key_different_version_input_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        first = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert first["overall_status"] == "CREATED"

        conflicting_input = _valid_owner_input(authorized_capital_usd="999999")
        second = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=conflicting_input)

        assert second["overall_status"] == "CONFLICT"
        assert second["conflict"]["reason"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_VERSION_INPUT"
        assert len(await _table_rows(db, AutonomousCapitalMandateVersion)) == 1


@pytest.mark.asyncio
async def test_different_idempotency_key_is_not_a_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check that the mismatch guard is scoped to reused keys, not a blanket
    rejection of any second mandate for the same campaign."""
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        first = await service.mandate_bootstrap_create(
            capital_campaign_id=2, owner_input=_valid_owner_input(idempotency_key="key-a")
        )
        second = await service.mandate_bootstrap_create(
            capital_campaign_id=2, owner_input=_valid_owner_input(idempotency_key="key-b")
        )

        assert first["overall_status"] == "CREATED"
        assert second["overall_status"] == "CREATED"
        assert first["mandate_id"] != second["mandate_id"]


@pytest.mark.asyncio
async def test_incoherent_audit_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        # A MANDATE_CREATED audit row that references a mandate row that was never
        # actually persisted (or has since been deleted) -- a broken audit trail.
        db.add(
            AuditLog(
                actor="operator:human",
                action="MANDATE_CREATED",
                entity_type="autonomous_capital_mandate",
                entity_id=uuid.uuid4(),
                before_state=None,
                after_state={"idempotency_key": "broken-key:create-mandate", "status": "DRAFT"},
            )
        )
        await db.commit()

        result = await service.mandate_bootstrap_create_status(capital_campaign_id=2, idempotency_key="broken-key")

        assert result["overall_status"] == "INCOHERENT"
        assert result["audit_coherent"] is False
        assert any("no longer exists" in c for c in result["conflicts"])


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_conflict_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        # Simulates a concurrent-creation race: two distinct mandates both audited under
        # the same idempotency_key.
        db.add(
            AuditLog(
                actor="operator:human",
                action="MANDATE_CREATED",
                entity_type="autonomous_capital_mandate",
                entity_id=uuid.uuid4(),
                before_state=None,
                after_state={"idempotency_key": "raced-key:create-mandate", "status": "DRAFT"},
            )
        )
        db.add(
            AuditLog(
                actor="operator:human",
                action="MANDATE_CREATED",
                entity_type="autonomous_capital_mandate",
                entity_id=uuid.uuid4(),
                before_state=None,
                after_state={"idempotency_key": "raced-key:create-mandate", "status": "DRAFT"},
            )
        )
        await db.commit()

        result = await service.mandate_bootstrap_create_status(capital_campaign_id=2, idempotency_key="raced-key")

        assert result["overall_status"] == "CONFLICT"
        assert any("distinct mandates" in c for c in result["conflicts"])


@pytest.mark.asyncio
async def test_identity_drift_produces_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        campaign, paper_account, profile, connection, definition = await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        created = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert created["overall_status"] == "CREATED"

        # The campaign's underlying exchange connection changes after mandate creation --
        # a different exchange_connections row now resolves for the same provider/env.
        old_connection_id = connection.exchange_connection_id
        await db.delete(await db.get(ExchangeConnection, old_connection_id))
        await db.commit()
        await _seed_exchange_connection(db, provider="kraken_spot", environment="production")

        result = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )

        assert result["overall_status"] == "CONFLICT"
        assert result["identity_coherent"] is False
        assert any("identity drift" in c for c in result["conflicts"])


@pytest.mark.asyncio
async def test_authorization_remains_impossible(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must never call authorize_mandate_version")

        monkeypatch.setattr(service, "authorize_mandate_version", _forbid)

        owner_input = _valid_owner_input()
        created = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert created["overall_status"] == "CREATED"

        status = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )
        assert status["overall_status"] == "COMPLETE_DRAFT"
        assert await _table_rows(db, AutonomousCapitalMandateAuthorization) == []


@pytest.mark.asyncio
async def test_activation_remains_impossible(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must never call apply_mandate_lifecycle_action")

        monkeypatch.setattr(service, "apply_mandate_lifecycle_action", _forbid)

        owner_input = _valid_owner_input()
        created = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert created["overall_status"] == "CREATED"

        mandate = (await db.execute(select(AutonomousCapitalMandate))).scalars().one()
        assert mandate.status == "DRAFT"
        assert mandate.activated_at is None


@pytest.mark.asyncio
async def test_exchange_execution_remains_impossible(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must never touch order execution or the autonomous cycle")

        monkeypatch.setattr(service, "mandate_bootstrap", _forbid)
        monkeypatch.setattr(service, "run_autonomous_preview_cycle", _forbid)

        owner_input = _valid_owner_input()
        created = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=owner_input)
        assert created["overall_status"] == "CREATED"

        status = await service.mandate_bootstrap_create_status(
            capital_campaign_id=2, idempotency_key=owner_input["idempotency_key"]
        )
        assert status["overall_status"] == "COMPLETE_DRAFT"


@pytest.mark.asyncio
async def test_scan_calls_confirms_status_command_is_write_free(monkeypatch: pytest.MonkeyPatch) -> None:
    found = service._mandate_governance_audit_scan_calls(service.mandate_bootstrap_create_status)
    assert found == []


def test_transaction_model_reports_separate_not_atomic() -> None:
    model = service._mandate_bootstrap_create_transaction_model()
    assert model["classification"] == "SEPARATE_IDEMPOTENT_TRANSACTIONS"
    assert "not" in model["description"].lower() and "atomic" in model["description"].lower()


@pytest.mark.asyncio
async def test_created_response_includes_transaction_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_create(capital_campaign_id=2, owner_input=_valid_owner_input())

        assert result["transaction_model"]["classification"] == "SEPARATE_IDEMPOTENT_TRANSACTIONS"


@pytest.mark.asyncio
async def test_status_output_top_level_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_create_status(capital_campaign_id=2, idempotency_key="some-key")

        for key in (
            "overall_status",
            "capital_campaign_id",
            "idempotency_key",
            "mandate_id",
            "mandate_version_id",
            "mandate_status",
            "mandate_version_number",
            "mandate_audit_event",
            "mandate_version_audit_event",
            "identity_coherent",
            "audit_coherent",
            "creation_complete",
            "recovery_required",
            "conflicts",
            "warnings",
            "next_safe_action",
        ):
            assert key in result
        assert result["overall_status"] in {
            "NOT_STARTED",
            "PARTIAL_RECOVERABLE",
            "COMPLETE_DRAFT",
            "CONFLICT",
            "INCOHERENT",
        }
