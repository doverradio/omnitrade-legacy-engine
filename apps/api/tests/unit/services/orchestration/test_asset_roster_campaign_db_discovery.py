from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.services.orchestration import asset_roster
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_TABLES = [
    Asset.__table__, AuditLog.__table__, AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__, AutonomousCapitalMandateAuthorization.__table__,
    CapitalCampaign.__table__, CapitalCampaignDefinition.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_TABLES) as session:
        yield session


def _settings(*, campaign_id: uuid.UUID | None, mandate_id: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=campaign_id,
        automatic_mandate_package_activation_mandate_id=mandate_id,
    )


async def _seed_campaign(session: AsyncSession, *, campaign_id: uuid.UUID, allowed_instruments: list[str], status: str = "READY") -> None:
    session.add(
        CapitalCampaignDefinition(
            campaign_id=campaign_id, version=1, name="test", owner_identity="operator:test", status=status,
            capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
            allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=allowed_instruments,
            campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
            minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
            profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
            compounding_policy={"policy_type": "FIXED_CAPITAL"},
        )
    )
    session.add(
        CapitalCampaign(
            uuid=campaign_id, owner="operator:test", name="test", status=status, campaign_type="definition_pinned_runtime",
            definition_campaign_id=campaign_id, definition_version=1,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
    )
    await session.flush()


async def _seed_asset(session: AsyncSession, *, symbol: str, is_active: bool = True) -> None:
    session.add(Asset(symbol=symbol, asset_class="crypto", exchange="kraken_spot", base_currency="USD", is_active=is_active))
    await session.flush()


async def _seed_mandate(
    session: AsyncSession, *, mandate_id: uuid.UUID, allowed_products: tuple[str, ...],
    mandate_status: str = "ACTIVE", autonomy_level: str = "LEVEL_2",
    version_is_active: bool = True, version_is_authorized: bool = True,
    authorization_state: str | None = "AUTHORIZED", revoked_at: datetime | None = None, expires_at: datetime | None = None,
) -> None:
    session.add(AutonomousCapitalMandate(
        mandate_id=mandate_id, owner_actor_id="operator:owner", status=mandate_status, autonomy_level=autonomy_level,
        provider="kraken_spot", exchange_environment="production", exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), capital_campaign_id=None,
        revoked_at=revoked_at, expires_at=expires_at,
    ))
    version_id = uuid.uuid4()
    session.add(AutonomousCapitalMandateVersion(
        mandate_version_id=version_id, mandate_id=mandate_id, version_number=1, version_hash="h1",
        base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("5"),
        max_daily_realized_loss_usd=Decimal("1"), max_campaign_drawdown_usd=Decimal("1"),
        max_consecutive_losses=2, position_limit=1, price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("20"), max_fee_bps=Decimal("50"), allowed_products=list(allowed_products),
        allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[_STRATEGY_IDENTITY],
        entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={}, approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={}, kill_switch_policy={}, owner_acknowledgements={"a": True},
        authorization_evidence_summary={"b": True}, is_authorized=version_is_authorized, is_active=version_is_active,
    ))
    if authorization_state is not None:
        session.add(AutonomousCapitalMandateAuthorization(
            mandate_id=mandate_id, mandate_version_id=version_id, authorization_state=authorization_state,
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", authorized_by_actor_id="operator:owner",
            authorization_method="test", owner_acknowledgements={"a": True}, authorization_evidence={"b": True},
            deterministic_explanation={"c": True}, idempotency_key=f"auth-{mandate_id}",
        ))
    await session.flush()


@pytest.mark.asyncio
async def test_no_configured_campaign_returns_empty_roster() -> None:
    async with _real_session() as session:
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=None, mandate_id=uuid.uuid4()),
        )
    assert products == []


@pytest.mark.asyncio
async def test_no_configured_mandate_returns_empty_roster() -> None:
    async with _real_session() as session:
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=uuid.uuid4(), mandate_id=None),
        )
    assert products == []


@pytest.mark.asyncio
async def test_campaign_not_found_returns_empty_roster() -> None:
    async with _real_session() as session:
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=uuid.uuid4(), mandate_id=uuid.uuid4()),
        )
    assert products == []


@pytest.mark.asyncio
async def test_campaign_not_currently_governing_returns_empty_roster_even_though_a_definition_row_exists() -> None:
    """A definition row exists and the runtime pin points at it, but its
    status is DRAFT (e.g. a brand new campaign never yet promoted) -- must
    resolve to no governing campaign, never to a default roster. Note this is
    NOT what a mid-commissioning-successor window looks like: create_campaign_draft
    deliberately leaves an existing governing (READY) runtime's pin untouched
    while an unvalidated successor is being built, so the previously governing
    version keeps resolving as governing throughout that window instead (see
    asset_commissioning's own test suite for that specific proof)."""
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"], status="DRAFT")
        await _seed_asset(session, symbol="BTC")
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=uuid.uuid4()),
        )
    assert products == []


@pytest.mark.asyncio
async def test_missing_governing_mandate_returns_empty_roster() -> None:
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=uuid.uuid4()),
        )
    assert products == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"mandate_status": "PAUSED"},
        {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
        {"revoked_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"version_is_active": False},
        {"version_is_authorized": False},
        {"authorization_state": "REVOKED"},
        {"autonomy_level": "LEVEL_1"},
    ],
)
async def test_inactive_expired_revoked_or_unauthorized_mandate_returns_empty_roster(kwargs: dict) -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"), **kwargs)
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == []


@pytest.mark.asyncio
async def test_campaign_authorized_but_mandate_disallowed_product_is_excluded() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))  # ETH-USD not authorized
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD"]
    assert "ETH-USD" not in products


@pytest.mark.asyncio
async def test_mandate_authorized_but_campaign_disallowed_product_is_excluded() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])  # ETH-USD not in campaign
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD"]
    assert "ETH-USD" not in products


@pytest.mark.asyncio
async def test_product_authorized_by_both_and_registered_active_is_included() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    # SOL-USD is campaign-authorized but neither mandate-authorized nor asset-registered.
    assert products == ["BTC-USD", "ETH-USD"]


@pytest.mark.asyncio
async def test_governing_predecessor_products_stay_selected_while_an_unrelated_draft_successor_exists() -> None:
    """The real mid-commissioning window: an unvalidated DRAFT successor
    (version 2, adding ETH-USD) has been created, but the runtime pin still
    points at the original, still-READY version 1. BTC-USD -- already
    governing before any of this started -- must keep being selected
    throughout; it must never blank out just because an unrelated successor
    is in flight elsewhere for the same campaign."""
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"], status="READY")
        session.add(CapitalCampaignDefinition(
            campaign_id=campaign_id, version=2, name="test", owner_identity="operator:test", status="DRAFT",
            capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
            allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=["BTC-USD", "ETH-USD"],
            campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
            minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
            profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
            compounding_policy={"policy_type": "FIXED_CAPITAL"},
        ))
        await session.flush()
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD"], "still governed by v1 -- ETH-USD only appears once v2 is actually promoted"


@pytest.mark.asyncio
async def test_inactive_asset_registry_row_excludes_product() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH", is_active=False)
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD"]


@pytest.mark.asyncio
async def test_btc_subject_to_the_same_intersection_no_special_casing() -> None:
    """Correction 2: BTC-USD is included only if it independently passes the
    same four-way check -- it is not force-included."""
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["ETH-USD"])  # BTC-USD not authorized
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("ETH-USD",))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["ETH-USD"]
    assert "BTC-USD" not in products


@pytest.mark.asyncio
async def test_unknown_product_in_allowed_instruments_never_guessed() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "DOGE-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="DOGE")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "DOGE-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD"]
    assert "DOGE-USD" not in products


@pytest.mark.asyncio
async def test_product_ordering_is_deterministic() -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["SOL-USD", "BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_asset(session, symbol="SOL")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("SOL-USD", "ETH-USD", "BTC-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert products == ["BTC-USD", "ETH-USD", "SOL-USD"]


@pytest.mark.asyncio
async def test_btc_included_only_when_active_supported_campaign_and_mandate_authorized() -> None:
    """Required proof #4: BTC-USD is subject to the exact same four-way
    intersection as any other product -- present only when it is
    simultaneously asset-registered+active, campaign-authorized, and
    mandate-authorized. Each dimension is varied independently."""
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()

    # Missing from campaign allowed_instruments -> excluded.
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "ETH-USD"))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id, mandate_id=mandate_id),
        )
    assert "BTC-USD" not in products

    # Missing from mandate allowed_products -> excluded.
    campaign_id2 = uuid.uuid4()
    mandate_id2 = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id2, allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_asset(session, symbol="ETH")
        await _seed_mandate(session, mandate_id=mandate_id2, allowed_products=("ETH-USD",))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id2, mandate_id=mandate_id2),
        )
    assert "BTC-USD" not in products

    # No active Asset Registry row -> excluded.
    campaign_id3 = uuid.uuid4()
    mandate_id3 = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id3, allowed_instruments=["BTC-USD"])
        await _seed_mandate(session, mandate_id=mandate_id3, allowed_products=("BTC-USD",))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id3, mandate_id=mandate_id3),
        )
    assert "BTC-USD" not in products

    # All four satisfied -> included.
    campaign_id4 = uuid.uuid4()
    mandate_id4 = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id4, allowed_instruments=["BTC-USD"])
        await _seed_asset(session, symbol="BTC")
        await _seed_mandate(session, mandate_id=mandate_id4, allowed_products=("BTC-USD",))
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(
            db=session, settings=_settings(campaign_id=campaign_id4, mandate_id=mandate_id4),
        )
    assert products == ["BTC-USD"]


# --- Required proof #5: legacy env mode is completely unaffected -------------------

def test_legacy_env_mode_btc_only_default_is_unchanged() -> None:
    settings = SimpleNamespace(autonomous_cycle_additional_products="", parsed_autonomous_cycle_additional_products=[])
    assert asset_roster.resolve_autonomous_cycle_products(settings=settings) == ["BTC-USD"]


def test_legacy_env_mode_additional_products_behavior_is_unchanged() -> None:
    settings = SimpleNamespace(
        autonomous_cycle_additional_products="ETH-USD,SOL-USD",
        parsed_autonomous_cycle_additional_products=["ETH-USD", "SOL-USD"],
    )
    assert asset_roster.resolve_autonomous_cycle_products(settings=settings) == ["BTC-USD", "ETH-USD", "SOL-USD"]


def test_legacy_env_mode_unknown_product_is_skipped_not_guessed() -> None:
    settings = SimpleNamespace(
        autonomous_cycle_additional_products="ETH-USD,DOGE-USD",
        parsed_autonomous_cycle_additional_products=["ETH-USD", "DOGE-USD"],
    )
    products = asset_roster.resolve_autonomous_cycle_products(settings=settings)
    assert products == ["BTC-USD", "ETH-USD"]
    assert "DOGE-USD" not in products
