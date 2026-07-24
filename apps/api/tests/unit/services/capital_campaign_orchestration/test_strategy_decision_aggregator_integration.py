from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
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
from app.schemas.capital_campaign_domain import CampaignCompoundingPolicy, CampaignProfitDistributionPolicy
from app.services.capital_campaign_domain.preview_engine import _validate_percentages
from app.services.capital_campaign_orchestration import authoritative
from app.services.capital_campaign_orchestration.authoritative import (
    AGGREGATE_STRATEGY_IDENTITY,
    resolve_and_persist_strategy_aggregate_evidence,
)
from app.services.strategy_outcomes.service import StrategyScorecard, StrategyScorecardBucket


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


class _AwaitableSession:
    """Minimal AsyncSession-shaped adapter over a real synchronous ORM Session.

    This keeps all real SQL, constraints, defaults and mapper events while
    avoiding aiosqlite's connection-worker deadlock in the sandbox.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    async def flush(self) -> None:
        self._session.flush()

    async def execute(self, statement):
        return self._session.execute(statement)

    async def scalar(self, statement):
        return self._session.scalar(statement)

    async def get(self, entity, ident):
        return self._session.get(entity, ident)

    async def rollback(self) -> None:
        self._session.rollback()

    @asynccontextmanager
    async def begin_nested(self):
        with self._session.begin_nested():
            yield


@asynccontextmanager
async def _real_session() -> AsyncIterator[_AwaitableSession]:
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
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
        StrategyRosterRun.metadata.create_all(engine, tables=tables)
        with Session(engine, expire_on_commit=False) as session:
            yield _AwaitableSession(session)
    finally:
        engine.dispose()


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ASSET_ID = uuid.uuid4()
CAMPAIGN_ID = uuid.uuid4()


async def _seed_roster_run_and_proposals(
    session: _AwaitableSession,
    *,
    actions: dict[str, str],
    trigger: str = "kraken_btc_15m_candle_close",
    complete: bool = True,
) -> uuid.UUID:
    roster_run_id = uuid.uuid4()
    failed_entries = [] if complete else [{"strategy_slug": "injected", "reason": "injected"}]
    run = StrategyRosterRun(
        roster_run_id=roster_run_id,
        idempotency_key=f"run-{uuid.uuid4()}",
        asset_id=ASSET_ID,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
        candle_open_time=NOW,
        candle_close_time=NOW,
        trigger=trigger,
        strategies_requested=list(actions.keys()),
        strategies_completed=list(actions.keys()) if complete else [],
        strategies_failed=failed_entries,
        strategies_requested_count=len(actions),
        strategies_completed_count=len(actions) if complete else 0,
        strategies_failed_count=0 if complete else 1,
        buy_count=sum(1 for a in actions.values() if a == "BUY"),
        sell_count=sum(1 for a in actions.values() if a == "SELL"),
        hold_count=sum(1 for a in actions.values() if a == "HOLD"),
        # strategy_roster.service always persists error_summary as
        # {"failed": failed}, a non-empty dict even when failed == [] -- mirror
        # that exact shape here so this harness doesn't mask the truthy-dict
        # regression (production evidence: roster_run_incomplete_or_failed on
        # a fully successful roster) behind a falsy-by-default test fixture.
        error_summary={"failed": failed_entries},
        started_at=NOW,
        completed_at=NOW if complete else None,
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


async def _call_aggregator(
    session: _AwaitableSession, *, preferred_strategy_identity: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    return await resolve_and_persist_strategy_aggregate_evidence(
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
        candle_item=SimpleNamespace(close="60000", interval="15m", close_time=NOW),
        now=NOW,
        required_trigger="kraken_btc_15m_candle_close",
        preferred_strategy_identity=preferred_strategy_identity,
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


def _one_sided_scorecard_bucket(*, buy_avg: Decimal, sell_avg: Decimal, blended_avg: Decimal) -> StrategyScorecardBucket:
    return StrategyScorecardBucket(
        horizon_label="aggregate",
        total_evaluated=3,
        buy_evaluations=2,
        buy_correct=2,
        sell_evaluations=1,
        sell_correct=0,
        hold_evaluations=0,
        hold_correct=0,
        overall_correct_pct=Decimal("66.6667"),
        average_raw_return_pct=blended_avg,
        average_fee_adjusted_return_pct=blended_avg,
        average_mfe_pct=Decimal("0"),
        average_mae_pct=Decimal("0"),
        buy_average_fee_adjusted_return_pct=buy_avg,
        sell_average_fee_adjusted_return_pct=sell_avg,
        hold_average_fee_adjusted_return_pct=None,
    )


# Reproduces the production defect where a BUY decision's economic edge
# estimate (profitable_after_fees_performance) was drawn from a scorecard
# aggregate that blends the dominant contributor's BUY, SELL, and HOLD
# outcome history together -- so a strategy with a genuinely profitable BUY
# track record and an unrelated losing SELL track record surfaced as
# "historically unprofitable" for a proposed BUY, and the net-edge gate
# rejected on evidence that had nothing to do with the action being taken.
@pytest.mark.asyncio
async def test_buy_evidence_profitability_uses_buy_scoped_history_not_blended_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_flat_position(monkeypatch)

    one_sided_bucket = _one_sided_scorecard_bucket(
        buy_avg=Decimal("1.5000"), sell_avg=Decimal("-10.0000"), blended_avg=Decimal("-2.3333")
    )

    async def _fake_scorecards(**_kwargs):
        return [
            StrategyScorecard(
                strategy_slug="breakout",
                per_horizon=[],
                aggregate=one_sided_bucket,
                best_regime=None,
                worst_regime=None,
                regime_evidence_count=3,
                regime_min_evidence_required=50,
            )
        ]

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _fake_scorecards)

    async with _real_session() as session:
        # All three equal-weight BUY votes tie; "breakout" wins the
        # alphabetical tie-break and becomes dominant_contributor_identity,
        # so its scorecard is the one surfaced into evidence.
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"})
        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "BUY"
        assert evidence["source_identity"]["scorecard_strategy_slug"] == "breakout"
        # The economic-edge figure must equal the BUY-scoped average, not the
        # blended (BUY+SELL+HOLD) average, which is negative.
        assert evidence["profitable_after_fees_performance"] == "1.5000"
        assert evidence["scorecard"]["blended_average_fee_adjusted_return_pct"] == "-2.3333"
        assert evidence["scorecard"]["action_scoped_average_fee_adjusted_return_pct"] == "1.5000"


@pytest.mark.asyncio
async def test_hold_evidence_profitability_is_none_when_no_hold_scoped_history_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dominant contributor with only BUY/SELL history and no HOLD outcomes
    must not have a HOLD decision's (unused, but should-be-absent) edge figure
    silently backed by the blended aggregate either."""
    _patch_flat_position(monkeypatch)

    one_sided_bucket = _one_sided_scorecard_bucket(
        buy_avg=Decimal("1.5000"), sell_avg=Decimal("-10.0000"), blended_avg=Decimal("-2.3333")
    )

    async def _fake_scorecards(**_kwargs):
        return [
            StrategyScorecard(
                strategy_slug="breakout",
                per_horizon=[],
                aggregate=one_sided_bucket,
                best_regime=None,
                worst_regime=None,
                regime_evidence_count=3,
                regime_min_evidence_required=50,
            )
        ]

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _fake_scorecards)

    async with _real_session() as session:
        await _seed_roster_run_and_proposals(session, actions={"ma_crossover": "HOLD", "momentum": "HOLD", "breakout": "HOLD"})
        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "HOLD"
        # The scorecard was found (tie-break dominant is "breakout"), but it
        # has no HOLD-scoped history -- profitability must be None, not
        # silently backed by the BUY-scoped or blended figures.
        assert evidence["source_identity"]["scorecard_strategy_slug"] == "breakout"
        assert evidence["profitable_after_fees_performance"] is None


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


@pytest.mark.asyncio
async def test_preferred_contributor_does_not_collapse_ensemble(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )
        evidence, reason = await _call_aggregator(
            session, preferred_strategy_identity="ma_crossover@1.0.0"
        )
        assert reason is None
        assert evidence["strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY
        assert evidence["aggregate_evidence"]["eligible_strategy_count"] == 3


@pytest.mark.asyncio
async def test_wrong_trigger_has_no_exact_roster_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY"}, trigger="manual"
        )
        evidence, reason = await _call_aggregator(session)
        assert evidence is None
        assert reason == "exact_roster_run_unavailable"


@pytest.mark.asyncio
async def test_incomplete_roster_run_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY"}, complete=False
        )
        evidence, reason = await _call_aggregator(session)
        assert evidence is None
        assert reason == "roster_run_incomplete_or_failed"


# Regression for the second production incident: strategy_roster.service
# always persists error_summary as {"failed": failed} -- a non-empty dict
# even when failed == [] -- and the aggregation eligibility check used to
# test truthiness of that dict directly (`or run.error_summary`), not its
# "failed" payload. A dict with one key is truthy regardless of its values,
# so every roster run, including fully successful ones, was rejected as
# roster_run_incomplete_or_failed. Reproduces the exact production shape:
# requested=7, completed=7, failed=0, one SELL and six HOLD proposals.
@pytest.mark.asyncio
async def test_fully_successful_seven_strategy_roster_is_accepted_despite_nonempty_error_summary_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        actions = {
            "ma_crossover": "HOLD",
            "momentum": "HOLD",
            "breakout": "HOLD",
            "mean_reversion": "HOLD",
            "rsi_mean_reversion": "HOLD",
            "bollinger_reversion": "HOLD",
            "donchian_breakout": "SELL",
        }
        roster_run_id = await _seed_roster_run_and_proposals(session, actions=actions)

        run = await session.get(StrategyRosterRun, roster_run_id)
        assert run.strategies_requested_count == 7
        assert run.strategies_completed_count == 7
        assert run.strategies_failed_count == 0
        # The exact shape that used to falsely trip eligibility: a non-empty
        # dict wrapping an empty failure list.
        assert run.error_summary == {"failed": []}

        evidence, reason = await _call_aggregator(session)
        assert reason is None
        assert evidence is not None
        assert evidence["aggregate_evidence"]["eligible_strategy_count"] == 7

        # Replay: the second call for the identical exact scope must hit the
        # pure-read idempotent-replay path and return the same decision,
        # never re-derive or re-reject it.
        replay_evidence, replay_reason = await _call_aggregator(session)
        assert replay_reason is None
        assert replay_evidence["action"] == evidence["action"]
        assert replay_evidence["source_identity"]["aggregate_decision_id"] == evidence["source_identity"]["aggregate_decision_id"]
        assert replay_evidence["source_identity"]["decision_record_id"] == evidence["source_identity"]["decision_record_id"]
        assert replay_evidence["aggregate_evidence"]["eligible_strategy_count"] == 7

        rows = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(rows) == 1


async def _persist_then_corrupt(monkeypatch: pytest.MonkeyPatch, statement) -> tuple[dict[str, Any] | None, str | None]:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )
        evidence, _ = await _call_aggregator(session)
        await session.execute(statement(evidence))
        await session.flush()
        return await authoritative.load_strategy_aggregate_evidence(
            db=session,
            roster_run_id=uuid.UUID(evidence["source_identity"]["strategy_roster_run_id"]),
            asset_id=ASSET_ID,
            candle_close_time=datetime.fromisoformat(evidence["evidence_timestamp"]),
            campaign_id=CAMPAIGN_ID,
            campaign_version=1,
            config_version="v1",
            environment="production",
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
        )


@pytest.mark.asyncio
async def test_conflicting_aggregate_identity_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, reason = await _persist_then_corrupt(
        monkeypatch,
        lambda evidence: update(StrategyAggregateDecision)
        .where(StrategyAggregateDecision.aggregate_decision_id == uuid.UUID(evidence["source_identity"]["aggregate_decision_id"]))
        .values(primary_strategy_identity="breakout@1.0.0"),
    )
    assert evidence is None
    assert reason == "aggregate_identity_conflict"


@pytest.mark.asyncio
async def test_generated_signal_identity_conflict_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, reason = await _persist_then_corrupt(
        monkeypatch,
        lambda evidence: update(DecisionRecord)
        .where(DecisionRecord.decision_id == uuid.UUID(evidence["source_identity"]["decision_record_id"]))
        .values(generated_signals=[{"strategy_identity": "breakout@1.0.0", "strategy_version": "1.0.0", "action": "BUY"}]),
    )
    assert evidence is None
    assert reason == "generated_signal_identity_conflict"


@pytest.mark.asyncio
async def test_contributor_lineage_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, reason = await _persist_then_corrupt(
        monkeypatch,
        lambda evidence: update(DecisionRecord)
        .where(DecisionRecord.decision_id == uuid.UUID(evidence["source_identity"]["decision_record_id"]))
        .values(supporting_strategies=[]),
    )
    assert evidence is None
    assert reason == "aggregate_contributor_lineage_mismatch"


@pytest.mark.asyncio
async def test_decision_snapshot_must_reconstruct_exact_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, reason = await _persist_then_corrupt(
        monkeypatch,
        lambda evidence: update(DecisionSnapshot)
        .where(DecisionSnapshot.decision_id == uuid.UUID(evidence["source_identity"]["decision_record_id"]))
        .values(strategy_inputs={"roster_run_id": "wrong", "contributions": []}),
    )
    assert evidence is None
    assert reason == "decision_snapshot_aggregate_mismatch"


# Regression for the first production incident after the aggregator went live:
# continuous_pipeline_worker composed the campaign cycle before creating this
# candle's StrategyRosterRun, so the exact-match lookup always missed and every
# cycle logged strategy_aggregate_skipped reason=exact_roster_run_unavailable.
# This proves the aggregator resolves correctly once the roster run exists
# first (the corrected pipeline ordering), and reproduces the pre-fix failure
# signature when it does not -- and that the resulting evidence is consistent
# with a campaign whose compounding has been disabled via the aggregator
# activation migration (percentages still sum to 100).
@pytest.mark.asyncio
async def test_exact_roster_run_must_exist_before_composition_for_aggregate_to_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        # Composing before the roster run for this candle exists reproduces
        # the exact production failure signature.
        evidence, reason = await _call_aggregator(session)
        assert evidence is None
        assert reason == "exact_roster_run_unavailable"

        # Once the strategy roster has run for this exact candle (the
        # corrected pipeline order: roster before composition), the aggregate
        # resolves deterministically against that run -- never an inferred
        # "latest" run.
        roster_run_id = await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )
        evidence, reason = await _call_aggregator(session)
        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "BUY"
        assert evidence["strategy_identity"] == AGGREGATE_STRATEGY_IDENTITY
        assert uuid.UUID(evidence["source_identity"]["decision_record_id"]) is not None
        assert evidence["aggregate_evidence"]["eligible_strategy_count"] == 3

        assert roster_run_id is not None

        # The evidence this cycle produced is only useful if the campaign it
        # feeds is actually composable -- a campaign migrated to disabled
        # compounding (reinvestment=0, reserve=100, matching the aggregator
        # activation migration) must still pass the preview engine's
        # percentages-sum-to-100 invariant.
        _validate_percentages(
            compounding_policy=CampaignCompoundingPolicy(
                policy_type="REINVEST_PERCENTAGE",
                reinvestment_percentage=Decimal("0"),
                profit_distribution_percentage=Decimal("0"),
                reserve_percentage=Decimal("100"),
            ),
            distribution_policy=CampaignProfitDistributionPolicy(reinvestment_percentage=Decimal("100")),
        )


# Regression for the production PendingRollbackError incident:
# autonomous_cycle_triggered (replayed) -> strategy roster replayed ->
# strategy_aggregate_skipped reason=idempotent_replay -> campaign
# orchestration failed at service.py:436 with PendingRollbackError. The true
# fault was not line 436 -- it was the scorecard-enrichment fetch inside the
# pure-read idempotent-replay path (load_strategy_aggregate_evidence),
# which caught any exception and silently continued without rolling back,
# leaving the session's ambient transaction invalid for the very next
# statement on it. These tests reproduce that failure shape and prove the
# begin_nested() savepoint isolates it.
@pytest.mark.asyncio
async def test_scorecard_fetch_failure_during_idempotent_replay_recovers_and_stays_idempotent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_flat_position(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )

        # First call: normal fresh computation (cache miss), scorecards healthy.
        _patch_no_scorecards(monkeypatch)
        first_evidence, first_reason = await _call_aggregator(session)
        assert first_reason is None
        assert first_evidence is not None

        # Second call: identical scope -> pure-read idempotent-replay path
        # (production's "strategy_aggregate_skipped reason=idempotent_replay"),
        # but scorecard enrichment for the replay fails.
        async def _raise_scorecards(**_kwargs):
            raise RuntimeError("simulated scorecard fetch failure")

        monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _raise_scorecards)
        caplog.set_level(logging.WARNING)

        second_evidence, second_reason = await _call_aggregator(session)

        # Replay still succeeds and is still the same decision -- idempotency
        # is preserved even though scorecard enrichment failed mid-replay.
        assert second_reason is None
        assert second_evidence is not None
        assert second_evidence["action"] == first_evidence["action"]
        assert (
            second_evidence["source_identity"]["aggregate_decision_id"]
            == first_evidence["source_identity"]["aggregate_decision_id"]
        )

        # The original exception is observable, not silently swallowed.
        matching = [r for r in caplog.records if r.getMessage().startswith("strategy_scorecard_fetch_failed_during_replay")]
        assert len(matching) == 1
        assert matching[0].exc_info is not None
        assert str(matching[0].exc_info[1]) == "simulated scorecard fetch failure"

        # No duplicate aggregate/decision rows were created by the replay attempt.
        aggregate_rows = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(aggregate_rows) == 1
        decision_rows = (await session.execute(DecisionRecord.__table__.select())).fetchall()
        assert len(decision_rows) == 1

        # The session is still usable for an unrelated query afterward -- this
        # is the exact failure shape production hit one statement later, at
        # capital_campaign_orchestration/service.py:436's AutonomousCycleRun
        # idempotency lookup (a query against a different table entirely). A
        # PendingRollbackError here is exactly what the fix must prevent.
        parameter_sets_after_failure = (await session.execute(ParameterSet.__table__.select())).fetchall()
        assert isinstance(parameter_sets_after_failure, list)

        # A subsequent replay ("next polling cycle") on the same session, with
        # scorecards healthy again, is unaffected -- this cycle's failure did
        # not poison the one after it.
        _patch_no_scorecards(monkeypatch)
        third_evidence, third_reason = await _call_aggregator(session)
        assert third_reason is None
        assert third_evidence["action"] == first_evidence["action"]
        assert (
            third_evidence["source_identity"]["aggregate_decision_id"]
            == first_evidence["source_identity"]["aggregate_decision_id"]
        )
        aggregate_rows_final = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(aggregate_rows_final) == 1


@pytest.mark.asyncio
async def test_scorecard_fetch_failure_during_fresh_computation_triggers_governed_veto_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Same defect, the other call site: a scorecard-fetch failure on a fresh
    (non-replay) computation must still isolate cleanly via its own savepoint,
    be logged, and fall through to the existing governed data-quality veto
    (fail-closed HOLD) rather than leaving the transaction unusable."""
    _patch_flat_position(monkeypatch)
    caplog.set_level(logging.WARNING)

    async def _raise_scorecards(**_kwargs):
        raise RuntimeError("simulated scorecard fetch failure")

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _raise_scorecards)

    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )

        evidence, reason = await _call_aggregator(session)

        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "HOLD"

        matching = [r for r in caplog.records if r.getMessage().startswith("strategy_scorecard_fetch_failed roster_run_id=")]
        assert len(matching) == 1
        assert matching[0].exc_info is not None

        # Exactly one aggregate/decision record was persisted for this veto --
        # not zero (silently dropped) and not more than one (duplicated).
        aggregate_rows = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(aggregate_rows) == 1

        # The session recovered enough to serve an unrelated query afterward.
        parameter_sets_after_failure = (await session.execute(ParameterSet.__table__.select())).fetchall()
        assert isinstance(parameter_sets_after_failure, list)


@pytest.mark.asyncio
async def test_scorecard_fetch_cancellation_propagates_and_savepoint_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_flat_position(monkeypatch)

    async def _cancel_scorecards(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _cancel_scorecards)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "SELL", "breakout": "HOLD"},
        )

        with pytest.raises(asyncio.CancelledError):
            await _call_aggregator(session)

        assert isinstance((await session.execute(ParameterSet.__table__.select())).fetchall(), list)


@pytest.mark.asyncio
async def test_unrecoverable_scorecard_session_aborts_instead_of_poisoning_later_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_flat_position(monkeypatch)

    async def _timeout(**_kwargs):
        raise TimeoutError("simulated command timeout")

    async def _unrecoverable(**_kwargs):
        return authoritative._ScorecardSessionRecovery(
            probe_succeeded=False,
            connection_invalidated=True,
            rollback_attempted=True,
            rollback_succeeded=False,
            session_usable=False,
        )

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _timeout)
    monkeypatch.setattr(authoritative, "_recover_session_after_scorecard_failure", _unrecoverable)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "SELL", "breakout": "HOLD"},
        )

        with pytest.raises(authoritative.ScorecardSessionRecoveryError):
            await _call_aggregator(session)


# Production evidence: a fetch_strategy_scorecards() command timeout left the
# session unable to serve the NEXT unrelated statement (PendingRollbackError,
# surfacing as campaign_orchestration_failed) despite the begin_nested()
# savepoint that is supposed to isolate scorecard-fetch failures. Root cause:
# a driver-level timeout can leave the underlying connection's wire protocol
# desynced, which the savepoint's own ROLLBACK TO SAVEPOINT -- sent over that
# same broken connection -- cannot always recover from. These two tests
# exercise _recover_session_after_scorecard_failure directly (not through the
# full aggregator) to prove it: (a) does nothing when the savepoint already
# fully recovered the session (the common case, and the exact case the two
# tests above already cover end-to-end), and (b) escalates to invalidating
# the connection and rolling back the session ONLY when a cheap probe proves
# the session is still broken after the savepoint's own attempt.
@pytest.mark.asyncio
async def test_recover_session_after_scorecard_failure_is_a_noop_when_probe_succeeds() -> None:
    from app.services.capital_campaign_orchestration.authoritative import _recover_session_after_scorecard_failure

    calls = {"execute": 0, "rollback": 0, "connection": 0}

    class _HealthySession:
        async def execute(self, _statement):
            calls["execute"] += 1

        async def connection(self):
            calls["connection"] += 1
            raise AssertionError("connection() must not be reached when the probe already succeeds")

        async def rollback(self):
            calls["rollback"] += 1

    recovery = await _recover_session_after_scorecard_failure(db=_HealthySession())

    assert calls["execute"] == 1
    assert calls["connection"] == 0
    assert calls["rollback"] == 0
    assert recovery.session_usable is True
    assert recovery.rollback_attempted is False


@pytest.mark.asyncio
async def test_recover_session_after_scorecard_failure_escalates_when_probe_still_fails() -> None:
    from app.services.capital_campaign_orchestration.authoritative import _recover_session_after_scorecard_failure

    calls = {"execute": 0, "invalidate": 0, "rollback": 0}

    class _BrokenConnection:
        async def invalidate(self):
            calls["invalidate"] += 1

    class _BrokenSession:
        async def execute(self, _statement):
            calls["execute"] += 1
            raise RuntimeError("This Session's transaction has been rolled back due to a previous exception")

        async def connection(self):
            return _BrokenConnection()

        async def rollback(self):
            calls["rollback"] += 1

    recovery = await _recover_session_after_scorecard_failure(db=_BrokenSession())

    assert calls["execute"] == 2
    assert calls["invalidate"] == 1
    assert calls["rollback"] == 1
    assert recovery.connection_invalidated is True
    assert recovery.rollback_succeeded is True
    assert recovery.session_usable is False


# Production evidence, second incident: after the probe-gated recovery above
# shipped, a later production cycle hit fetch_strategy_scorecards() timing
# out AGAIN, this time with the probe ALSO failing (the savepoint's own
# rollback did not fully recover the connection) -- so
# _recover_session_after_scorecard_failure's escalation path ran a real
# session.rollback(). That expires every ORM instance still tracked by the
# session, including every `proposal` in resolve_or_create_strategy_aggregate_evidence's
# `proposals` list, loaded earlier in the same session. The very next line,
# `scorecard_by_slug.get(proposal.strategy_slug)`, touched an expired
# attribute on a plain (non-awaited) for-loop iteration -- outside
# SQLAlchemy's asyncio greenlet bridge, this raises MissingGreenlet instead
# of transparently refetching. This test reproduces the full chain against a
# REAL SQLAlchemy Session (not a mock), so the expiration -- and the fix's
# avoidance of it -- is genuine, not asserted by assumption.
@pytest.mark.asyncio
async def test_scorecard_timeout_with_unrecovered_probe_survives_real_orm_expiration(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_flat_position(monkeypatch)
    caplog.set_level(logging.WARNING)

    probe_state = {"armed": False, "raised": False}

    async def _raise_scorecards(**_kwargs):
        # Fires exactly where production's TimeoutError fired. Arms the
        # probe-failure so the very next db.execute() (the health probe
        # inside _recover_session_after_scorecard_failure) also fails,
        # forcing the escalation path -- a real session.rollback() -- to run.
        probe_state["armed"] = True
        raise TimeoutError("simulated fetch_strategy_scorecards command timeout")

    monkeypatch.setattr(authoritative, "fetch_strategy_scorecards", _raise_scorecards)

    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )

        original_execute = session.execute

        async def _flaky_execute(statement):
            if probe_state["armed"] and not probe_state["raised"]:
                probe_state["raised"] = True
                raise RuntimeError("simulated: connection still broken after savepoint's own rollback")
            return await original_execute(statement)

        session.execute = _flaky_execute

        # Load the exact same ORM instances the aggregator will use
        # internally (same session identity map, same primary keys) so we
        # can prove, independently, that they really were expired.
        proposal_rows = list((await original_execute(StrategyRosterProposal.__table__.select())).fetchall())
        assert len(proposal_rows) == 3
        held_proposals = [
            await session.get(StrategyRosterProposal, row.proposal_id) for row in proposal_rows
        ]
        assert all(sa_inspect(p).expired is False for p in held_proposals)

        evidence, reason = await _call_aggregator(session)

        # The probe really did fail once, proving the escalation path ran.
        assert probe_state["raised"] is True

        # The escalation path's session.rollback() really did expire the
        # proposals this function loaded earlier in the same session --
        # confirming the mechanism, not assuming it.
        assert all(sa_inspect(p).expired is True for p in held_proposals)

        # And yet the function completed correctly: fail-closed veto, not a
        # crash. This only holds because the fix snapshots every proposal
        # field into plain values before the scorecard fetch -- without it,
        # `scorecard_by_slug.get(proposal.strategy_slug)` below would touch
        # one of the now-expired instances above and raise MissingGreenlet
        # (in real production) or ObjectDeletedError (here, since the seeded
        # rows were only flushed, never committed, so the rollback actually
        # removed them).
        assert reason is None
        assert evidence is not None
        assert evidence["action"] == "HOLD"

        matching = [r for r in caplog.records if r.getMessage().startswith("strategy_scorecard_fetch_failed roster_run_id=")]
        assert len(matching) == 1
        message = matching[0].getMessage()
        assert "stage=fetch_strategy_scorecards" in message
        assert "provider=kraken_spot product=BTC-USD interval=15m" in message
        assert "timeout=True" in message
        assert "rollback_attempted=True" in message
        assert "rollback_succeeded=True" in message
        assert "session_usable=True" in message


@pytest.mark.asyncio
async def test_clean_idempotent_replay_without_error_remains_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control case: no fault injected. Repeated replay of the same scope is
    byte-identical and creates no duplicate rows -- proves the savepoint
    added around the healthy path changes nothing about ordinary behavior."""
    _patch_flat_position(monkeypatch)
    _patch_no_scorecards(monkeypatch)
    async with _real_session() as session:
        await _seed_roster_run_and_proposals(
            session, actions={"ma_crossover": "BUY", "momentum": "BUY", "breakout": "BUY"}
        )

        first_evidence, first_reason = await _call_aggregator(session)
        second_evidence, second_reason = await _call_aggregator(session)
        third_evidence, third_reason = await _call_aggregator(session)

        assert first_reason is second_reason is third_reason is None
        for evidence in (first_evidence, second_evidence, third_evidence):
            assert evidence["action"] == first_evidence["action"]
            assert evidence["source_identity"]["aggregate_decision_id"] == first_evidence["source_identity"]["aggregate_decision_id"]
            assert evidence["source_identity"]["decision_record_id"] == first_evidence["source_identity"]["decision_record_id"]
            assert evidence["aggregate_evidence"]["eligible_strategy_count"] == first_evidence["aggregate_evidence"]["eligible_strategy_count"]

        aggregate_rows = (await session.execute(StrategyAggregateDecision.__table__.select())).fetchall()
        assert len(aggregate_rows) == 1
