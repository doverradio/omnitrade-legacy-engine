from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.controlled_proof import service as controlled_proof_service
from app.services.mandates import lifecycle
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_ALLOWED_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_ALLOWED_PROVIDER = controlled_proof_service.ALLOWED_PROVIDER
_ALLOWED_ENVIRONMENT = controlled_proof_service.ALLOWED_ENVIRONMENT

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


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_runtime_scope(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, int]:
    """Seeds exactly the runtime shape get_controlled_proof_mandate_readiness
    resolves: the pinned ALLOWED_CAMPAIGN_ID's runtime CapitalCampaign row,
    its paper account, live trading profile, and the one authoritative
    connected/credentials-valid exchange connection for provider/environment.
    Returns (paper_account_id, live_trading_profile_id, exchange_connection_id, capital_campaign_row_id)."""
    paper_account_id = uuid.uuid4()
    session.add(PaperAccount(
        id=paper_account_id, owner_user_id=uuid.uuid4(), name="controlled-proof-account",
        asset_class="crypto", starting_balance=Decimal("25"), current_cash_balance=Decimal("25"),
    ))
    campaign = CapitalCampaign(
        uuid=_ALLOWED_CAMPAIGN_ID, owner="test", name="controlled-proof-campaign", status="READY",
        campaign_type="TEST", exchange=_ALLOWED_PROVIDER, paper_account_id=paper_account_id,
        definition_campaign_id=_ALLOWED_CAMPAIGN_ID, definition_version=1,
        starting_capital=Decimal("25"), current_equity=Decimal("25"),
    )
    session.add(campaign)
    profile = LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=paper_account_id,
        provenance_metadata={"provider": _ALLOWED_PROVIDER, "exchange_environment": _ALLOWED_ENVIRONMENT},
    )
    session.add(profile)
    connection_id = uuid.uuid4()
    session.add(ExchangeConnection(
        exchange_connection_id=connection_id, provider=_ALLOWED_PROVIDER, connection_name="kraken-prod",
        environment=_ALLOWED_ENVIRONMENT, status="connected",
        credentials_encrypted="enc", api_key_masked="****", api_secret_masked="****",
        credentials_valid=True, balances=[{"currency": "USD", "available": "25"}],
    ))
    await session.flush()
    return paper_account_id, profile.id, connection_id, campaign.id


def _lifecycle_request(*, mandate_id: uuid.UUID, action: str, idempotency_key: str) -> MandateLifecycleActionRequest:
    return MandateLifecycleActionRequest(
        mandate_id=mandate_id, actor="system:test", action=action, reason=f"test:{action.lower()}",
        idempotency_key=idempotency_key, audit_correlation_id=uuid.uuid4(), software_build_version="build-1",
    )


def _version_request(*, mandate_id: uuid.UUID, idempotency_key: str, allowed_products: tuple[str, ...] = ("BTC-USD",)) -> MandateVersionCreateRequest:
    return MandateVersionCreateRequest(
        mandate_id=mandate_id, actor="system:test", base_currency="USD",
        authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("25"),
        max_daily_realized_loss_usd=Decimal("10"), max_campaign_drawdown_usd=Decimal("10"),
        max_consecutive_losses=5, position_limit=1, price_evidence_max_age_seconds=300,
        max_slippage_bps=Decimal("50"), max_fee_bps=Decimal("50"),
        allowed_products=allowed_products, allowed_order_sides=("BUY", "SELL"),
        allowed_strategy_versions=(_STRATEGY_IDENTITY,),
        entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={},
        approval_policy="MANDATE_ALLOWED", reconciliation_policy={}, kill_switch_policy={},
        owner_acknowledgements={"accepted": True}, authorization_evidence_summary={"source": "owner"},
        idempotency_key=idempotency_key, audit_correlation_id=uuid.uuid4(),
    )


async def _activate_mandate(
    session: AsyncSession, *, mandate: AutonomousCapitalMandate, key_prefix: str,
    allowed_products: tuple[str, ...] = ("BTC-USD",),
) -> None:
    await lifecycle.apply_mandate_lifecycle_action(
        db=session, request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key=f"{key_prefix}-submit"),
    )
    version = await lifecycle.create_mandate_version(
        db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key=f"{key_prefix}-version", allowed_products=allowed_products),
    )
    await lifecycle.authorize_mandate_version(
        db=session,
        request=MandateAuthorizationRequest(
            mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id,
            actor="system:test", authorization_method="owner_signature",
            owner_acknowledgements={"accepted": True}, authorization_evidence={"signature": "hash"},
            deterministic_explanation={"reason": "explicit_owner_authorization"},
            expires_at=None,
            idempotency_key=f"{key_prefix}-auth",
        ),
    )
    await lifecycle.apply_mandate_lifecycle_action(
        db=session, request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key=f"{key_prefix}-activate"),
    )


@pytest.mark.asyncio
async def test_readiness_reports_not_configured_when_setting_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=None))
    async with _real_session() as session:
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["configured"] is False
    assert report["ready"] is False
    assert "controlled_proof_mandate_id is not configured" in report["blockers"]


@pytest.mark.asyncio
async def test_readiness_reports_mandate_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_id = uuid.uuid4()
    monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=missing_id))
    async with _real_session() as session:
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["configured"] is True
    assert report["mandate_found"] is False
    assert report["ready"] is False
    assert "configured mandate_id does not exist" in report["blockers"]


@pytest.mark.asyncio
async def test_readiness_is_true_for_a_fully_correct_controlled_proof_mandate(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        paper_account_id, profile_id, connection_id, campaign_row_id = await _seed_runtime_scope(session)
        mandate = AutonomousCapitalMandate(
            mandate_id=uuid.uuid4(), owner_actor_id="system:test", status="DRAFT",
            autonomy_level="LEVEL_2", purpose="CONTROLLED_PROOF",
            provider=_ALLOWED_PROVIDER, exchange_environment=_ALLOWED_ENVIRONMENT,
            exchange_connection_id=connection_id, live_trading_profile_id=profile_id,
            paper_account_id=paper_account_id, capital_campaign_id=campaign_row_id,
        )
        session.add(mandate)
        await session.flush()
        await _activate_mandate(session, mandate=mandate, key_prefix="happy")
        await session.commit()

        monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=mandate.mandate_id))
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["blockers"] == []
    assert report["ready"] is True
    assert report["purpose"] == "CONTROLLED_PROOF"
    assert report["governing_version_found"] is True


@pytest.mark.asyncio
async def test_readiness_reports_profile_scope_incompatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        paper_account_id, _profile_id, connection_id, campaign_row_id = await _seed_runtime_scope(session)
        wrong_profile_id = uuid.uuid4()  # deliberately NOT the runtime-resolved profile
        mandate = AutonomousCapitalMandate(
            mandate_id=uuid.uuid4(), owner_actor_id="system:test", status="DRAFT",
            autonomy_level="LEVEL_2", purpose="CONTROLLED_PROOF",
            provider=_ALLOWED_PROVIDER, exchange_environment=_ALLOWED_ENVIRONMENT,
            exchange_connection_id=connection_id, live_trading_profile_id=wrong_profile_id,
            paper_account_id=paper_account_id, capital_campaign_id=campaign_row_id,
        )
        session.add(mandate)
        await session.flush()
        await _activate_mandate(session, mandate=mandate, key_prefix="wrong-profile")
        await session.commit()

        monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=mandate.mandate_id))
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["ready"] is False
    assert any("live_trading_profile_id" in blocker for blocker in report["blockers"])


@pytest.mark.asyncio
async def test_readiness_reports_product_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        paper_account_id, profile_id, connection_id, campaign_row_id = await _seed_runtime_scope(session)
        mandate = AutonomousCapitalMandate(
            mandate_id=uuid.uuid4(), owner_actor_id="system:test", status="DRAFT",
            autonomy_level="LEVEL_2", purpose="CONTROLLED_PROOF",
            provider=_ALLOWED_PROVIDER, exchange_environment=_ALLOWED_ENVIRONMENT,
            exchange_connection_id=connection_id, live_trading_profile_id=profile_id,
            paper_account_id=paper_account_id, capital_campaign_id=campaign_row_id,
        )
        session.add(mandate)
        await session.flush()
        await _activate_mandate(session, mandate=mandate, key_prefix="wrong-product", allowed_products=("ETH-USD",))
        await session.commit()

        monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=mandate.mandate_id))
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["ready"] is False
    assert "allowed_products does not include BTC-USD" in report["blockers"]


@pytest.mark.asyncio
async def test_readiness_reports_production_purpose_mandate_as_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PRODUCTION-purpose mandate configured by mistake as
    CONTROLLED_PROOF_MANDATE_ID must never report ready=True."""
    async with _real_session() as session:
        paper_account_id, profile_id, connection_id, campaign_row_id = await _seed_runtime_scope(session)
        mandate = AutonomousCapitalMandate(
            mandate_id=uuid.uuid4(), owner_actor_id="system:test", status="DRAFT",
            autonomy_level="LEVEL_2", purpose="PRODUCTION",
            provider=_ALLOWED_PROVIDER, exchange_environment=_ALLOWED_ENVIRONMENT,
            exchange_connection_id=connection_id, live_trading_profile_id=profile_id,
            paper_account_id=paper_account_id, capital_campaign_id=campaign_row_id,
        )
        session.add(mandate)
        await session.flush()
        await _activate_mandate(session, mandate=mandate, key_prefix="wrong-purpose")
        await session.commit()

        monkeypatch.setattr(controlled_proof_service, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=mandate.mandate_id))
        report = await controlled_proof_service.get_controlled_proof_mandate_readiness(db=session)

    assert report["ready"] is False
    assert "mandate purpose is 'PRODUCTION', expected 'CONTROLLED_PROOF'" in report["blockers"]

