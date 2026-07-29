from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.controlled_proof import service as controlled_proof_service
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [
    AuditLog.__table__,
    AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__,
    AutonomousCapitalMandateAuthorization.__table__,
    CapitalCampaign.__table__,
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    PaperAccount.__table__,
]

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


class _SessionContext:
    """Mirrors AsyncSessionLocal()'s async-context-manager shape but never closes the
    underlying session, so one real sqlite session can back the multiple sequential
    `async with AsyncSessionLocal() as db:` blocks controlled_proof_mandate_bootstrap
    and mandate_bootstrap each open internally."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def __aenter__(self) -> AsyncSession:
        return self._db

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@asynccontextmanager
async def _provisioned_environment(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    """Seeds exactly the runtime shape resolve_controlled_proof_runtime_scope (and
    therefore get_controlled_proof_mandate_readiness) requires: ALLOWED_CAMPAIGN_ID's
    runtime CapitalCampaign row, its paper account, live trading profile, and the one
    connected/credentials-valid exchange connection for Controlled Proof's pinned
    provider/environment -- the exact infrastructure a real deployment would already
    have onboarded before ever provisioning a CONTROLLED_PROOF mandate."""
    async with real_sqlite_session(_ALL_TABLES) as db:
        paper_account_id = uuid.uuid4()
        db.add(PaperAccount(
            id=paper_account_id, owner_user_id=uuid.uuid4(), name="controlled-proof-account",
            asset_class="crypto", starting_balance=Decimal("25"), current_cash_balance=Decimal("25"),
        ))
        db.add(CapitalCampaign(
            uuid=controlled_proof_service.ALLOWED_CAMPAIGN_ID, owner="test", name="controlled-proof-campaign",
            status="READY", campaign_type="TEST", exchange=controlled_proof_service.ALLOWED_PROVIDER,
            paper_account_id=paper_account_id,
            definition_campaign_id=controlled_proof_service.ALLOWED_CAMPAIGN_ID, definition_version=1,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        ))
        profile_id = uuid.uuid4()
        db.add(LiveTradingProfile(
            id=profile_id, paper_account_id=paper_account_id,
            provenance_metadata={
                "provider": controlled_proof_service.ALLOWED_PROVIDER,
                "exchange_environment": controlled_proof_service.ALLOWED_ENVIRONMENT,
            },
        ))
        db.add(ExchangeConnection(
            exchange_connection_id=uuid.uuid4(), provider=controlled_proof_service.ALLOWED_PROVIDER,
            connection_name="kraken-prod", environment=controlled_proof_service.ALLOWED_ENVIRONMENT,
            status="connected", credentials_encrypted="enc", api_key_masked="****", api_secret_masked="****",
            credentials_valid=True, balances=[{"currency": "USD", "available": "25"}],
        ))
        await db.flush()
        await db.commit()

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        yield db


@pytest.mark.asyncio
async def test_controlled_proof_mandate_bootstrap_reaches_readiness_true_via_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Proves the full 'fresh deployment' path this feature exists for: provision a
    dedicated CONTROLLED_PROOF mandate using only governed lifecycle calls (no SQL),
    persist CONTROLLED_PROOF_MANDATE_ID via the real .env-file mechanism Settings
    actually loads from (not a bare monkeypatched value), and confirm
    get_controlled_proof_mandate_readiness reports ready=True and zero blockers."""
    env_file = tmp_path / ".env"

    async with _provisioned_environment(monkeypatch) as db:
        result = await service.controlled_proof_mandate_bootstrap(
            actor="operator:human",
            reason="controlled_proof_mandate_provisioning",
            idempotency_key="controlled-proof-mandate-1",
            allowed_strategy_versions=(_STRATEGY_IDENTITY,),
            confirm=True,
            env_file=env_file,
        )

        assert result["status"] == "ACTIVE"
        assert result["purpose"] == "CONTROLLED_PROOF"
        assert result["controlled_proof_mandate_id_written"] is True
        assert result["env_file"] == str(env_file)

        mandate_id = uuid.UUID(result["mandate_id"])
        assert env_file.read_text().strip() == f"CONTROLLED_PROOF_MANDATE_ID={mandate_id}"

        # Constructs the REAL Settings class from the file this feature actually wrote,
        # rather than monkeypatching the value away -- the point being proven is that
        # the supported (env-file) configuration mechanism genuinely works end to end.
        settings = Settings(_env_file=env_file)
        assert settings.controlled_proof_mandate_id == mandate_id

        monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: settings)
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=db)

        assert report["blockers"] == []
        assert report["ready"] is True
        assert report["purpose"] == "CONTROLLED_PROOF"
        assert report["governing_version_found"] is True

        mandates = (await db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        authorizations = (await db.execute(select(AutonomousCapitalMandateAuthorization))).scalars().all()
        assert len(mandates) == 1
        assert len(versions) == 1
        assert len(authorizations) == 1
        assert versions[0].max_order_notional_usd == controlled_proof_service.MAX_NOTIONAL_USD
        assert versions[0].max_open_exposure_usd == controlled_proof_service.MAX_NOTIONAL_USD
        assert versions[0].position_limit == 1
        assert versions[0].allowed_products == ["BTC-USD"]
        assert set(versions[0].allowed_order_sides) == {"BUY", "SELL"}


@pytest.mark.asyncio
async def test_controlled_proof_mandate_bootstrap_rejects_without_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async with _provisioned_environment(monkeypatch) as db:
        with pytest.raises(PermissionError, match="confirm"):
            await service.controlled_proof_mandate_bootstrap(
                actor="operator:human", reason="test", idempotency_key="controlled-proof-noconfirm",
                allowed_strategy_versions=(_STRATEGY_IDENTITY,), confirm=False, env_file=tmp_path / ".env",
            )

        mandates = (await db.execute(select(AutonomousCapitalMandate))).scalars().all()
        assert mandates == []


@pytest.mark.asyncio
async def test_controlled_proof_mandate_bootstrap_idempotent_rerun_creates_no_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    async with _provisioned_environment(monkeypatch) as db:
        kwargs: dict[str, Any] = dict(
            actor="operator:human", reason="controlled_proof_mandate_provisioning",
            idempotency_key="controlled-proof-idempotent",
            allowed_strategy_versions=(_STRATEGY_IDENTITY,), confirm=True, env_file=env_file,
        )

        first = await service.controlled_proof_mandate_bootstrap(**kwargs)
        second = await service.controlled_proof_mandate_bootstrap(**kwargs)

        assert first["mandate_id"] == second["mandate_id"]
        assert second["status"] == "ACTIVE"

        mandates = (await db.execute(select(AutonomousCapitalMandate))).scalars().all()
        assert len(mandates) == 1
        # The env file is upserted, not appended to, on rerun.
        assert env_file.read_text().strip() == f"CONTROLLED_PROOF_MANDATE_ID={first['mandate_id']}"


@pytest.mark.asyncio
async def test_controlled_proof_mandate_bootstrap_fails_closed_when_runtime_scope_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No ALLOWED_CAMPAIGN_ID row (or exchange connection) seeded at all -- an
    unprovisioned/incomplete deployment -- must fail loudly rather than guess a scope."""
    async with real_sqlite_session(_ALL_TABLES) as db:
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        with pytest.raises(Exception):
            await service.controlled_proof_mandate_bootstrap(
                actor="operator:human", reason="test", idempotency_key="controlled-proof-no-scope",
                allowed_strategy_versions=(_STRATEGY_IDENTITY,), confirm=True, env_file=tmp_path / ".env",
            )

        mandates = (await db.execute(select(AutonomousCapitalMandate))).scalars().all()
        assert mandates == []
