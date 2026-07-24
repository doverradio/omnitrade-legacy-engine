from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from collections.abc import Collection
from typing import Any
import hashlib
import json
import logging
import statistics
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.candle import Candle
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome


HORIZONS: tuple[tuple[str, int], ...] = (("15m", 15), ("1h", 60), ("4h", 240), ("24h", 1440))

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StrategyOutcomeScoringResult:
    scanned_proposals: int
    inserted_outcomes: int
    skipped_not_due: int
    skipped_existing: int
    skipped_missing_prices: int


@dataclass(frozen=True, slots=True)
class StrategyScorecardBucket:
    horizon_label: str
    total_evaluated: int
    buy_evaluations: int
    buy_correct: int
    sell_evaluations: int
    sell_correct: int
    hold_evaluations: int
    hold_correct: int
    overall_correct_pct: Decimal | None
    average_raw_return_pct: Decimal | None
    average_fee_adjusted_return_pct: Decimal | None
    average_mfe_pct: Decimal | None
    average_mae_pct: Decimal | None
    # Action-specific fee-adjusted return averages. average_fee_adjusted_return_pct
    # above blends BUY+SELL+HOLD outcomes together, which is meaningless as an
    # expected-edge estimate for a specific proposed action (a strategy's
    # historical SELL outcomes say nothing about whether its BUY calls make
    # money, and vice versa). Callers estimating the economic edge of a
    # specific proposed action must use the matching field here instead of
    # the blended aggregate. None when there is no evidence for that action.
    buy_average_fee_adjusted_return_pct: Decimal | None = None
    sell_average_fee_adjusted_return_pct: Decimal | None = None
    hold_average_fee_adjusted_return_pct: Decimal | None = None
    # Action-specific RAW (pre-fee) return averages. This is the genuinely
    # "gross" figure -- callers computing an expected NET edge (i.e. that will
    # apply their own fee/slippage model on top) must source their gross
    # input from here, never from the *_average_fee_adjusted_return_pct
    # fields above, which are already net of outcome-scoring's own round-trip
    # fee assumption. None when there is no evidence for that action.
    buy_average_raw_return_pct: Decimal | None = None
    sell_average_raw_return_pct: Decimal | None = None
    hold_average_raw_return_pct: Decimal | None = None


@dataclass(frozen=True, slots=True)
class StrategyScorecard:
    strategy_slug: str
    per_horizon: list[StrategyScorecardBucket]
    aggregate: StrategyScorecardBucket
    best_regime: str | None
    worst_regime: str | None
    regime_evidence_count: int
    regime_min_evidence_required: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _to_pct(value: Decimal) -> Decimal:
    return value * Decimal("100")


def _round(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal("0.0001"))


def _score_buy_correct(*, buy_fee_adjusted_return_pct: Decimal) -> bool:
    return buy_fee_adjusted_return_pct > Decimal("0")


def _score_sell_correct(*, sell_fee_adjusted_return_pct: Decimal) -> bool:
    return sell_fee_adjusted_return_pct > Decimal("0")


def _score_hold_correct(
    *,
    buy_fee_adjusted_return_pct: Decimal,
    sell_fee_adjusted_return_pct: Decimal,
    hold_buy_threshold_pct: Decimal,
    hold_sell_threshold_pct: Decimal,
) -> bool:
    return buy_fee_adjusted_return_pct <= hold_buy_threshold_pct and sell_fee_adjusted_return_pct <= hold_sell_threshold_pct


def _market_move(*, market_return_pct: Decimal, sideways_threshold_pct: Decimal) -> str:
    if market_return_pct > sideways_threshold_pct:
        return "UP"
    if market_return_pct < (Decimal("0") - sideways_threshold_pct):
        return "DOWN"
    return "SIDEWAYS"


def _regime_labels(*, closes: list[Decimal], highs: list[Decimal], lows: list[Decimal]) -> tuple[str, str, str]:
    if len(closes) < 2:
        return "RANGING", "LOW_VOLATILITY", "COMPRESSION"

    start_close = closes[0]
    end_close = closes[-1]
    net_return_pct = Decimal("0") if start_close == 0 else _to_pct((end_close - start_close) / start_close)
    trend = "TRENDING" if abs(net_return_pct) >= Decimal("0.60") else "RANGING"

    returns: list[float] = []
    for idx in range(1, len(closes)):
        prev = closes[idx - 1]
        curr = closes[idx]
        if prev != 0:
            returns.append(float((curr - prev) / prev))
    vol = statistics.pstdev(returns) if returns else 0.0
    volatility = "HIGH_VOLATILITY" if vol >= 0.004 else "LOW_VOLATILITY"

    ranges: list[Decimal] = []
    for idx in range(len(highs)):
        close_ref = closes[idx]
        if close_ref == 0:
            continue
        ranges.append(_to_pct((highs[idx] - lows[idx]) / close_ref))
    if not ranges:
        return trend, volatility, "COMPRESSION"

    pivot = max(1, len(ranges) // 2)
    first_avg = sum(ranges[:pivot], Decimal("0")) / Decimal(len(ranges[:pivot]))
    second_avg = sum(ranges[pivot:], Decimal("0")) / Decimal(len(ranges[pivot:]))
    range_regime = "EXPANSION" if second_avg > (first_avg + Decimal("0.10")) else "COMPRESSION"
    return trend, volatility, range_regime


async def _load_close_at_or_before(*, db: AsyncSession, asset_id: uuid.UUID, interval: str, target: datetime) -> Decimal | None:
    return await db.scalar(
        select(Candle.close)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .where(Candle.close_time <= target)
        .order_by(Candle.close_time.desc())
        .limit(1)
    )


async def _load_window_candles(
    *,
    db: AsyncSession,
    asset_id: uuid.UUID,
    interval: str,
    start_exclusive: datetime,
    end_inclusive: datetime,
) -> list[Candle]:
    result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .where(Candle.close_time > start_exclusive)
        .where(Candle.close_time <= end_inclusive)
        .order_by(Candle.close_time.asc())
    )
    return list(result.scalars().all())


async def score_due_strategy_roster_proposal_outcomes(
    *,
    db: AsyncSession,
    as_of: datetime | None = None,
) -> StrategyOutcomeScoringResult:
    settings = get_settings()
    now = _utc(as_of or datetime.now(timezone.utc))
    earliest_due = now - timedelta(minutes=HORIZONS[0][1])

    fee_bps = Decimal(str(getattr(settings, "outcome_scoring_fee_bps", Decimal("10"))))
    hold_buy_threshold_pct = Decimal(str(getattr(settings, "outcome_scoring_hold_buy_threshold_pct", Decimal("0"))))
    hold_sell_threshold_pct = Decimal(str(getattr(settings, "outcome_scoring_hold_sell_threshold_pct", Decimal("0"))))
    sideways_threshold_pct = Decimal(str(getattr(settings, "outcome_scoring_sideways_threshold_pct", Decimal("0.10"))))

    result = await db.execute(
        select(StrategyRosterProposal)
        .where(StrategyRosterProposal.candle_close_time <= earliest_due)
        .order_by(StrategyRosterProposal.candle_close_time.asc(), StrategyRosterProposal.proposal_id.asc())
    )
    proposals = list(result.scalars().all())

    inserted = 0
    skipped_not_due = 0
    skipped_existing = 0
    skipped_missing_prices = 0

    roundtrip_fee_pct = (fee_bps * Decimal("2")) / Decimal("100")

    for proposal in proposals:
        close_time = _utc(proposal.candle_close_time)

        for horizon_label, horizon_minutes in HORIZONS:
            horizon_time = close_time + timedelta(minutes=horizon_minutes)
            if horizon_time > now:
                skipped_not_due += 1
                continue

            existing = await db.scalar(
                select(StrategyRosterProposalOutcome.outcome_id)
                .where(StrategyRosterProposalOutcome.proposal_id == proposal.proposal_id)
                .where(StrategyRosterProposalOutcome.horizon_minutes == horizon_minutes)
                .limit(1)
            )
            if existing is not None:
                skipped_existing += 1
                continue

            entry_price = await _load_close_at_or_before(
                db=db,
                asset_id=proposal.asset_id,
                interval=proposal.interval,
                target=close_time,
            )
            exit_price = await _load_close_at_or_before(
                db=db,
                asset_id=proposal.asset_id,
                interval=proposal.interval,
                target=horizon_time,
            )
            if entry_price is None or exit_price is None or entry_price == 0:
                skipped_missing_prices += 1
                continue

            window_candles = await _load_window_candles(
                db=db,
                asset_id=proposal.asset_id,
                interval=proposal.interval,
                start_exclusive=close_time,
                end_inclusive=horizon_time,
            )

            market_return_pct = _to_pct((exit_price - entry_price) / entry_price)
            buy_raw_return_pct = market_return_pct
            sell_raw_return_pct = Decimal("0") - market_return_pct
            buy_fee_adjusted_return_pct = buy_raw_return_pct - roundtrip_fee_pct
            sell_fee_adjusted_return_pct = sell_raw_return_pct - roundtrip_fee_pct

            action = proposal.action.upper()
            evaluation_status = proposal.evaluation_status.upper()

            actual_raw_return_pct: Decimal | None = None
            actual_fee_adjusted_return_pct: Decimal | None = None
            actual_action_correct: bool | None = None
            evaluation_state = "RESOLVED"
            evaluation_reason: str | None = None

            highs = [entry_price]
            lows = [entry_price]
            closes = [entry_price]
            for candle in window_candles:
                highs.append(Decimal(str(candle.high)))
                lows.append(Decimal(str(candle.low)))
                closes.append(Decimal(str(candle.close)))
            highs.append(exit_price)
            lows.append(exit_price)
            closes.append(exit_price)

            if evaluation_status != "EVALUATED":
                evaluation_state = "PROPOSAL_NOT_EVALUATED"
                evaluation_reason = proposal.reason
            elif action == "BUY":
                actual_raw_return_pct = buy_raw_return_pct
                actual_fee_adjusted_return_pct = buy_fee_adjusted_return_pct
                actual_action_correct = _score_buy_correct(buy_fee_adjusted_return_pct=buy_fee_adjusted_return_pct)
            elif action == "SELL":
                actual_raw_return_pct = sell_raw_return_pct
                actual_fee_adjusted_return_pct = sell_fee_adjusted_return_pct
                actual_action_correct = _score_sell_correct(sell_fee_adjusted_return_pct=sell_fee_adjusted_return_pct)
            else:
                actual_raw_return_pct = Decimal("0")
                actual_fee_adjusted_return_pct = Decimal("0")
                actual_action_correct = _score_hold_correct(
                    buy_fee_adjusted_return_pct=buy_fee_adjusted_return_pct,
                    sell_fee_adjusted_return_pct=sell_fee_adjusted_return_pct,
                    hold_buy_threshold_pct=hold_buy_threshold_pct,
                    hold_sell_threshold_pct=hold_sell_threshold_pct,
                )

            if action == "BUY":
                mfe_pct = _to_pct((max(highs) - entry_price) / entry_price)
                mae_pct = _to_pct((min(lows) - entry_price) / entry_price)
            elif action == "SELL":
                mfe_pct = _to_pct((entry_price - min(lows)) / entry_price)
                mae_pct = _to_pct((entry_price - max(highs)) / entry_price)
            else:
                mfe_pct = _to_pct((max(highs) - entry_price) / entry_price)
                mae_pct = _to_pct((min(lows) - entry_price) / entry_price)

            regime_trend, regime_volatility, regime_range = _regime_labels(
                closes=closes,
                highs=highs,
                lows=lows,
            )

            outcome = StrategyRosterProposalOutcome(
                idempotency_key=_hash(
                    {
                        "kind": "strategy_roster_proposal_outcome",
                        "proposal_id": str(proposal.proposal_id),
                        "horizon_minutes": horizon_minutes,
                    }
                ),
                proposal_id=proposal.proposal_id,
                roster_run_id=proposal.roster_run_id,
                asset_id=proposal.asset_id,
                provider=proposal.provider,
                product_id=proposal.product_id,
                interval=proposal.interval,
                strategy_slug=proposal.strategy_slug,
                strategy_identity=proposal.strategy_identity,
                action=action,
                proposal_evaluation_status=evaluation_status,
                horizon_label=horizon_label,
                horizon_minutes=horizon_minutes,
                proposal_candle_close_time=proposal.candle_close_time,
                horizon_time=horizon_time,
                evaluated_at=now,
                entry_price=_round(entry_price) or Decimal("0"),
                exit_price=_round(exit_price) or Decimal("0"),
                market_return_pct=_round(market_return_pct) or Decimal("0"),
                buy_raw_return_pct=_round(buy_raw_return_pct) or Decimal("0"),
                buy_fee_adjusted_return_pct=_round(buy_fee_adjusted_return_pct) or Decimal("0"),
                sell_raw_return_pct=_round(sell_raw_return_pct) or Decimal("0"),
                sell_fee_adjusted_return_pct=_round(sell_fee_adjusted_return_pct) or Decimal("0"),
                actual_raw_return_pct=_round(actual_raw_return_pct),
                actual_fee_adjusted_return_pct=_round(actual_fee_adjusted_return_pct),
                mfe_pct=_round(mfe_pct),
                mae_pct=_round(mae_pct),
                actual_action_correct=actual_action_correct,
                evaluation_completed=True,
                evaluation_state=evaluation_state,
                evaluation_reason=evaluation_reason,
                market_move=_market_move(market_return_pct=market_return_pct, sideways_threshold_pct=sideways_threshold_pct),
                regime_trend=regime_trend,
                regime_volatility=regime_volatility,
                regime_range=regime_range,
                fee_bps=fee_bps,
                hold_buy_threshold_pct=hold_buy_threshold_pct,
                hold_sell_threshold_pct=hold_sell_threshold_pct,
                execution_mode="SHADOW",
                live_submission_allowed=False,
            )
            db.add(outcome)
            inserted += 1

    await db.commit()

    return StrategyOutcomeScoringResult(
        scanned_proposals=len(proposals),
        inserted_outcomes=inserted,
        skipped_not_due=skipped_not_due,
        skipped_existing=skipped_existing,
        skipped_missing_prices=skipped_missing_prices,
    )


class _StrategyBucketAccumulator:
    """Collects everything a StrategyScorecardBucket needs (counts, correctness,
    and the four full-bucket sums plus the six action-scoped sums) in a single
    linear scan. fetch_strategy_scorecards() previously ran, per bucket: 3
    action-partition passes, 3 correctness-sum passes, 4 full-bucket average
    passes, and 6 action-scoped average passes -- 5 buckets (4 horizons +
    aggregate) per strategy, all re-scanning largely the same rows. Every one
    of those sums/counts is order-independent (Decimal addition is exact and
    associative here -- no precision is lost by accumulating one row at a time
    instead of via sum() over a pre-built list), so a single .add(item) call
    per row, per bucket the row belongs to, produces identical totals."""

    __slots__ = (
        "total", "buy_count", "buy_correct", "sell_count", "sell_correct",
        "hold_count", "hold_correct",
        "sum_raw", "sum_fee", "sum_mfe", "sum_mae",
        "buy_sum_raw", "buy_sum_fee",
        "sell_sum_raw", "sell_sum_fee",
        "hold_sum_raw", "hold_sum_fee",
    )

    def __init__(self) -> None:
        self.total = 0
        self.buy_count = 0
        self.buy_correct = 0
        self.sell_count = 0
        self.sell_correct = 0
        self.hold_count = 0
        self.hold_correct = 0
        self.sum_raw = Decimal("0")
        self.sum_fee = Decimal("0")
        self.sum_mfe = Decimal("0")
        self.sum_mae = Decimal("0")
        self.buy_sum_raw = Decimal("0")
        self.buy_sum_fee = Decimal("0")
        self.sell_sum_raw = Decimal("0")
        self.sell_sum_fee = Decimal("0")
        self.hold_sum_raw = Decimal("0")
        self.hold_sum_fee = Decimal("0")

    def add(self, item: Any) -> None:
        raw = item.actual_raw_return_pct or Decimal("0")
        fee = item.actual_fee_adjusted_return_pct or Decimal("0")

        self.total += 1
        self.sum_raw += raw
        self.sum_fee += fee
        self.sum_mfe += (item.mfe_pct or Decimal("0"))
        self.sum_mae += (item.mae_pct or Decimal("0"))

        action = item.action
        # scored_items (the only caller) already filters out
        # actual_action_correct is None, so this is always a real bool here --
        # same guarantee the original `if item.actual_action_correct` relied on.
        correct = bool(item.actual_action_correct)
        if action == "BUY":
            self.buy_count += 1
            self.buy_sum_raw += raw
            self.buy_sum_fee += fee
            if correct:
                self.buy_correct += 1
        elif action == "SELL":
            self.sell_count += 1
            self.sell_sum_raw += raw
            self.sell_sum_fee += fee
            if correct:
                self.sell_correct += 1
        elif action == "HOLD":
            self.hold_count += 1
            self.hold_sum_raw += raw
            self.hold_sum_fee += fee
            if correct:
                self.hold_correct += 1

    def to_bucket(self, horizon_label: str) -> StrategyScorecardBucket:
        total = self.total
        total_correct = self.buy_correct + self.sell_correct + self.hold_correct

        overall_correct_pct = None
        raw_avg = None
        fee_avg = None
        mfe_avg = None
        mae_avg = None
        if total > 0:
            overall_correct_pct = (Decimal(total_correct) * Decimal("100")) / Decimal(total)
            raw_avg = self.sum_raw / Decimal(total)
            fee_avg = self.sum_fee / Decimal(total)
            mfe_avg = self.sum_mfe / Decimal(total)
            mae_avg = self.sum_mae / Decimal(total)

        def _avg(total_value: Decimal, count: int) -> Decimal | None:
            if count == 0:
                return None
            return total_value / Decimal(count)

        return StrategyScorecardBucket(
            horizon_label=horizon_label,
            total_evaluated=total,
            buy_evaluations=self.buy_count,
            buy_correct=self.buy_correct,
            sell_evaluations=self.sell_count,
            sell_correct=self.sell_correct,
            hold_evaluations=self.hold_count,
            hold_correct=self.hold_correct,
            overall_correct_pct=_round(overall_correct_pct),
            average_raw_return_pct=_round(raw_avg),
            average_fee_adjusted_return_pct=_round(fee_avg),
            average_mfe_pct=_round(mfe_avg),
            average_mae_pct=_round(mae_avg),
            buy_average_fee_adjusted_return_pct=_round(_avg(self.buy_sum_fee, self.buy_count)),
            sell_average_fee_adjusted_return_pct=_round(_avg(self.sell_sum_fee, self.sell_count)),
            hold_average_fee_adjusted_return_pct=_round(_avg(self.hold_sum_fee, self.hold_count)),
            buy_average_raw_return_pct=_round(_avg(self.buy_sum_raw, self.buy_count)),
            sell_average_raw_return_pct=_round(_avg(self.sell_sum_raw, self.sell_count)),
            hold_average_raw_return_pct=_round(_avg(self.hold_sum_raw, self.hold_count)),
        )


async def fetch_strategy_scorecards(
    *,
    db: AsyncSession,
    provider: str,
    product_id: str,
    interval: str,
    strategy_slugs: Collection[str] | None = None,
) -> list[StrategyScorecard]:
    settings = get_settings()
    regime_min_evidence_required = int(
        getattr(settings, "outcome_scorecards_regime_min_evaluations", 50)
    )
    max_samples_per_bucket = int(
        getattr(settings, "outcome_scorecards_max_samples_per_action_horizon", 100)
    )
    normalized_strategy_slugs = sorted({str(item).strip() for item in (strategy_slugs or ()) if str(item).strip()})

    # Phase-timed diagnostic instrumentation (production performance
    # investigation into fetch_strategy_scorecards() timeouts). Each phase
    # is measured with perf_counter() around exactly the work it names.
    # Confirmed via production EXPLAIN (ANALYZE, BUFFERS) that PostgreSQL
    # itself executes this query in ~76ms fully served from shared_buffers
    # (no sequential scan, no disk I/O) -- the multi-second query_ms actually
    # observed in production is network transfer + asyncpg decoding of the
    # full-entity result set (all 39 mapped columns x ~18k rows, ~389 bytes/
    # row per Postgres's own width estimate, ~7MB total), which EXPLAIN
    # ANALYZE's server-side "Execution Time" never measures. The SELECT
    # query below therefore both selects only the fields scoring reads and
    # ranks outcomes per current-strategy/action/horizon bucket.  The hard
    # per-bucket cap bounds transfer and decoding without allowing one action
    # or horizon to starve BUY, SELL, HOLD, or another horizon. Missing
    # evidence remains missing and is handled by the existing fail-closed
    # aggregator rules.
    t_query_start = time.perf_counter()
    ranked_outcomes = (
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
            StrategyRosterProposalOutcome.evaluated_at,
            StrategyRosterProposalOutcome.outcome_id,
            func.row_number().over(
                partition_by=(
                    StrategyRosterProposalOutcome.strategy_slug,
                    StrategyRosterProposalOutcome.action,
                    StrategyRosterProposalOutcome.horizon_label,
                ),
                order_by=(
                    StrategyRosterProposalOutcome.evaluated_at.desc(),
                    StrategyRosterProposalOutcome.outcome_id.desc(),
                ),
            ).label("scorecard_rank"),
        )
        .where(StrategyRosterProposalOutcome.provider == provider)
        .where(StrategyRosterProposalOutcome.product_id == product_id)
        .where(StrategyRosterProposalOutcome.interval == interval)
        .where(StrategyRosterProposalOutcome.evaluation_state == "RESOLVED")
    )
    if normalized_strategy_slugs:
        ranked_outcomes = ranked_outcomes.where(
            StrategyRosterProposalOutcome.strategy_slug.in_(normalized_strategy_slugs)
        )
    ranked_outcomes = ranked_outcomes.subquery()
    result = await db.execute(
        select(
            ranked_outcomes.c.strategy_slug,
            ranked_outcomes.c.action,
            ranked_outcomes.c.actual_action_correct,
            ranked_outcomes.c.actual_raw_return_pct,
            ranked_outcomes.c.actual_fee_adjusted_return_pct,
            ranked_outcomes.c.mfe_pct,
            ranked_outcomes.c.mae_pct,
            ranked_outcomes.c.horizon_label,
            ranked_outcomes.c.regime_trend,
            ranked_outcomes.c.evaluation_state,
        )
        .where(ranked_outcomes.c.scorecard_rank <= max_samples_per_bucket)
        .order_by(
            ranked_outcomes.c.strategy_slug.asc(),
            ranked_outcomes.c.evaluated_at.asc(),
            ranked_outcomes.c.outcome_id.asc(),
        )
    )
    t_query_done = time.perf_counter()

    rows = [
        row
        for row in result.all()
        if row.evaluation_state == "RESOLVED"
    ]
    t_hydration_done = time.perf_counter()

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.strategy_slug, []).append(row)
    t_grouping_done = time.perf_counter()

    scorecards: list[StrategyScorecard] = []
    for strategy_slug in sorted(grouped):
        items = grouped[strategy_slug]
        scored_items = [item for item in items if item.actual_action_correct is not None]

        horizon_accumulators: dict[str, _StrategyBucketAccumulator] = {
            horizon_label: _StrategyBucketAccumulator() for horizon_label, _horizon_minutes in HORIZONS
        }
        aggregate_accumulator = _StrategyBucketAccumulator()
        regime_sums: dict[str, Decimal] = {}
        regime_counts: dict[str, int] = {}

        for item in scored_items:
            aggregate_accumulator.add(item)
            horizon_accumulator = horizon_accumulators.get(item.horizon_label)
            if horizon_accumulator is not None:
                horizon_accumulator.add(item)

            regime = item.regime_trend
            regime_sums[regime] = regime_sums.get(regime, Decimal("0")) + (item.actual_fee_adjusted_return_pct or Decimal("0"))
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

        per_horizon: list[StrategyScorecardBucket] = [
            horizon_accumulators[horizon_label].to_bucket(horizon_label)
            for horizon_label, _horizon_minutes in HORIZONS
        ]
        aggregate = aggregate_accumulator.to_bucket("aggregate")

        best_regime = None
        worst_regime = None
        regime_evidence_count = len(scored_items)
        if regime_sums and regime_evidence_count >= regime_min_evidence_required:
            regime_avg = {
                regime: (regime_sums[regime] / Decimal(regime_counts[regime]))
                for regime in regime_sums
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

    t_scoring_done = time.perf_counter()
    logger.info(
        "strategy_scorecard_fetch_timing provider=%s product_id=%s interval=%s "
        "row_count=%d strategy_count=%d requested_strategy_count=%d max_samples_per_action_horizon=%d "
        "query_ms=%.1f hydration_ms=%.1f grouping_ms=%.1f scoring_ms=%.1f total_ms=%.1f",
        provider, product_id, interval,
        len(rows), len(grouped), len(normalized_strategy_slugs), max_samples_per_bucket,
        (t_query_done - t_query_start) * 1000,
        (t_hydration_done - t_query_done) * 1000,
        (t_grouping_done - t_hydration_done) * 1000,
        (t_scoring_done - t_grouping_done) * 1000,
        (t_scoring_done - t_query_start) * 1000,
    )
    return scorecards
