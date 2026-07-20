from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import TextClause

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.models.strategy_aggregate_decision import StrategyAggregateDecision
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.capital_campaign_orchestration import authoritative
from app.services.capital_campaign_orchestration.authoritative import (
    AGGREGATE_STRATEGY_IDENTITY,
    _load_latest_strategy_evidence,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        # postgresql.UUID(as_uuid=True)'s bind_processor compares using
        # value.hex (dashless); a dashed str(uuid4()) default would silently
        # never match in later WHERE-clause lookups under sqlite.
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)

    tables = [
        StrategyRosterRun.__table__,
        StrategyRosterProposal.__table__,
        DecisionRecord.__table__,
        DecisionSnapshot.__table__,
        StrategyAggregateDecision.__table__,
        Strategy.__table__,
        ParameterSet.__table__,
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
            await conn.run_sync(StrategyRosterRun.metadata.create_all, tables=tables)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ASSET_ID = uuid.uuid4()
CAMPAIGN_ID = uuid.uuid4()


async def _seed_roster_run_and_proposals(session: AsyncSession, *, actions: dict[str, str]) -> uuid.UUID:
    roster_run_id = uuid.uuid4()
    run = StrategyRosterRun(
        roster_run_id=roster_run_id,
        idempotency_key=f"run-{uuid.uuid4()}",
        asset_id=ASSET_ID,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
        candle_open_time=NOW,
        candle_close_time=NOW,
        trigger="kraken_btc_15m_candle_close",
        strategies_requested=list(actions.keys()),
        strategies_completed=list(actions.keys()),
        strategies_failed=[],
        strategies_requested_count=len(actions),
        strategies_completed_count=len(actions),
        strategies_failed_count=0,
        buy_count=sum(1 for a in actions.values() if a == "BUY"),
        sell_count=sum(1 for a in actions.values() if a == "SELL"),
        hold_count=sum(1 for a in actions.values() if a == "HOLD"),
        started_at=NOW,
        completed_at=NOW,
    )
    session.add(run)
    await session.flush()

    for slug, action in actions.items():
        session.add(
            StrategyRosterProposal(
                idempotency_key=f"proposal-{slug}-{uuid.uuid4()}",
                roster_run_id=roster_run_id,
                asset_id=ASSET_ID,
                provider="kraken_spot",
                product_id="BTC-USD",
                interval="15m",
                candle_open_time=NOW,
                candle_close_time=NOW,
                strategy_slug=slug,
                strategy_version="1.0.0",
                strategy_identity=f"{slug}@1.0.0",
                parameter_set_identity="default",
                evaluated_at=NOW,
                action=action,
                evaluation_status="EVALUATED",
                strength=None,
                confidence=None,
                reason="test",
                minimum_history_required=1,
                history_candle_count=10,
            )
        )
    await session.flush()
    return roster_run_id


def _patch_flat_position(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_position_evidence(**_kwargs):
        return {"authority_class": "AUTHORITATIVE", "position": None}

    monkeypatch.setattr(authoritative, "_load_position_evidence", _fake_position_evidence)


def _patch_no_scorecards(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_scorecards(**_kwargs):
        return []

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _fake_scorecards)


async def _call_aggregator(session: AsyncSession) -> tuple[dict[str, Any] | None, str | None]:
    return await _load_latest_strategy_evidence(
        db=session,
        asset_id=ASSET_ID,
        product_id="BTC-USD",
        interval="15m",
        campaign_id=CAMPAIGN_ID,
        campaign_version=1,
        environment="production",
        paper_account_id=uuid.uuid4(),
        runtime_campaign_id=1,
        asset=SimpleNamespace(id=ASSET_ID),
        candle_item=SimpleNamespace(close="60000", interval="15m"),
        now=NOW,
    )


# 13. authoritative campaign consumes the aggregate decision
@pytest.mark.asyncio
async def test_authoritative_evidence_reflects_multi_strategy_aggregate_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})

        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "BUY"
        assert evidence["aggregate_evidence"]["eligible_strategy_count"] == 3
        # Item 1 of the production-safety review: the reported identity must be
        # the stable canonical aggregate identity, never an arbitrary contributor.
        assert evidence["strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY

        decision_record_id = uuid.UUID(evidence["source_identity"]["decision_record_id"])
        record = await session.get(DecisionRecord, decision_record_id)
        assert record is not None
        assert record.generated_signals[0]["action"] == "BUY"
        assert len(record.supporting_strategies) == 3


# 14 (partial -- proves the aggregate never bypasses HOLD classification when
# a position doesn't support the action) + 5
@pytest.mark.asyncio
async def test_sell_majority_without_position_resolves_to_hold_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "SELL", "momentum": "SELL", "breakout": "SELL"})

        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "HOLD"


# 11 + 12: duplicate roster run is idempotent / deterministic replay
@pytest.mark.asyncio
async def test_repeated_processing_is_idempotent_and_replay_is_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})

        first_evidence, _ = await _call_aggregator(session)
        second_evidence, _ = await _call_aggregator(session)

        assert first_evidence["action"] == second_evidence["action"] == "BUY"
        assert (
            first_evidence["source_identity"]["aggregate_decision_id"]
            == second_evidence["source_identity"]["aggregate_decision_id"]
        )
        assert (
            first_evidence["source_identity"]["decision_record_id"]
            == second_evidence["source_identity"]["decision_record_id"]
        )

        rows = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(rows) == 1


# Item 1 of the production-safety review: the canonical package system must be
# able to resolve the aggregate identity exactly like any other strategy
# identity (real Strategy + ParameterSet catalog rows, not a fictional string).
@pytest.mark.asyncio
async def test_canonical_package_system_can_resolve_the_aggregate_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.canonical_preview_package import _resolve_strategy_and_parameter_binding

    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})
        evidence, _ = await _call_aggregator(session)
        assert evidence["strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY

        strategy, parameter_set = await _resolve_strategy_and_parameter_binding(
            db=session, strategy_identity=evidence["strategy_identity"]
        )
        assert strategy is not None
        assert strategy.slug == "strategy_roster_aggregate"
        assert strategy.is_active is True
        assert parameter_set is not None
        assert parameter_set.strategy_id == strategy.id


@pytest.mark.asyncio
async def test_ensure_aggregate_strategy_catalog_entry_is_idempotent() -> None:
    async with _real_session() as session:
        await authoritative._ensure_aggregate_strategy_catalog_entry(db=session, actor="test")
        await authoritative._ensure_aggregate_strategy_catalog_entry(db=session, actor="test")

        strategies = (await session.execute(Strategy.__table__.select())).fetchall()
        parameter_sets = (await session.execute(ParameterSet.__table__.select())).fetchall()
        assert len(strategies) == 1
        assert len(parameter_sets) == 1


# Item 2 of the production-safety review: the pure-read entry point must never
# write, and a downstream failure after the aggregator has flushed its rows
# must not leave any partial/incoherent record surviving a rollback.
@pytest.mark.asyncio
async def test_pure_read_entry_point_performs_zero_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        roster_run_id = await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})

        evidence, reason = await authoritative.load_strategy_aggregate_evidence(
            db=session,
            roster_run_id=roster_run_id,
            asset_id=ASSET_ID,
            candle_close_time=NOW,
            campaign_id=CAMPAIGN_ID,
            campaign_version=1,
            config_version="v1",
            environment="production",
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
        )

        assert evidence is None
        assert reason == "not_yet_computed"
        # Nothing this pure-read call could have written exists.
        assert (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall() == []
        assert (await session.execute(DecisionRecord.__table__.select())).fetchall() == []
        assert (await session.execute(Strategy.__table__.select())).fetchall() == []


@pytest.mark.asyncio
async def test_downstream_failure_after_flush_leaves_no_partial_records_after_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)

    class _InjectedFailure(Exception):
        pass

    async def _failing_catalog_entry(**_kwargs):
        raise _InjectedFailure("simulated downstream composition failure after evidence rows were flushed")

    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})

        # DecisionRecord/DecisionSnapshot are flushed before the catalog-entry
        # step inside _persist_strategy_aggregate_decision; failing exactly
        # there proves an exception after some rows are already flushed still
        # leaves nothing behind once the caller rolls back.
        monkeypatch.setattr(authoritative, "_ensure_aggregate_strategy_catalog_entry", _failing_catalog_entry)

        with pytest.raises(_InjectedFailure):
            await _call_aggregator(session)

        await session.rollback()

        assert (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall() == []
        assert (await session.execute(DecisionRecord.__table__.select())).fetchall() == []
        assert (await session.execute(DecisionSnapshot.__table__.select())).fetchall() == []


# 9. insufficient eligible strategies -> HOLD / fail closed, end to end
@pytest.mark.asyncio
async def test_single_strategy_proposal_fails_closed_to_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY"})

        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "HOLD"
        assert evidence["aggregate_evidence"]["eligible_strategy_count"] == 1


# 15. no direct exchange submission from the aggregator
def test_aggregator_never_calls_order_submission() -> None:
    from app.services.strategy_roster import decision_aggregator

    forbidden = ("submit_order", "create_order", "place_order", "execute_order", "orchestrate_paper_signal_execution", "get_exchange_provider")
    source = inspect.getsource(decision_aggregator)
    for token in forbidden:
        assert token not in source, f"decision_aggregator.py must never reference {token}"

    persist_source = inspect.getsource(authoritative._persist_strategy_aggregate_decision)
    load_source = inspect.getsource(authoritative.resolve_or_create_strategy_aggregate_evidence)
    for token in forbidden:
        assert token not in persist_source
        assert token not in load_source
