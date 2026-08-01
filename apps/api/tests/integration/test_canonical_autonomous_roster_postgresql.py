import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.asset import Asset
from app.models.candle import Candle
from app.models.controlled_proof_run import ControlledProofRun
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.strategy_aggregate_decision import StrategyAggregateDecision
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.capital_campaign_orchestration.authoritative import resolve_and_persist_strategy_aggregate_evidence
from app.services.strategy_roster.contracts import StrategyRosterRequest
from app.services.strategy_roster.registry import ENABLED_PHASE1_ROSTER
from app.services.strategy_roster.service import run_strategy_roster_for_candle
from tests.support.canonical_autonomous_roster_postgresql import build_canonical_buy_roster


URL = os.getenv("AUTONOMOUS_ROSTER_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="disposable PostgreSQL URL required")


async def _truncate_application_tables(session):
    await session.execute(text("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'
      LOOP EXECUTE format('TRUNCATE TABLE %I CASCADE', r.tablename); END LOOP; END $$"""))
    await session.commit()


@pytest.mark.asyncio
async def test_real_roster_and_aggregate_buy_are_replay_safe():
    engine = create_async_engine(URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await _truncate_application_tables(session)
            fixture = await build_canonical_buy_roster(db=session)
            assert fixture.final_action == "BUY"

            run = await session.get(StrategyRosterRun, fixture.roster_run_id)
            assert run is not None
            assert (run.provider, run.product_id, run.interval, run.trigger) == (
                "kraken_spot", "BTC-USD", "15m", "kraken_btc_15m_candle_close",
            )
            assert run.scheduled_cycle_id == fixture.scheduled_cycle_id
            assert run.candle_close_time == fixture.candle_close_time
            assert (run.strategies_requested_count, run.strategies_completed_count, run.strategies_failed_count) == (7, 7, 0)
            assert (run.buy_count, run.sell_count, run.hold_count) == (5, 1, 1)

            proposals = list((await session.scalars(
                select(StrategyRosterProposal).order_by(StrategyRosterProposal.strategy_slug)
            )).all())
            assert len(proposals) == len(ENABLED_PHASE1_ROSTER)
            assert {item.strategy_slug for item in proposals} == set(ENABLED_PHASE1_ROSTER)
            assert all(item.strategy_id is not None and item.parameter_set_id is not None for item in proposals)
            assert all(item.evaluation_status == "EVALUATED" for item in proposals)
            assert all(item.roster_run_id == fixture.roster_run_id for item in proposals)
            assert all(item.asset_id == fixture.asset_id for item in proposals)
            assert all(item.provider == fixture.provider and item.product_id == fixture.product_id for item in proposals)
            assert all(item.interval == fixture.interval and item.candle_close_time == fixture.candle_close_time for item in proposals)
            assert all(item.scheduled_cycle_id == fixture.scheduled_cycle_id for item in proposals)
            assert all(item.execution_mode == "SHADOW" and item.live_submission_allowed is False for item in proposals)

            aggregate = await session.get(StrategyAggregateDecision, fixture.aggregate_decision_id)
            assert aggregate is not None
            assert aggregate.final_action == "BUY"
            assert aggregate.roster_run_id == fixture.roster_run_id
            assert aggregate.asset_id == fixture.asset_id
            assert aggregate.candle_close_time == fixture.candle_close_time
            assert (aggregate.provider, aggregate.product_id, aggregate.interval) == ("kraken_spot", "BTC-USD", "15m")
            assert aggregate.eligible_strategy_count == 7
            assert aggregate.weighted_buy_score > aggregate.weighted_sell_score
            assert await session.scalar(select(func.count()).select_from(ControlledProofRun)) == 0

            replay = await run_strategy_roster_for_candle(db=session, request=StrategyRosterRequest(
                asset_id=fixture.asset_id, provider=fixture.provider, product_id=fixture.product_id,
                interval=fixture.interval, candle_open_time=fixture.candle_open_time,
                candle_close_time=fixture.candle_close_time, trigger=fixture.trigger,
                scheduled_cycle_id=fixture.scheduled_cycle_id,
            ))
            assert replay.replayed is True
            assert replay.roster_run_id == fixture.roster_run_id

            asset = await session.get(Asset, fixture.asset_id)
            candle = await session.get(Candle, fixture.candle_id)
            evidence, blocker = await resolve_and_persist_strategy_aggregate_evidence(
                db=session, asset_id=fixture.asset_id, product_id=fixture.product_id,
                interval=fixture.interval, campaign_id=fixture.campaign_id, campaign_version=1,
                environment="production", paper_account_id=fixture.account_id,
                runtime_campaign_id=fixture.runtime_campaign_id, asset=asset, candle_item=candle,
                now=datetime.now(timezone.utc), required_trigger=fixture.trigger,
                scheduled_cycle_id=fixture.scheduled_cycle_id,
            )
            assert blocker is None and evidence is not None
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(StrategyRosterRun)) == 1
            assert await session.scalar(select(func.count()).select_from(StrategyRosterProposal)) == 7
            assert await session.scalar(select(func.count()).select_from(StrategyAggregateDecision)) == 1
            assert await session.scalar(select(func.count()).select_from(DecisionRecord)) == 1
            assert await session.scalar(select(func.count()).select_from(DecisionSnapshot)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_injected_failure_rolls_back_aggregate_unit_without_false_buy():
    engine = create_async_engine(URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await _truncate_application_tables(session)
            fixture = await build_canonical_buy_roster(db=session, commit_aggregate=False)
            assert await session.scalar(select(func.count()).select_from(StrategyAggregateDecision)) == 1
            assert await session.scalar(select(func.count()).select_from(DecisionRecord)) == 1
            assert await session.scalar(select(func.count()).select_from(DecisionSnapshot)) == 1

            # Simulate failure in the caller-owned composition transaction.
            await session.rollback()

            assert await session.scalar(select(func.count()).select_from(StrategyRosterRun)) == 1
            assert await session.scalar(select(func.count()).select_from(StrategyRosterProposal)) == 7
            assert await session.scalar(select(func.count()).select_from(StrategyAggregateDecision)) == 0
            assert await session.scalar(select(func.count()).select_from(DecisionRecord)) == 0
            assert await session.scalar(select(func.count()).select_from(DecisionSnapshot)) == 0
    finally:
        await engine.dispose()
