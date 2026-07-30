from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.services import canonical_preview_package as cpp
from app.services.mandates import lifecycle
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [
    AuditLog.__table__,
    AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__,
    AutonomousCapitalMandateAuthorization.__table__,
    AutonomousCapitalMandateEvaluation.__table__,
    CapitalCampaign.__table__,
    CanonicalPreviewPackage.__table__,
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    PaperAccount.__table__,
    Strategy.__table__,
]

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_runtime_infrastructure(
    *, db: AsyncSession, paper_account_id: uuid.UUID, live_trading_profile_id: uuid.UUID,
    exchange_connection_id: uuid.UUID, provider: str, environment: str,
) -> None:
    """create_mandate()'s _validate_relationships() genuinely checks these rows
    exist -- seed exactly what it needs, same as the real deployment would
    already have onboarded before ever provisioning a mandate."""
    db.add(PaperAccount(
        id=paper_account_id, owner_user_id=uuid.uuid4(), name="test-account",
        asset_class="crypto", starting_balance=Decimal("25"), current_cash_balance=Decimal("25"),
    ))
    db.add(LiveTradingProfile(id=live_trading_profile_id, paper_account_id=paper_account_id, provenance_metadata={}))
    db.add(ExchangeConnection(
        exchange_connection_id=exchange_connection_id, provider=provider, connection_name="kraken-test",
        environment=environment, status="connected", credentials_encrypted="enc",
        api_key_masked="****", api_secret_masked="****", credentials_valid=True,
        balances=[{"currency": "USD", "available": "25"}],
    ))
    await db.flush()


async def _create_active_mandate(
    *, db: AsyncSession, purpose: str, provider: str, environment: str,
    exchange_connection_id: uuid.UUID, live_trading_profile_id: uuid.UUID,
    paper_account_id: uuid.UUID, capital_campaign_row_id: int, key_prefix: str,
) -> AutonomousCapitalMandate:
    """Provisions one fully ACTIVE, authorized LEVEL_2 mandate through the real,
    unmodified governed lifecycle (create_mandate -> create_mandate_version ->
    SUBMIT_FOR_AUTHORIZATION -> authorize_mandate_version -> ACTIVATE) -- the
    same sequence operator_cli.mandate_bootstrap/controlled_proof_mandate_
    bootstrap orchestrate, so this reproduces a real, valid mandate rather than
    a hand-assembled row that happens to satisfy the query."""
    mandate = await lifecycle.create_mandate(
        db=db, owner_actor_id="system:test", autonomy_level="LEVEL_2", provider=provider,
        exchange_environment=environment, exchange_connection_id=exchange_connection_id,
        live_trading_profile_id=live_trading_profile_id, paper_account_id=paper_account_id,
        capital_campaign_id=capital_campaign_row_id, expires_at=None, actor="system:test",
        idempotency_key=f"{key_prefix}:create", reason="test", purpose=purpose,
    )
    version = await lifecycle.create_mandate_version(
        db=db,
        request=MandateVersionCreateRequest(
            mandate_id=mandate.mandate_id, actor="system:test", base_currency="USD",
            authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("25"),
            max_daily_realized_loss_usd=Decimal("10"), max_campaign_drawdown_usd=Decimal("10"),
            max_consecutive_losses=5, position_limit=1, price_evidence_max_age_seconds=300,
            max_slippage_bps=Decimal("50"), max_fee_bps=Decimal("50"),
            allowed_products=("BTC-USD",), allowed_order_sides=("BUY", "SELL"),
            allowed_strategy_versions=(_STRATEGY_IDENTITY,),
            entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={},
            approval_policy="MANDATE_ALLOWED", reconciliation_policy={}, kill_switch_policy={},
            owner_acknowledgements={"accepted": True}, authorization_evidence_summary={"source": "test"},
            idempotency_key=f"{key_prefix}:version", audit_correlation_id=uuid.uuid4(),
        ),
    )
    await lifecycle.apply_mandate_lifecycle_action(
        db=db,
        request=MandateLifecycleActionRequest(
            mandate_id=mandate.mandate_id, actor="system:test", action="SUBMIT_FOR_AUTHORIZATION",
            reason="test", idempotency_key=f"{key_prefix}:submit", audit_correlation_id=uuid.uuid4(),
            software_build_version="test",
        ),
    )
    await lifecycle.authorize_mandate_version(
        db=db,
        request=MandateAuthorizationRequest(
            mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id,
            actor="system:test", authorization_method="owner_signature",
            owner_acknowledgements={"accepted": True}, authorization_evidence={"source": "test"},
            deterministic_explanation={"reason": "test"}, expires_at=None,
            idempotency_key=f"{key_prefix}:authorize",
        ),
    )
    activated = await lifecycle.apply_mandate_lifecycle_action(
        db=db,
        request=MandateLifecycleActionRequest(
            mandate_id=mandate.mandate_id, actor="system:test", action="ACTIVATE",
            reason="test", idempotency_key=f"{key_prefix}:activate", audit_correlation_id=uuid.uuid4(),
            software_build_version="test",
        ),
    )
    return activated


async def _make_package(
    *, db: AsyncSession, campaign_uuid: uuid.UUID, campaign_version: int,
    paper_account_id: uuid.UUID, live_trading_profile_id: uuid.UUID, exchange_connection_id: uuid.UUID,
    strategy_id: uuid.UUID, mandate_id: uuid.UUID | None,
) -> CanonicalPreviewPackage:
    now = datetime.now(timezone.utc)
    package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_uuid, campaign_version=campaign_version,
        runtime_campaign_id=campaign_uuid, paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider="kraken_spot", environment="production", product="BTC-USD", side="BUY",
        proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=strategy_id, strategy_version="1.0.0", parameter_set_id=uuid.uuid4(),
        parameter_set_version="baseline", decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(),
        market_evidence_identity={"exchange_connection_id": str(exchange_connection_id)},
        market_evidence_observed_at=now, preview_expires_at=now + timedelta(minutes=5),
        package_state="READY", generated_at=now, idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
        approval_event_id=None, authorization_source=None, mandate_id=mandate_id, mandate_version_id=None,
        mandate_evaluation_id=None, authorization_expires_at=None, authority_audit_correlation_id=None,
        dry_run_live_crypto_order_id=None,
    )
    db.add(package)
    await db.flush()
    return package


@asynccontextmanager
async def _scope_with_two_active_mandates() -> AsyncIterator[dict]:
    """Reproduces the exact production shape: one ordinary PRODUCTION mandate
    and one dedicated CONTROLLED_PROOF mandate, both genuinely ACTIVE/LEVEL_2,
    for the identical provider/environment/connection/profile/paper_account/
    campaign scope -- the precondition that made the scope-only mandate
    search ambiguous."""
    async with _real_session() as session:
        provider, environment = "kraken_spot", "production"
        exchange_connection_id = uuid.uuid4()
        live_trading_profile_id = uuid.uuid4()
        paper_account_id = uuid.uuid4()
        campaign_uuid = uuid.uuid4()
        campaign = CapitalCampaign(
            uuid=campaign_uuid, owner="test", name="test-campaign", status="READY", campaign_type="TEST",
            exchange=provider, paper_account_id=paper_account_id,
            definition_campaign_id=campaign_uuid, definition_version=1,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
        strategy = Strategy(id=uuid.uuid4(), name="MA Crossover", slug="ma_crossover", module_version="1.0.0", is_active=True)
        session.add(campaign)
        session.add(strategy)
        await session.flush()
        await _seed_runtime_infrastructure(
            db=session, paper_account_id=paper_account_id, live_trading_profile_id=live_trading_profile_id,
            exchange_connection_id=exchange_connection_id, provider=provider, environment=environment,
        )

        production_mandate = await _create_active_mandate(
            db=session, purpose="PRODUCTION", provider=provider, environment=environment,
            exchange_connection_id=exchange_connection_id, live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id, capital_campaign_row_id=campaign.id, key_prefix="prod",
        )
        controlled_proof_mandate = await _create_active_mandate(
            db=session, purpose="CONTROLLED_PROOF", provider=provider, environment=environment,
            exchange_connection_id=exchange_connection_id, live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id, capital_campaign_row_id=campaign.id, key_prefix="cproof",
        )
        assert production_mandate.mandate_id != controlled_proof_mandate.mandate_id

        yield {
            "session": session, "campaign_uuid": campaign_uuid, "campaign": campaign,
            "paper_account_id": paper_account_id, "live_trading_profile_id": live_trading_profile_id,
            "exchange_connection_id": exchange_connection_id, "strategy": strategy,
            "production_mandate": production_mandate, "controlled_proof_mandate": controlled_proof_mandate,
        }


@pytest.mark.asyncio
async def test_controlled_proof_package_selects_only_its_dedicated_mandate() -> None:
    """Reproduces the exact reported production defect and proves the fix:
    with BOTH mandates genuinely ACTIVE for the identical scope, a
    Controlled-Proof-created package (mandate_id already pinned to the
    CONTROLLED_PROOF mandate) authorizes successfully using expected_mandate_id
    + expected_mandate_purpose='CONTROLLED_PROOF', never touching the
    ambiguous scope-only search."""
    async with _scope_with_two_active_mandates() as ctx:
        package = await _make_package(
            db=ctx["session"], campaign_uuid=ctx["campaign_uuid"], campaign_version=1,
            paper_account_id=ctx["paper_account_id"], live_trading_profile_id=ctx["live_trading_profile_id"],
            exchange_connection_id=ctx["exchange_connection_id"], strategy_id=ctx["strategy"].id,
            mandate_id=ctx["controlled_proof_mandate"].mandate_id,
        )

        result = await cpp.authorize_canonical_preview_package_under_mandate(
            db=ctx["session"],
            request=cpp.CanonicalPreviewPackageMandateAuthorizeRequest(
                package_id=package.package_id, idempotency_key="controlled-proof-disambiguation",
                expected_mandate_id=ctx["controlled_proof_mandate"].mandate_id,
                expected_mandate_purpose="CONTROLLED_PROOF",
            ),
        )

        assert result["package"]["mandate_id"] == str(ctx["controlled_proof_mandate"].mandate_id)
        assert package.package_state == "AUTHORIZED"
        assert package.mandate_id == ctx["controlled_proof_mandate"].mandate_id


@pytest.mark.asyncio
async def test_ordinary_production_package_still_selects_its_own_mandate_when_unambiguous() -> None:
    """Normal autonomous production authorization never supplies
    expected_mandate_id -- with only the production mandate ACTIVE (the
    Controlled Proof mandate not yet provisioned, the realistic steady state
    for a campaign that has never run Controlled Proof), the original
    scope-only search still resolves it correctly, completely unchanged."""
    async with _real_session() as session:
        provider, environment = "kraken_spot", "production"
        exchange_connection_id = uuid.uuid4()
        live_trading_profile_id = uuid.uuid4()
        paper_account_id = uuid.uuid4()
        campaign_uuid = uuid.uuid4()
        campaign = CapitalCampaign(
            uuid=campaign_uuid, owner="test", name="test-campaign", status="READY", campaign_type="TEST",
            exchange=provider, paper_account_id=paper_account_id,
            definition_campaign_id=campaign_uuid, definition_version=1,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
        strategy = Strategy(id=uuid.uuid4(), name="MA Crossover", slug="ma_crossover", module_version="1.0.0", is_active=True)
        session.add(campaign)
        session.add(strategy)
        await session.flush()
        await _seed_runtime_infrastructure(
            db=session, paper_account_id=paper_account_id, live_trading_profile_id=live_trading_profile_id,
            exchange_connection_id=exchange_connection_id, provider=provider, environment=environment,
        )

        production_mandate = await _create_active_mandate(
            db=session, purpose="PRODUCTION", provider=provider, environment=environment,
            exchange_connection_id=exchange_connection_id, live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id, capital_campaign_row_id=campaign.id, key_prefix="prod-only",
        )
        package = await _make_package(
            db=session, campaign_uuid=campaign_uuid, campaign_version=1,
            paper_account_id=paper_account_id, live_trading_profile_id=live_trading_profile_id,
            exchange_connection_id=exchange_connection_id, strategy_id=strategy.id,
            mandate_id=production_mandate.mandate_id,
        )

        result = await cpp.authorize_canonical_preview_package_under_mandate(
            db=session,
            request=cpp.CanonicalPreviewPackageMandateAuthorizeRequest(
                package_id=package.package_id, idempotency_key="ordinary-production-unambiguous",
            ),
        )

        assert result["package"]["mandate_id"] == str(production_mandate.mandate_id)
        assert package.package_state == "AUTHORIZED"


@pytest.mark.asyncio
async def test_ordinary_production_path_still_fails_closed_on_genuine_ambiguity() -> None:
    """Requirement: fail-closed behavior for genuine ambiguity must be
    preserved. When the ordinary (non-Controlled-Proof) caller does not
    supply expected_mandate_id and two ACTIVE LEVEL_2 mandates genuinely
    match the same scope, authorization must still refuse to guess -- this
    is unchanged, pre-existing behavior, not weakened by this fix."""
    async with _scope_with_two_active_mandates() as ctx:
        package = await _make_package(
            db=ctx["session"], campaign_uuid=ctx["campaign_uuid"], campaign_version=1,
            paper_account_id=ctx["paper_account_id"], live_trading_profile_id=ctx["live_trading_profile_id"],
            exchange_connection_id=ctx["exchange_connection_id"], strategy_id=ctx["strategy"].id,
            mandate_id=None,
        )

        with pytest.raises(PermissionError, match="ambiguous matching ACTIVE LEVEL_2 mandates"):
            await cpp.authorize_canonical_preview_package_under_mandate(
                db=ctx["session"],
                request=cpp.CanonicalPreviewPackageMandateAuthorizeRequest(
                    package_id=package.package_id, idempotency_key="genuine-ambiguity",
                ),
            )

        refreshed = await ctx["session"].get(CanonicalPreviewPackage, package.package_id)
        assert refreshed.package_state == "READY"
