from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy import BigInteger, event, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import TextClause

from app.models.audit_log import AuditLog
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.strategy import Strategy
from app.services.capital_campaign_orchestration.aggregator_activation import (
    execute_campaign_aggregator_activation,
    fetch_campaign_aggregator_activation_audit,
    inspect_campaign_aggregator_activation,
)
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_IDENTITY


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(element, compiler, **kw) -> str:
    # SQLite's rowid-alias autoincrement only activates for a column whose
    # declared type is the literal string "INTEGER" -- "BIGINT" gets INTEGER
    # affinity but not the alias, so autoincrement PKs silently fail to
    # populate under sqlite unless compiled down to plain INTEGER here.
    return "INTEGER"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)

    tables = [
        CapitalCampaignDefinition.__table__,
        CanonicalPreviewPackage.__table__,
        Strategy.__table__,
        AuditLog.__table__,
    ]
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
                raw = default.arg.text.strip().split("::", 1)[0]
                if raw.endswith("()") and not raw.startswith("("):
                    raw = f"({raw})"
                column.server_default = DefaultClause(text(raw))

    try:
        async with engine.begin() as conn:
            await conn.run_sync(CapitalCampaignDefinition.metadata.create_all, tables=tables)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


CAMPAIGN_ID = uuid.uuid4()
CAMPAIGN_VERSION = 1


async def _seed_definition(session: AsyncSession, *, metadata_evidence=None, compounding_policy=None) -> CapitalCampaignDefinition:
    definition = CapitalCampaignDefinition(
        campaign_id=CAMPAIGN_ID,
        version=CAMPAIGN_VERSION,
        name="Kraken BTC Proving",
        owner_identity="operator:eric",
        capital_budget=Decimal("1000"),
        remaining_unallocated_capital=Decimal("1000"),
        base_currency="USD",
        maximum_open_positions=1,
        maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        profitability_policy_id="default",
        profitability_policy_version="1",
        risk_policy_id="default",
        risk_policy_version="1",
        metadata_evidence=metadata_evidence or {},
        compounding_policy=compounding_policy or {"policy_type": "REINVEST_PERCENTAGE", "reinvestment_percentage": "100"},
        campaign_modes=["COMPOUND", "OPPORTUNITY_SEEKING"],
    )
    session.add(definition)
    await session.flush()
    return definition


async def _seed_activated_package(session: AsyncSession, *, strategy_slug: str, strategy_version: str, package_state: str = "ACTIVATED") -> None:
    strategy = await session.scalar(
        select(Strategy).where(Strategy.slug == strategy_slug).limit(1)
    )
    if strategy is None:
        strategy = Strategy(name=strategy_slug, slug=strategy_slug, module_version=strategy_version, is_active=True)
        session.add(strategy)
        await session.flush()
    now = datetime.now(timezone.utc)
    session.add(
        CanonicalPreviewPackage(
            campaign_id=CAMPAIGN_ID,
            campaign_version=CAMPAIGN_VERSION,
            runtime_campaign_id=uuid.uuid4(),
            paper_account_id=uuid.uuid4(),
            live_trading_profile_id=uuid.uuid4(),
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
            side="BUY",
            proposed_order_amount=Decimal("5"),
            risk_approved_amount=Decimal("5"),
            strategy_id=strategy.id,
            strategy_version=strategy_version,
            parameter_set_id=uuid.uuid4(),
            parameter_set_version="1",
            decision_record_id=uuid.uuid4(),
            risk_event_id=uuid.uuid4(),
            crypto_order_preview_id=uuid.uuid4(),
            preview_expires_at=now,
            package_state=package_state,
            generated_at=now,
            idempotency_key=f"pkg-{uuid.uuid4()}",
            input_fingerprint="fp",
        )
    )
    await session.flush()


# --- readiness ---

@pytest.mark.asyncio
async def test_readiness_reports_continuity_conflict_and_compounding_enabled() -> None:
    async with _real_session() as session:
        await _seed_definition(session)
        await _seed_activated_package(session, strategy_slug="ma_crossover", strategy_version="1.0.0")

        readiness = await inspect_campaign_aggregator_activation(db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION)

        assert readiness.ready is True
        assert readiness.snapshot["already_pinned_to_aggregate_identity"] is False
        assert readiness.snapshot["already_compounding_disabled"] is False
        assert readiness.snapshot["continuity_conflict_risk_if_deployed_now"] is True
        assert len(readiness.snapshot["active_packages_in_continuity_states"]) == 1
        assert readiness.snapshot["active_packages_in_continuity_states"][0]["historical_strategy_identity"] == "ma_crossover@1.0.0"


@pytest.mark.asyncio
async def test_readiness_missing_definition_is_not_ready() -> None:
    async with _real_session() as session:
        readiness = await inspect_campaign_aggregator_activation(db=session, campaign_id=uuid.uuid4(), campaign_version=1)
        assert readiness.ready is False
        assert "campaign_definition_not_found" in readiness.blockers


# --- execute ---

@pytest.mark.asyncio
async def test_execute_requires_confirm() -> None:
    async with _real_session() as session:
        await _seed_definition(session)
        with pytest.raises(PermissionError):
            await execute_campaign_aggregator_activation(
                db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
                actor="operator:eric", reason="test", idempotency_key="key-1", confirm=False,
            )


@pytest.mark.asyncio
async def test_execute_pins_identity_and_disables_compounding_and_leaves_package_untouched() -> None:
    async with _real_session() as session:
        await _seed_definition(session)
        await _seed_activated_package(session, strategy_slug="ma_crossover", strategy_version="1.0.0")

        result = await execute_campaign_aggregator_activation(
            db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
            actor="operator:eric", reason="pre-deploy migration", idempotency_key="key-1", confirm=True,
        )

        assert result.changed is True
        assert result.idempotent is False
        assert result.audit_created is True
        assert result.after["selected_strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY
        assert result.after["reinvestment_percentage"] == "0"

        definition = await session.get(CapitalCampaignDefinition, (await session.execute(
            select(CapitalCampaignDefinition.id).where(CapitalCampaignDefinition.campaign_id == CAMPAIGN_ID)
        )).scalar_one())
        assert definition.metadata_evidence["selected_strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY
        assert definition.compounding_policy["reinvestment_percentage"] == "0"
        # policy_type must be preserved -- only reinvestment_percentage changes.
        assert definition.compounding_policy["policy_type"] == "REINVEST_PERCENTAGE"

        packages = (await session.execute(select(CanonicalPreviewPackage))).scalars().all()
        assert len(packages) == 1
        assert packages[0].package_state == "ACTIVATED"

        post_readiness = await inspect_campaign_aggregator_activation(db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION)
        assert post_readiness.snapshot["continuity_conflict_risk_if_deployed_now"] is False


@pytest.mark.asyncio
async def test_execute_is_idempotent_by_idempotency_key() -> None:
    async with _real_session() as session:
        await _seed_definition(session)

        first = await execute_campaign_aggregator_activation(
            db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
            actor="operator:eric", reason="test", idempotency_key="key-1", confirm=True,
        )
        second = await execute_campaign_aggregator_activation(
            db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
            actor="operator:eric", reason="test", idempotency_key="key-1", confirm=True,
        )

        assert first.changed is True
        assert second.changed is False
        assert second.idempotent is True
        assert second.after == first.after

        audit_rows = (await session.execute(select(AuditLog))).scalars().all()
        assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_execute_is_idempotent_even_with_a_new_idempotency_key_once_state_already_applied() -> None:
    async with _real_session() as session:
        await _seed_definition(
            session,
            metadata_evidence={"selected_strategy_identity": AGGREGATE_STRATEGY_IDENTITY},
            compounding_policy={"policy_type": "REINVEST_PERCENTAGE", "reinvestment_percentage": "0"},
        )

        result = await execute_campaign_aggregator_activation(
            db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
            actor="operator:eric", reason="test", idempotency_key="a-different-key", confirm=True,
        )

        assert result.changed is False
        assert result.idempotent is True
        assert result.audit_created is False

        audit_rows = (await session.execute(select(AuditLog))).scalars().all()
        assert len(audit_rows) == 0


@pytest.mark.asyncio
async def test_execute_missing_definition_raises() -> None:
    async with _real_session() as session:
        with pytest.raises(LookupError):
            await execute_campaign_aggregator_activation(
                db=session, campaign_id=uuid.uuid4(), campaign_version=1,
                actor="operator:eric", reason="test", idempotency_key="key-1", confirm=True,
            )


# --- audit ---

@pytest.mark.asyncio
async def test_audit_returns_prior_execution_records() -> None:
    async with _real_session() as session:
        await _seed_definition(session)
        await execute_campaign_aggregator_activation(
            db=session, campaign_id=CAMPAIGN_ID, campaign_version=CAMPAIGN_VERSION,
            actor="operator:eric", reason="pre-deploy migration", idempotency_key="key-1", confirm=True,
        )

        records = await fetch_campaign_aggregator_activation_audit(db=session, campaign_id=CAMPAIGN_ID, limit=20)

        assert len(records) == 1
        assert records[0]["actor"] == "operator:eric"
        assert records[0]["after_state"]["selected_strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY


@pytest.mark.asyncio
async def test_audit_empty_before_any_execution() -> None:
    async with _real_session() as session:
        await _seed_definition(session)
        records = await fetch_campaign_aggregator_activation_audit(db=session, campaign_id=CAMPAIGN_ID, limit=20)
        assert records == []
