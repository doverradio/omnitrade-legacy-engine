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


async def _table_rows(db: AsyncSession, model: Any) -> list[Any]:
    return list((await db.execute(select(model))).scalars().all())


@pytest.mark.asyncio
async def test_commissioning_succeeds_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=_valid_owner_input())

        assert result["overall_status"] == "COMMISSIONED"
        assert result["mandate_id"] is not None
        assert result["mandate_version_id"] is not None
        assert result["integrity_status"] == "COMPLETE_DRAFT"
        assert result["current_state"] == {"mandate_status": "DRAFT", "is_authorized": False, "is_active": False}
        assert result["transaction_model"]["classification"] == "SEPARATE_IDEMPOTENT_TRANSACTIONS"
        assert result["audit_summary"]["governance_audit_status"] == "READY_FOR_STAGE9"

        assert len(await _table_rows(db, AutonomousCapitalMandate)) == 1
        assert len(await _table_rows(db, AutonomousCapitalMandateVersion)) == 1
        assert await _table_rows(db, AutonomousCapitalMandateAuthorization) == []


@pytest.mark.asyncio
async def test_aborts_with_zero_writes_when_governance_audit_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        # Force the governance audit itself to report NOT_READY, exactly as Stage 8's own
        # tests prove is possible when a validator is shadowed.
        monkeypatch.setattr(
            service,
            "validate_mandate_version",
            lambda version: ValidationResult(valid=True, reason=None),
        )

        result = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=_valid_owner_input())

        assert result["overall_status"] == "ABORTED_NOT_READY"
        assert result["mandate_id"] is None
        assert result["mandate_version_id"] is None
        assert result["current_state"] is None
        assert await _table_rows(db, AutonomousCapitalMandate) == []


@pytest.mark.asyncio
async def test_passes_through_failed_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()
        del owner_input["actor"]

        result = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=owner_input)

        assert result["overall_status"] == "FAILED_VALIDATION"
        assert result["mandate_id"] is None
        assert await _table_rows(db, AutonomousCapitalMandate) == []


@pytest.mark.asyncio
async def test_passes_through_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)
        owner_input = _valid_owner_input()

        first = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=owner_input)
        assert first["overall_status"] == "COMMISSIONED"

        conflicting_input = _valid_owner_input(authorized_capital_usd="999999")
        second = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=conflicting_input)

        assert second["overall_status"] == "CONFLICT"
        assert len(await _table_rows(db, AutonomousCapitalMandateVersion)) == 1


@pytest.mark.asyncio
async def test_authorization_activation_and_trading_remain_impossible(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        def _forbid(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("mandate_bootstrap_commission must never touch lifecycle/authorization/trading")

        # Deliberately does not monkeypatch mandate_bootstrap itself: the governance audit
        # this command runs first inspects that function's own source (proving its write
        # path stays gated), not merely avoids calling it -- replacing it here would break
        # the gate-detection check for a reason unrelated to what this test asserts (see
        # the identical fix in test_mandate_governance_readiness_audit.py).
        monkeypatch.setattr(service, "authorize_mandate_version", _forbid)
        monkeypatch.setattr(service, "apply_mandate_lifecycle_action", _forbid)
        monkeypatch.setattr(service, "run_autonomous_preview_cycle", _forbid)

        result = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=_valid_owner_input())

        assert result["overall_status"] == "COMMISSIONED"
        mandate = (await db.execute(select(AutonomousCapitalMandate))).scalars().one()
        assert mandate.status == "DRAFT"
        assert mandate.activated_at is None
        assert await _table_rows(db, AutonomousCapitalMandateAuthorization) == []


@pytest.mark.asyncio
async def test_scan_calls_confirms_no_forbidden_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    found = service._mandate_governance_audit_scan_calls(service.mandate_bootstrap_commission)
    assert found == []


@pytest.mark.asyncio
async def test_output_top_level_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async with real_sqlite_session(_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
        await _seed_fully_resolved_campaign(db)

        result = await service.mandate_bootstrap_commission(capital_campaign_id=2, owner_input=_valid_owner_input())

        for key in (
            "mandate_id",
            "mandate_version_id",
            "audit_summary",
            "integrity_status",
            "transaction_model",
            "current_state",
            "next_required_action",
            "overall_status",
        ):
            assert key in result
