from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from app.services.strategy_outcomes.service import (
    HORIZONS,
    StrategyRosterProposalOutcome,
    fetch_strategy_scorecards,
    score_due_strategy_roster_proposal_outcomes,
)


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, *, proposals: list[SimpleNamespace], existing_keys: set[tuple[uuid.UUID, int]] | None = None):
        self.proposals = proposals
        self.existing_keys = existing_keys or set()
        self.inserted: list[StrategyRosterProposalOutcome] = []
        self.commits = 0

    def add(self, item):
        if isinstance(item, StrategyRosterProposalOutcome):
            self.inserted.append(item)

    async def commit(self):
        self.commits += 1

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM strategy_roster_proposal_outcomes" in sql and "horizon_minutes" in sql:
            proposal_id = next((value for value in params.values() if isinstance(value, uuid.UUID)), None)
            horizon = next((value for value in params.values() if isinstance(value, int)), None)
            if proposal_id is not None and horizon is not None and (proposal_id, horizon) in self.existing_keys:
                return uuid.uuid4()
            return None
        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM strategy_roster_proposals" in sql:
            return _Result(self.proposals)
        if "FROM strategy_roster_proposal_outcomes" in sql:
            return _Result(self.inserted)
        return _Result([])


def _proposal(*, action: str = "BUY", evaluation_status: str = "EVALUATED") -> SimpleNamespace:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        proposal_id=uuid.uuid4(),
        roster_run_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
        strategy_slug="momentum",
        strategy_identity="momentum@1.0.0",
        action=action,
        evaluation_status=evaluation_status,
        candle_close_time=now - timedelta(hours=30),
        reason="ok",
    )


@pytest.mark.asyncio
async def test_outcome_scoring_creates_four_horizons(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = _proposal(action="BUY", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal])

    import app.services.strategy_outcomes.service as module

    async def _close(*, target, **kwargs):
        base = Decimal("100")
        if target > proposal.candle_close_time:
            return Decimal("101")
        return base

    async def _window(**kwargs):
        return [SimpleNamespace(high=Decimal("102"), low=Decimal("99"), close=Decimal("101"))]

    monkeypatch.setattr(module, "_load_close_at_or_before", _close)
    monkeypatch.setattr(module, "_load_window_candles", _window)

    result = await score_due_strategy_roster_proposal_outcomes(
        db=db,
        as_of=proposal.candle_close_time + timedelta(days=2),
    )

    assert result.scanned_proposals == 1
    assert result.inserted_outcomes == len(HORIZONS)
    assert db.commits == 1
    assert len(db.inserted) == len(HORIZONS)
    assert {item.horizon_minutes for item in db.inserted} == {15, 60, 240, 1440}
    assert all(item.execution_mode == "SHADOW" for item in db.inserted)
    assert all(item.live_submission_allowed is False for item in db.inserted)


@pytest.mark.asyncio
async def test_outcome_scoring_is_idempotent_when_outcome_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = _proposal(action="BUY", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal], existing_keys={(proposal.proposal_id, 15)})

    import app.services.strategy_outcomes.service as module

    async def _close(**kwargs):
        return Decimal("100")

    async def _window(**kwargs):
        return [SimpleNamespace(high=Decimal("100"), low=Decimal("100"), close=Decimal("100"))]

    monkeypatch.setattr(module, "_load_close_at_or_before", _close)
    monkeypatch.setattr(module, "_load_window_candles", _window)

    result = await score_due_strategy_roster_proposal_outcomes(
        db=db,
        as_of=proposal.candle_close_time + timedelta(days=2),
    )

    assert result.skipped_existing == 1
    assert result.inserted_outcomes == len(HORIZONS) - 1


@pytest.mark.asyncio
async def test_fetch_strategy_scorecards_aggregates_accuracy_and_returns() -> None:
    proposal = _proposal(action="BUY", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal])

    db.inserted = [
        StrategyRosterProposalOutcome(
            proposal_id=proposal.proposal_id,
            roster_run_id=proposal.roster_run_id,
            asset_id=proposal.asset_id,
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slug="momentum",
            strategy_identity="momentum@1.0.0",
            action="BUY",
            proposal_evaluation_status="EVALUATED",
            horizon_label="15m",
            horizon_minutes=15,
            proposal_candle_close_time=proposal.candle_close_time,
            horizon_time=proposal.candle_close_time + timedelta(minutes=15),
            evaluated_at=datetime.now(timezone.utc),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            market_return_pct=Decimal("1.0"),
            buy_raw_return_pct=Decimal("1.0"),
            buy_fee_adjusted_return_pct=Decimal("0.8"),
            sell_raw_return_pct=Decimal("-1.0"),
            sell_fee_adjusted_return_pct=Decimal("-1.2"),
            actual_raw_return_pct=Decimal("1.0"),
            actual_fee_adjusted_return_pct=Decimal("0.8"),
            mfe_pct=Decimal("1.5"),
            mae_pct=Decimal("-0.5"),
            actual_action_correct=True,
            evaluation_completed=True,
            evaluation_state="RESOLVED",
            evaluation_reason=None,
            market_move="UP",
            regime_trend="TRENDING",
            regime_volatility="HIGH_VOLATILITY",
            regime_range="EXPANSION",
            fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"),
            hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW",
            live_submission_allowed=False,
        ),
        StrategyRosterProposalOutcome(
            proposal_id=uuid.uuid4(),
            roster_run_id=proposal.roster_run_id,
            asset_id=proposal.asset_id,
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slug="momentum",
            strategy_identity="momentum@1.0.0",
            action="SELL",
            proposal_evaluation_status="EVALUATED",
            horizon_label="1h",
            horizon_minutes=60,
            proposal_candle_close_time=proposal.candle_close_time,
            horizon_time=proposal.candle_close_time + timedelta(minutes=60),
            evaluated_at=datetime.now(timezone.utc),
            entry_price=Decimal("100"),
            exit_price=Decimal("99"),
            market_return_pct=Decimal("-1.0"),
            buy_raw_return_pct=Decimal("-1.0"),
            buy_fee_adjusted_return_pct=Decimal("-1.2"),
            sell_raw_return_pct=Decimal("1.0"),
            sell_fee_adjusted_return_pct=Decimal("0.8"),
            actual_raw_return_pct=Decimal("1.0"),
            actual_fee_adjusted_return_pct=Decimal("0.8"),
            mfe_pct=Decimal("1.2"),
            mae_pct=Decimal("-0.4"),
            actual_action_correct=True,
            evaluation_completed=True,
            evaluation_state="RESOLVED",
            evaluation_reason=None,
            market_move="DOWN",
            regime_trend="TRENDING",
            regime_volatility="LOW_VOLATILITY",
            regime_range="COMPRESSION",
            fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"),
            hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW",
            live_submission_allowed=False,
        ),
    ]

    scorecards = await fetch_strategy_scorecards(
        db=db,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
    )

    assert len(scorecards) == 1
    card = scorecards[0]
    assert card.strategy_slug == "momentum"
    assert len(card.per_horizon) == len(HORIZONS)

    by_horizon = {bucket.horizon_label: bucket for bucket in card.per_horizon}
    assert by_horizon["15m"].total_evaluated == 1
    assert by_horizon["15m"].buy_evaluations == 1
    assert by_horizon["15m"].buy_correct == 1
    assert by_horizon["1h"].total_evaluated == 1
    assert by_horizon["1h"].sell_evaluations == 1
    assert by_horizon["1h"].sell_correct == 1
    assert by_horizon["4h"].total_evaluated == 0
    assert by_horizon["24h"].total_evaluated == 0

    aggregate = card.aggregate
    assert aggregate.total_evaluated == 2
    assert aggregate.buy_evaluations == 1
    assert aggregate.sell_evaluations == 1
    assert aggregate.hold_evaluations == 0
    assert aggregate.buy_correct == 1
    assert aggregate.sell_correct == 1
    assert aggregate.hold_correct == 0
    assert aggregate.buy_evaluations + aggregate.sell_evaluations + aggregate.hold_evaluations == aggregate.total_evaluated
    assert aggregate.overall_correct_pct == Decimal("100.0000")
    assert aggregate.average_fee_adjusted_return_pct == Decimal("0.8000")

    assert card.best_regime is None
    assert card.worst_regime is None
    assert card.regime_evidence_count == 2
    assert card.regime_min_evidence_required == 50


def _outcome_row(
    *,
    proposal: SimpleNamespace,
    action: str,
    horizon_label: str,
    horizon_minutes: int,
    actual_fee_adjusted_return_pct: Decimal,
    actual_action_correct: bool,
    actual_raw_return_pct: Decimal | None = None,
) -> "StrategyRosterProposalOutcome":
    return StrategyRosterProposalOutcome(
        proposal_id=uuid.uuid4(),
        roster_run_id=proposal.roster_run_id,
        asset_id=proposal.asset_id,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
        strategy_slug="momentum",
        strategy_identity="momentum@1.0.0",
        action=action,
        proposal_evaluation_status="EVALUATED",
        horizon_label=horizon_label,
        horizon_minutes=horizon_minutes,
        proposal_candle_close_time=proposal.candle_close_time,
        horizon_time=proposal.candle_close_time + timedelta(minutes=horizon_minutes),
        evaluated_at=datetime.now(timezone.utc),
        entry_price=Decimal("100"),
        exit_price=Decimal("100"),
        market_return_pct=Decimal("0"),
        buy_raw_return_pct=Decimal("0"),
        buy_fee_adjusted_return_pct=Decimal("0"),
        sell_raw_return_pct=Decimal("0"),
        sell_fee_adjusted_return_pct=Decimal("0"),
        actual_raw_return_pct=actual_fee_adjusted_return_pct if actual_raw_return_pct is None else actual_raw_return_pct,
        actual_fee_adjusted_return_pct=actual_fee_adjusted_return_pct,
        mfe_pct=Decimal("0"),
        mae_pct=Decimal("0"),
        actual_action_correct=actual_action_correct,
        evaluation_completed=True,
        evaluation_state="RESOLVED",
        evaluation_reason=None,
        market_move="SIDEWAYS",
        regime_trend="RANGING",
        regime_volatility="LOW_VOLATILITY",
        regime_range="COMPRESSION",
        fee_bps=Decimal("10"),
        hold_buy_threshold_pct=Decimal("0"),
        hold_sell_threshold_pct=Decimal("0"),
        execution_mode="SHADOW",
        live_submission_allowed=False,
    )


@pytest.mark.asyncio
async def test_fetch_strategy_scorecards_action_scoped_averages_do_not_cross_contaminate() -> None:
    """Reproduces the production defect: a strategy with a genuinely
    profitable BUY track record and a losing SELL track record must not have
    its BUY-specific edge estimate dragged down by the SELL losses (or vice
    versa) when the blended aggregate is negative overall. This is what fed
    profitable_after_fees_performance for a BUY decision from an unrelated
    SELL loss history in production."""
    proposal = _proposal(action="BUY", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal])

    db.inserted = [
        _outcome_row(
            proposal=proposal, action="BUY", horizon_label="15m", horizon_minutes=15,
            actual_fee_adjusted_return_pct=Decimal("2.0"), actual_action_correct=True,
        ),
        _outcome_row(
            proposal=proposal, action="BUY", horizon_label="15m", horizon_minutes=15,
            actual_fee_adjusted_return_pct=Decimal("1.0"), actual_action_correct=True,
        ),
        _outcome_row(
            proposal=proposal, action="SELL", horizon_label="15m", horizon_minutes=15,
            actual_fee_adjusted_return_pct=Decimal("-10.0"), actual_action_correct=False,
        ),
    ]

    scorecards = await fetch_strategy_scorecards(db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m")
    aggregate = scorecards[0].aggregate

    # Blended across all 3 actions: (2.0 + 1.0 - 10.0) / 3 = -2.3333 -- negative,
    # even though the strategy's actual BUY calls were both profitable.
    assert aggregate.average_fee_adjusted_return_pct == Decimal("-2.3333")
    # Action-scoped figures must not cross-contaminate.
    assert aggregate.buy_average_fee_adjusted_return_pct == Decimal("1.5000")
    assert aggregate.sell_average_fee_adjusted_return_pct == Decimal("-10.0000")
    assert aggregate.hold_average_fee_adjusted_return_pct is None


@pytest.mark.asyncio
async def test_fetch_strategy_scorecards_raw_average_does_not_include_outcome_scoring_fee() -> None:
    """Reproduces the second production defect: average_fee_adjusted_return_pct
    (and the action-scoped variants) already subtract outcome-scoring's own
    round-trip fee assumption from the raw historical return. A caller that
    needs a genuinely pre-fee ("gross") figure -- to which it will apply its
    OWN cost model exactly once -- must read *_average_raw_return_pct, which
    must never equal the fee-adjusted figure when raw and fee-adjusted
    outcomes actually differ."""
    proposal = _proposal(action="BUY", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal])

    db.inserted = [
        _outcome_row(
            proposal=proposal, action="BUY", horizon_label="15m", horizon_minutes=15,
            actual_raw_return_pct=Decimal("0.2"), actual_fee_adjusted_return_pct=Decimal("0.0"),
            actual_action_correct=False,
        ),
        _outcome_row(
            proposal=proposal, action="BUY", horizon_label="15m", horizon_minutes=15,
            actual_raw_return_pct=Decimal("0.4"), actual_fee_adjusted_return_pct=Decimal("0.2"),
            actual_action_correct=True,
        ),
    ]

    scorecards = await fetch_strategy_scorecards(db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m")
    aggregate = scorecards[0].aggregate

    assert aggregate.buy_average_raw_return_pct == Decimal("0.3000")
    assert aggregate.buy_average_fee_adjusted_return_pct == Decimal("0.1000")
    assert aggregate.buy_average_raw_return_pct != aggregate.buy_average_fee_adjusted_return_pct
    assert aggregate.sell_average_raw_return_pct is None
    assert aggregate.hold_average_raw_return_pct is None


@pytest.mark.asyncio
async def test_fetch_strategy_scorecards_excludes_non_resolved_and_reconciles_hold() -> None:
    proposal = _proposal(action="HOLD", evaluation_status="EVALUATED")
    db = _FakeDb(proposals=[proposal])

    db.inserted = [
        StrategyRosterProposalOutcome(
            proposal_id=proposal.proposal_id,
            roster_run_id=proposal.roster_run_id,
            asset_id=proposal.asset_id,
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slug="momentum",
            strategy_identity="momentum@1.0.0",
            action="HOLD",
            proposal_evaluation_status="EVALUATED",
            horizon_label="4h",
            horizon_minutes=240,
            proposal_candle_close_time=proposal.candle_close_time,
            horizon_time=proposal.candle_close_time + timedelta(minutes=240),
            evaluated_at=datetime.now(timezone.utc),
            entry_price=Decimal("100"),
            exit_price=Decimal("100"),
            market_return_pct=Decimal("0"),
            buy_raw_return_pct=Decimal("0"),
            buy_fee_adjusted_return_pct=Decimal("-0.2"),
            sell_raw_return_pct=Decimal("0"),
            sell_fee_adjusted_return_pct=Decimal("-0.2"),
            actual_raw_return_pct=Decimal("0"),
            actual_fee_adjusted_return_pct=Decimal("0"),
            mfe_pct=Decimal("0.1"),
            mae_pct=Decimal("-0.1"),
            actual_action_correct=True,
            evaluation_completed=True,
            evaluation_state="RESOLVED",
            evaluation_reason=None,
            market_move="SIDEWAYS",
            regime_trend="RANGING",
            regime_volatility="LOW_VOLATILITY",
            regime_range="COMPRESSION",
            fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"),
            hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW",
            live_submission_allowed=False,
        ),
        StrategyRosterProposalOutcome(
            proposal_id=uuid.uuid4(),
            roster_run_id=proposal.roster_run_id,
            asset_id=proposal.asset_id,
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slug="momentum",
            strategy_identity="momentum@1.0.0",
            action="BUY",
            proposal_evaluation_status="FAILED",
            horizon_label="24h",
            horizon_minutes=1440,
            proposal_candle_close_time=proposal.candle_close_time,
            horizon_time=proposal.candle_close_time + timedelta(minutes=1440),
            evaluated_at=datetime.now(timezone.utc),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            market_return_pct=Decimal("1"),
            buy_raw_return_pct=Decimal("1"),
            buy_fee_adjusted_return_pct=Decimal("0.8"),
            sell_raw_return_pct=Decimal("-1"),
            sell_fee_adjusted_return_pct=Decimal("-1.2"),
            actual_raw_return_pct=Decimal("1"),
            actual_fee_adjusted_return_pct=Decimal("0.8"),
            mfe_pct=Decimal("1.1"),
            mae_pct=Decimal("-0.2"),
            actual_action_correct=True,
            evaluation_completed=True,
            evaluation_state="PROPOSAL_NOT_EVALUATED",
            evaluation_reason="failed upstream",
            market_move="UP",
            regime_trend="TRENDING",
            regime_volatility="LOW_VOLATILITY",
            regime_range="EXPANSION",
            fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"),
            hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW",
            live_submission_allowed=False,
        ),
    ]

    scorecards = await fetch_strategy_scorecards(
        db=db,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
    )

    assert len(scorecards) == 1
    aggregate = scorecards[0].aggregate
    assert aggregate.total_evaluated == 1
    assert aggregate.buy_evaluations == 0
    assert aggregate.sell_evaluations == 0
    assert aggregate.hold_evaluations == 1
    assert aggregate.hold_correct == 1
    assert aggregate.buy_evaluations + aggregate.sell_evaluations + aggregate.hold_evaluations == aggregate.total_evaluated


def _install_sqlite_uuid_compiler() -> None:
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "CHAR(32)"


_install_sqlite_uuid_compiler()


def _fix_sqlite_server_defaults(table) -> None:  # noqa: ANN001
    from sqlalchemy import text as _text
    from sqlalchemy.schema import DefaultClause
    from sqlalchemy.sql.elements import TextClause

    for column in table.columns:
        default = column.server_default
        if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
            raw = default.arg.text.strip().split("::", 1)[0]
            if raw.endswith("()") and not raw.startswith("("):
                raw = f"({raw})"
            column.server_default = DefaultClause(_text(raw))


@asynccontextmanager
async def _real_outcomes_session():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _register_functions(dbapi_conn, _record) -> None:  # noqa: ANN001
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)

    _fix_sqlite_server_defaults(StrategyRosterProposalOutcome.__table__)
    StrategyRosterProposalOutcome.metadata.create_all(engine, tables=[StrategyRosterProposalOutcome.__table__])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class _AwaitableOutcomesSession:
    """Minimal AsyncSession-shaped adapter over a real synchronous ORM
    Session, scoped to exactly what fetch_strategy_scorecards() needs
    (db.execute). A real SQLite engine is used so both implementations
    below run genuine, independently-compiled SQL against the same seeded
    rows -- this is not a hand-rolled fake that echoes back whatever list it
    was given regardless of the query shape (unlike _FakeDb/_Result above,
    which don't distinguish a full-entity select from a narrow-column
    select), so it can actually prove the two query shapes produce
    identical scoring output."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)


_proposal_id_counter = count()


def _seed_outcome_row(
    session,  # noqa: ANN001
    *,
    strategy_slug: str,
    action: str,
    horizon_label: str,
    horizon_minutes: int,
    regime_trend: str,
    actual_action_correct: bool | None,
    actual_raw_return_pct: Decimal,
    actual_fee_adjusted_return_pct: Decimal,
    mfe_pct: Decimal,
    mae_pct: Decimal,
) -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    proposal_id = uuid.uuid4()
    session.add(
        StrategyRosterProposalOutcome(
            outcome_id=uuid.uuid4(),
            idempotency_key=f"idem-{next(_proposal_id_counter)}",
            proposal_id=proposal_id,
            roster_run_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slug=strategy_slug,
            strategy_identity=f"{strategy_slug}@1.0.0",
            action=action,
            proposal_evaluation_status="EVALUATED",
            horizon_label=horizon_label,
            horizon_minutes=horizon_minutes,
            proposal_candle_close_time=now,
            horizon_time=now + timedelta(minutes=horizon_minutes),
            evaluated_at=now + timedelta(seconds=horizon_minutes),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            market_return_pct=Decimal("1.0"),
            buy_raw_return_pct=Decimal("1.0"),
            buy_fee_adjusted_return_pct=Decimal("0.8"),
            sell_raw_return_pct=Decimal("-1.0"),
            sell_fee_adjusted_return_pct=Decimal("-1.2"),
            actual_raw_return_pct=actual_raw_return_pct,
            actual_fee_adjusted_return_pct=actual_fee_adjusted_return_pct,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            actual_action_correct=actual_action_correct,
            evaluation_completed=True,
            evaluation_state="RESOLVED",
            evaluation_reason=None,
            market_move="UP",
            regime_trend=regime_trend,
            regime_volatility="HIGH_VOLATILITY",
            regime_range="EXPANSION",
            fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"),
            hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW",
            live_submission_allowed=False,
        )
    )


async def _reference_fetch_strategy_scorecards_full_entity(
    *, db: Any, provider: str, product_id: str, interval: str,
):
    """Frozen copy of fetch_strategy_scorecards() exactly as it read before
    the column-narrowing optimization: select(StrategyRosterProposalOutcome)
    (all 39 mapped columns) + result.scalars().all(), followed by the
    unmodified grouping/scoring logic. Kept only in this test file as "the
    current implementation" for a byte-for-byte comparison against the
    optimized narrow-column select -- production code must never import
    this."""
    from sqlalchemy import select

    from app.services.strategy_outcomes.service import (
        HORIZONS,
        StrategyScorecard,
        StrategyScorecardBucket,
        _round,
        get_settings,
    )

    settings = get_settings()
    regime_min_evidence_required = int(getattr(settings, "outcome_scorecards_regime_min_evaluations", 50))

    result = await db.execute(
        select(StrategyRosterProposalOutcome)
        .where(StrategyRosterProposalOutcome.provider == provider)
        .where(StrategyRosterProposalOutcome.product_id == product_id)
        .where(StrategyRosterProposalOutcome.interval == interval)
        .where(StrategyRosterProposalOutcome.evaluation_state == "RESOLVED")
        .order_by(
            StrategyRosterProposalOutcome.strategy_slug.asc(),
            StrategyRosterProposalOutcome.evaluated_at.asc(),
            StrategyRosterProposalOutcome.outcome_id.asc(),
        )
    )
    rows = [row for row in result.scalars().all() if row.evaluation_state == "RESOLVED"]

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.strategy_slug, []).append(row)

    scorecards: list[StrategyScorecard] = []
    for strategy_slug in sorted(grouped):
        items = grouped[strategy_slug]
        scored_items = [item for item in items if item.actual_action_correct is not None]

        def _bucket(horizon_label: str, bucket_items: list[Any]) -> StrategyScorecardBucket:
            buy_items = [item for item in bucket_items if item.action == "BUY"]
            sell_items = [item for item in bucket_items if item.action == "SELL"]
            hold_items = [item for item in bucket_items if item.action == "HOLD"]

            buy_correct = sum(1 for item in buy_items if item.actual_action_correct)
            sell_correct = sum(1 for item in sell_items if item.actual_action_correct)
            hold_correct = sum(1 for item in hold_items if item.actual_action_correct)

            total = len(bucket_items)
            total_correct = buy_correct + sell_correct + hold_correct

            def _fee_adjusted_average(items: list[Any]) -> Decimal | None:
                if not items:
                    return None
                return sum((item.actual_fee_adjusted_return_pct or Decimal("0") for item in items), Decimal("0")) / Decimal(len(items))

            def _raw_average(items: list[Any]) -> Decimal | None:
                if not items:
                    return None
                return sum((item.actual_raw_return_pct or Decimal("0") for item in items), Decimal("0")) / Decimal(len(items))

            overall_correct_pct = None
            raw_avg = None
            fee_avg = None
            mfe_avg = None
            mae_avg = None
            if total > 0:
                overall_correct_pct = (Decimal(total_correct) * Decimal("100")) / Decimal(total)
                raw_avg = sum((item.actual_raw_return_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                fee_avg = sum((item.actual_fee_adjusted_return_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                mfe_avg = sum((item.mfe_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                mae_avg = sum((item.mae_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)

            return StrategyScorecardBucket(
                horizon_label=horizon_label,
                total_evaluated=total,
                buy_evaluations=len(buy_items),
                buy_correct=buy_correct,
                sell_evaluations=len(sell_items),
                sell_correct=sell_correct,
                hold_evaluations=len(hold_items),
                hold_correct=hold_correct,
                overall_correct_pct=_round(overall_correct_pct),
                average_raw_return_pct=_round(raw_avg),
                average_fee_adjusted_return_pct=_round(fee_avg),
                average_mfe_pct=_round(mfe_avg),
                average_mae_pct=_round(mae_avg),
                buy_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(buy_items)),
                sell_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(sell_items)),
                hold_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(hold_items)),
                buy_average_raw_return_pct=_round(_raw_average(buy_items)),
                sell_average_raw_return_pct=_round(_raw_average(sell_items)),
                hold_average_raw_return_pct=_round(_raw_average(hold_items)),
            )

        per_horizon: list[StrategyScorecardBucket] = []
        for horizon_label, _horizon_minutes in HORIZONS:
            horizon_items = [item for item in scored_items if item.horizon_label == horizon_label]
            per_horizon.append(_bucket(horizon_label, horizon_items))

        aggregate = _bucket("aggregate", scored_items)

        regime_groups: dict[str, list[Decimal]] = {}
        for item in scored_items:
            regime_groups.setdefault(item.regime_trend, []).append(item.actual_fee_adjusted_return_pct or Decimal("0"))

        best_regime = None
        worst_regime = None
        regime_evidence_count = len(scored_items)
        if regime_groups and regime_evidence_count >= regime_min_evidence_required:
            regime_avg = {
                regime: (sum(values, Decimal("0")) / Decimal(len(values)))
                for regime, values in regime_groups.items()
            }
            best_regime = max(regime_avg, key=lambda key: regime_avg[key])
            worst_regime = min(regime_avg, key=lambda key: regime_avg[key])

        scorecards.append(
            StrategyScorecard(
                strategy_slug=strategy_slug,
                per_horizon=per_horizon,
                aggregate=aggregate,
                best_regime=best_regime,
                worst_regime=worst_regime,
                regime_evidence_count=regime_evidence_count,
                regime_min_evidence_required=regime_min_evidence_required,
            )
        )

    return scorecards


@pytest.mark.asyncio
async def test_narrow_column_select_matches_full_entity_select_byte_for_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the column-narrowing optimization (real production timeout
    fix: query_ms was ~2.5-3.3s in production despite PostgreSQL executing
    the indexed query in ~76ms, because the full-entity select transfers
    and decodes all 39 mapped columns x ~18k rows instead of only the ~10
    the scoring logic reads) produces IDENTICAL StrategyScorecard output to
    the pre-optimization full-entity select, against a real SQLite-backed
    SQL round trip -- not the hand-rolled _FakeDb/_Result fakes used
    elsewhere in this file, which echo back whatever object list they were
    given regardless of the query's actual column shape and therefore
    cannot distinguish these two implementations at all."""
    monkeypatch.setattr(
        "app.services.strategy_outcomes.service.get_settings",
        lambda: SimpleNamespace(outcome_scorecards_regime_min_evaluations=3),
    )

    async with _real_outcomes_session() as raw_session:
        for i in range(6):
            _seed_outcome_row(
                raw_session,
                strategy_slug="momentum",
                action=("BUY", "SELL", "HOLD")[i % 3],
                horizon_label=HORIZONS[i % len(HORIZONS)][0],
                horizon_minutes=HORIZONS[i % len(HORIZONS)][1],
                regime_trend="TRENDING" if i % 2 == 0 else "RANGING",
                actual_action_correct=None if i == 5 else (i % 2 == 0),
                actual_raw_return_pct=Decimal(i) - Decimal("2.5"),
                actual_fee_adjusted_return_pct=Decimal(i) - Decimal("3.0"),
                mfe_pct=Decimal("1.5") + Decimal(i),
                mae_pct=Decimal("-0.5") - Decimal(i),
            )
        for i in range(5):
            _seed_outcome_row(
                raw_session,
                strategy_slug="breakout",
                action=("BUY", "SELL", "HOLD")[i % 3],
                horizon_label=HORIZONS[i % len(HORIZONS)][0],
                horizon_minutes=HORIZONS[i % len(HORIZONS)][1],
                regime_trend="RANGING",
                actual_action_correct=(i % 3 != 0),
                actual_raw_return_pct=Decimal("2.0") + Decimal(i),
                actual_fee_adjusted_return_pct=Decimal("1.5") + Decimal(i),
                mfe_pct=Decimal("3.0"),
                mae_pct=Decimal("-1.0"),
            )
        raw_session.commit()

        db = _AwaitableOutcomesSession(raw_session)

        reference_result = await _reference_fetch_strategy_scorecards_full_entity(
            db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m",
        )
        optimized_result = await fetch_strategy_scorecards(
            db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m",
        )

        assert len(reference_result) == 2
        assert reference_result == optimized_result


@pytest.mark.asyncio
async def test_scorecard_query_bounds_each_current_strategy_action_horizon_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.strategy_outcomes.service.get_settings",
        lambda: SimpleNamespace(
            outcome_scorecards_regime_min_evaluations=3,
            outcome_scorecards_max_samples_per_action_horizon=2,
        ),
    )
    async with _real_outcomes_session() as raw_session:
        for strategy_slug in ("momentum", "historical_unused"):
            for action in ("BUY", "SELL", "HOLD"):
                for index in range(3):
                    _seed_outcome_row(
                        raw_session,
                        strategy_slug=strategy_slug,
                        action=action,
                        horizon_label="15m",
                        horizon_minutes=15,
                        regime_trend="TRENDING",
                        actual_action_correct=True,
                        actual_raw_return_pct=Decimal(index),
                        actual_fee_adjusted_return_pct=Decimal(index),
                        mfe_pct=Decimal("1"),
                        mae_pct=Decimal("-1"),
                    )
        raw_session.commit()

        scorecards = await fetch_strategy_scorecards(
            db=_AwaitableOutcomesSession(raw_session),
            provider="kraken_spot",
            product_id="BTC-USD",
            interval="15m",
            strategy_slugs=["momentum"],
        )

    assert [item.strategy_slug for item in scorecards] == ["momentum"]
    aggregate = scorecards[0].aggregate
    assert aggregate.total_evaluated == 6
    assert aggregate.buy_evaluations == 2
    assert aggregate.sell_evaluations == 2
    assert aggregate.hold_evaluations == 2


async def _reference_fetch_strategy_scorecards_multi_pass(
    *, db: Any, provider: str, product_id: str, interval: str,
):
    """Frozen copy of fetch_strategy_scorecards() as it read at the start of
    this round: narrow-column select (already optimized in the prior round)
    but still the original multi-pass scoring -- per bucket, 3 action-
    partition passes, 3 correctness-sum passes, 4 full-bucket average passes,
    and 6 action-scoped average passes, x 5 buckets (4 horizons + aggregate)
    per strategy, plus a separate horizon-partition pass and a separate
    regime-grouping pass. Kept only in this test file as "the current
    implementation" for a byte-for-byte comparison against the single-pass
    _StrategyBucketAccumulator rewrite -- production code must never import
    this."""
    from sqlalchemy import select

    from app.services.strategy_outcomes.service import (
        HORIZONS,
        StrategyScorecard,
        StrategyScorecardBucket,
        _round,
        get_settings,
    )

    settings = get_settings()
    regime_min_evidence_required = int(getattr(settings, "outcome_scorecards_regime_min_evaluations", 50))

    result = await db.execute(
        select(
            StrategyRosterProposalOutcome.strategy_slug,
            StrategyRosterProposalOutcome.action,
            StrategyRosterProposalOutcome.actual_action_correct,
            StrategyRosterProposalOutcome.actual_raw_return_pct,
            StrategyRosterProposalOutcome.actual_fee_adjusted_return_pct,
            StrategyRosterProposalOutcome.mfe_pct,
            StrategyRosterProposalOutcome.mae_pct,
            StrategyRosterProposalOutcome.horizon_label,
            StrategyRosterProposalOutcome.regime_trend,
            StrategyRosterProposalOutcome.evaluation_state,
        )
        .where(StrategyRosterProposalOutcome.provider == provider)
        .where(StrategyRosterProposalOutcome.product_id == product_id)
        .where(StrategyRosterProposalOutcome.interval == interval)
        .where(StrategyRosterProposalOutcome.evaluation_state == "RESOLVED")
        .order_by(
            StrategyRosterProposalOutcome.strategy_slug.asc(),
            StrategyRosterProposalOutcome.evaluated_at.asc(),
            StrategyRosterProposalOutcome.outcome_id.asc(),
        )
    )
    rows = [row for row in result.all() if row.evaluation_state == "RESOLVED"]

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.strategy_slug, []).append(row)

    scorecards: list[StrategyScorecard] = []
    for strategy_slug in sorted(grouped):
        items = grouped[strategy_slug]
        scored_items = [item for item in items if item.actual_action_correct is not None]

        def _bucket(horizon_label: str, bucket_items: list[Any]) -> StrategyScorecardBucket:
            buy_items = [item for item in bucket_items if item.action == "BUY"]
            sell_items = [item for item in bucket_items if item.action == "SELL"]
            hold_items = [item for item in bucket_items if item.action == "HOLD"]

            buy_correct = sum(1 for item in buy_items if item.actual_action_correct)
            sell_correct = sum(1 for item in sell_items if item.actual_action_correct)
            hold_correct = sum(1 for item in hold_items if item.actual_action_correct)

            total = len(bucket_items)
            total_correct = buy_correct + sell_correct + hold_correct

            def _fee_adjusted_average(items: list[Any]) -> Decimal | None:
                if not items:
                    return None
                return sum((item.actual_fee_adjusted_return_pct or Decimal("0") for item in items), Decimal("0")) / Decimal(len(items))

            def _raw_average(items: list[Any]) -> Decimal | None:
                if not items:
                    return None
                return sum((item.actual_raw_return_pct or Decimal("0") for item in items), Decimal("0")) / Decimal(len(items))

            overall_correct_pct = None
            raw_avg = None
            fee_avg = None
            mfe_avg = None
            mae_avg = None
            if total > 0:
                overall_correct_pct = (Decimal(total_correct) * Decimal("100")) / Decimal(total)
                raw_avg = sum((item.actual_raw_return_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                fee_avg = sum((item.actual_fee_adjusted_return_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                mfe_avg = sum((item.mfe_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)
                mae_avg = sum((item.mae_pct or Decimal("0") for item in bucket_items), Decimal("0")) / Decimal(total)

            return StrategyScorecardBucket(
                horizon_label=horizon_label,
                total_evaluated=total,
                buy_evaluations=len(buy_items),
                buy_correct=buy_correct,
                sell_evaluations=len(sell_items),
                sell_correct=sell_correct,
                hold_evaluations=len(hold_items),
                hold_correct=hold_correct,
                overall_correct_pct=_round(overall_correct_pct),
                average_raw_return_pct=_round(raw_avg),
                average_fee_adjusted_return_pct=_round(fee_avg),
                average_mfe_pct=_round(mfe_avg),
                average_mae_pct=_round(mae_avg),
                buy_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(buy_items)),
                sell_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(sell_items)),
                hold_average_fee_adjusted_return_pct=_round(_fee_adjusted_average(hold_items)),
                buy_average_raw_return_pct=_round(_raw_average(buy_items)),
                sell_average_raw_return_pct=_round(_raw_average(sell_items)),
                hold_average_raw_return_pct=_round(_raw_average(hold_items)),
            )

        per_horizon: list[StrategyScorecardBucket] = []
        for horizon_label, _horizon_minutes in HORIZONS:
            horizon_items = [item for item in scored_items if item.horizon_label == horizon_label]
            per_horizon.append(_bucket(horizon_label, horizon_items))

        aggregate = _bucket("aggregate", scored_items)

        regime_groups: dict[str, list[Decimal]] = {}
        for item in scored_items:
            regime_groups.setdefault(item.regime_trend, []).append(item.actual_fee_adjusted_return_pct or Decimal("0"))

        best_regime = None
        worst_regime = None
        regime_evidence_count = len(scored_items)
        if regime_groups and regime_evidence_count >= regime_min_evidence_required:
            regime_avg = {
                regime: (sum(values, Decimal("0")) / Decimal(len(values)))
                for regime, values in regime_groups.items()
            }
            best_regime = max(regime_avg, key=lambda key: regime_avg[key])
            worst_regime = min(regime_avg, key=lambda key: regime_avg[key])

        scorecards.append(
            StrategyScorecard(
                strategy_slug=strategy_slug,
                per_horizon=per_horizon,
                aggregate=aggregate,
                best_regime=best_regime,
                worst_regime=worst_regime,
                regime_evidence_count=regime_evidence_count,
                regime_min_evidence_required=regime_min_evidence_required,
            )
        )

    return scorecards


@pytest.mark.asyncio
async def test_single_pass_accumulator_matches_multi_pass_reference_byte_for_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the single-pass _StrategyBucketAccumulator rewrite (replacing
    ~10 full passes per bucket x 5 buckets per strategy, plus a separate
    horizon-partition pass and a separate regime-grouping pass, with one scan
    over scored_items) produces IDENTICAL StrategyScorecard output to the
    original multi-pass scoring logic, against a real SQLite-backed SQL round
    trip -- not the hand-rolled _FakeDb/_Result fakes elsewhere in this file."""
    monkeypatch.setattr(
        "app.services.strategy_outcomes.service.get_settings",
        lambda: SimpleNamespace(outcome_scorecards_regime_min_evaluations=3),
    )

    async with _real_outcomes_session() as raw_session:
        # Wider and messier than the narrow-column test's fixture: uneven
        # action distribution, a regime with only one member, multiple
        # strategies with different scored-item counts, and both action-
        # scoped averages that would diverge if buy/sell/hold sums were ever
        # cross-contaminated by the accumulator rewrite.
        for i in range(11):
            _seed_outcome_row(
                raw_session,
                strategy_slug="momentum",
                action=("BUY", "BUY", "SELL", "HOLD")[i % 4],
                horizon_label=HORIZONS[i % len(HORIZONS)][0],
                horizon_minutes=HORIZONS[i % len(HORIZONS)][1],
                regime_trend=("TRENDING", "RANGING", "TRENDING")[i % 3],
                actual_action_correct=None if i == 10 else (i % 3 != 0),
                actual_raw_return_pct=Decimal(i) - Decimal("4.5"),
                actual_fee_adjusted_return_pct=Decimal(i) - Decimal("5.0"),
                mfe_pct=Decimal("0.5") * Decimal(i),
                mae_pct=Decimal("-0.25") * Decimal(i),
            )
        for i in range(4):
            _seed_outcome_row(
                raw_session,
                strategy_slug="breakout",
                action=("SELL", "HOLD")[i % 2],
                horizon_label="15m",
                horizon_minutes=15,
                regime_trend="RANGING",
                actual_action_correct=(i % 2 == 0),
                actual_raw_return_pct=Decimal("-1.0") - Decimal(i),
                actual_fee_adjusted_return_pct=Decimal("-1.5") - Decimal(i),
                mfe_pct=Decimal("0.2"),
                mae_pct=Decimal("-2.0"),
            )
        raw_session.commit()

        db = _AwaitableOutcomesSession(raw_session)

        reference_result = await _reference_fetch_strategy_scorecards_multi_pass(
            db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m",
        )
        optimized_result = await fetch_strategy_scorecards(
            db=db, provider="kraken_spot", product_id="BTC-USD", interval="15m",
        )

        assert len(reference_result) == 2
        assert reference_result == optimized_result
