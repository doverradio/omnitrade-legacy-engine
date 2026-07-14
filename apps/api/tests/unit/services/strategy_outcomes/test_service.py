from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
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
