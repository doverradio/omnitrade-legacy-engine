from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.strategies.base import StrategyContext
from app.services.strategies.identity import build_strategy_identity
from app.services.strategies.registry import StrategyLookupError, strategy_registry
from app.services.strategy_roster.contracts import StrategyRosterRequest, StrategyRosterRunResult
from app.services.strategy_roster.registry import ENABLED_PHASE1_ROSTER, minimum_history_required

logger = logging.getLogger(__name__)

_STALE_CANDLE_MAX_MINUTES = 90


def _hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_roster_run_idempotency_key(*, request: StrategyRosterRequest) -> str:
    return _hash_payload(
        {
            "kind": "strategy_roster_run",
            "asset_id": str(request.asset_id),
            "provider": request.provider,
            "interval": request.interval,
            "candle_close_time": request.candle_close_time.isoformat(),
            "trigger": request.trigger,
        }
    )


def _build_proposal_idempotency_key(
    *,
    request: StrategyRosterRequest,
    strategy_identity: str,
    parameter_set_identity: str,
) -> str:
    return _hash_payload(
        {
            "kind": "strategy_roster_proposal",
            "asset_id": str(request.asset_id),
            "interval": request.interval,
            "candle_close_time": request.candle_close_time.isoformat(),
            "strategy_identity": strategy_identity,
            "parameter_set_identity": parameter_set_identity,
        }
    )


def _uppercase_action(action: str) -> str:
    normalized = action.strip().upper()
    if normalized in {"BUY", "SELL", "HOLD"}:
        return normalized
    return "HOLD"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def run_strategy_roster_for_candle(
    *,
    db: AsyncSession,
    request: StrategyRosterRequest,
) -> StrategyRosterRunResult:
    run_key = _build_roster_run_idempotency_key(request=request)
    existing_run = await db.scalar(
        select(StrategyRosterRun)
        .where(StrategyRosterRun.idempotency_key == run_key)
        .limit(1)
    )
    if existing_run is not None:
        logger.info(
            "strategy_roster_replayed roster_run_id=%s asset_id=%s candle_close=%s execution_mode=shadow live_submission=false",
            existing_run.roster_run_id,
            request.asset_id,
            request.candle_close_time.isoformat(),
        )
        return StrategyRosterRunResult(
            roster_run_id=existing_run.roster_run_id,
            replayed=True,
            strategies_requested_count=existing_run.strategies_requested_count,
            strategies_completed_count=existing_run.strategies_completed_count,
            strategies_failed_count=existing_run.strategies_failed_count,
            buy_count=existing_run.buy_count,
            sell_count=existing_run.sell_count,
            hold_count=existing_run.hold_count,
        )

    started_at = datetime.now(timezone.utc)
    logger.info(
        "strategy_roster_started asset_id=%s candle_close=%s trigger=%s execution_mode=shadow live_submission=false",
        request.asset_id,
        request.candle_close_time.isoformat(),
        request.trigger,
    )

    run = StrategyRosterRun(
        idempotency_key=run_key,
        asset_id=request.asset_id,
        provider=request.provider,
        product_id=request.product_id,
        interval=request.interval,
        candle_open_time=request.candle_open_time,
        candle_close_time=request.candle_close_time,
        trigger=request.trigger,
        scheduled_cycle_id=request.scheduled_cycle_id,
        strategies_requested=list(ENABLED_PHASE1_ROSTER),
        strategies_completed=[],
        strategies_failed=[],
        strategies_requested_count=len(ENABLED_PHASE1_ROSTER),
        strategies_completed_count=0,
        strategies_failed_count=0,
        buy_count=0,
        sell_count=0,
        hold_count=0,
        error_summary={},
        started_at=started_at,
        completed_at=None,
    )
    db.add(run)
    await db.flush()

    strategy_rows_result = await db.execute(
        select(Strategy)
        .where(Strategy.slug.in_(ENABLED_PHASE1_ROSTER))
    )
    strategy_rows = {item.slug: item for item in strategy_rows_result.scalars().all()}

    candles_result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == request.asset_id)
        .where(Candle.interval == request.interval)
        .where(Candle.close_time <= request.candle_close_time)
        .order_by(Candle.open_time.desc())
        .limit(400)
    )
    candles = list(candles_result.scalars().all())
    candles.reverse()

    latest_close_utc = _utc(request.candle_close_time)
    is_stale = (datetime.now(timezone.utc) - latest_close_utc).total_seconds() > (_STALE_CANDLE_MAX_MINUTES * 60)
    is_incomplete = latest_close_utc > datetime.now(timezone.utc)

    completed: list[str] = []
    failed: list[dict[str, str]] = []
    buy_count = 0
    sell_count = 0
    hold_count = 0

    for slug in ENABLED_PHASE1_ROSTER:
        proposal_started = datetime.now(timezone.utc)
        strategy_row = strategy_rows.get(slug)
        strategy_impl = None
        params: dict[str, object] = {}
        try:
            strategy_impl = strategy_registry.get(slug)
            params = dict(strategy_impl.default_params)
        except StrategyLookupError:
            strategy_impl = None
        parameter_set_id: uuid.UUID | None = None
        if strategy_row is not None and strategy_impl is not None:
            parameter_set = await db.scalar(
                select(ParameterSet)
                .where(ParameterSet.strategy_id == strategy_row.id)
                .order_by(ParameterSet.created_at.desc())
                .limit(1)
            )
            if parameter_set is not None:
                params.update(parameter_set.params)
                parameter_set_id = parameter_set.id

        if strategy_row is None:
            strategy_identity = f"{slug}@unknown"
        else:
            strategy_identity = build_strategy_identity(slug=slug, module_version=strategy_row.module_version)
        parameter_set_identity = str(parameter_set_id) if parameter_set_id is not None else "default"

        minimum_history = minimum_history_required(slug=slug, params=params)
        history_count = len(candles)
        indicator_values: dict[str, object] = {}
        explanation_codes: list[str] = []
        action = "HOLD"
        evaluation_status = "EVALUATED"
        reason = "Evaluation completed."
        strength: Decimal | None = None

        if strategy_row is None:
            evaluation_status = "FAILED"
            reason = "Strategy row not found."
            explanation_codes = ["CHECK_FAILED:strategy_row_missing"]
        elif strategy_impl is None:
            evaluation_status = "FAILED"
            reason = "Strategy implementation not registered."
            explanation_codes = ["CHECK_FAILED:strategy_not_registered"]
        elif is_incomplete:
            evaluation_status = "FAILED"
            reason = "Current candle is incomplete."
            explanation_codes = ["CHECK_FAILED:incomplete_candle"]
        elif is_stale:
            evaluation_status = "FAILED"
            reason = "Candle is stale for roster evaluation."
            explanation_codes = ["CHECK_FAILED:stale_candle"]
        elif history_count < minimum_history:
            evaluation_status = "INSUFFICIENT_CONTEXT"
            reason = "Insufficient candle history."
            explanation_codes = ["CHECK_FAILED:insufficient_candle_history"]
        else:
            strategy_context_candles = [
                {
                    "open_time": row.open_time,
                    "close_time": row.close_time,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in candles
            ]
            try:
                signal = strategy_impl.generate_signal(
                    context=StrategyContext(
                        candles=strategy_context_candles,
                        asset_metadata={
                            "asset_id": str(request.asset_id),
                            "symbol": request.product_id,
                            "asset_class": "crypto",
                        },
                        interval=request.interval,
                        current_position=None,
                        strategy_parameters=params,
                    )
                )
                action = _uppercase_action(signal.action)
                strength = signal.strength
                reason = signal.reason
                indicator_values = dict(signal.indicators)
                explanation_codes = [
                    "CHECK_PASSED:strategy_evaluated",
                    f"CHECK_INFO:signal_action={action}",
                ]
            except Exception as exc:
                evaluation_status = "FAILED"
                action = "HOLD"
                reason = f"Strategy evaluation failed: {exc.__class__.__name__}"
                explanation_codes = ["CHECK_FAILED:strategy_evaluation_exception"]

        proposal = StrategyRosterProposal(
            idempotency_key=_build_proposal_idempotency_key(
                request=request,
                strategy_identity=strategy_identity,
                parameter_set_identity=parameter_set_identity,
            ),
            roster_run_id=run.roster_run_id,
            asset_id=request.asset_id,
            provider=request.provider,
            product_id=request.product_id,
            interval=request.interval,
            candle_open_time=request.candle_open_time,
            candle_close_time=request.candle_close_time,
            strategy_id=None if strategy_row is None else strategy_row.id,
            strategy_slug=slug,
            strategy_version=strategy_identity.split("@", 1)[1] if "@" in strategy_identity else "unknown",
            strategy_identity=strategy_identity,
            parameter_set_id=parameter_set_id,
            parameter_set_identity=parameter_set_identity,
            scheduled_cycle_id=request.scheduled_cycle_id,
            evaluated_at=datetime.now(timezone.utc),
            action=action,
            evaluation_status=evaluation_status,
            strength=strength,
            confidence=None,
            deterministic_explanation=explanation_codes,
            reason=reason,
            indicator_values=indicator_values,
            market_window_evidence={
                "history_candle_count": history_count,
                "minimum_history_required": minimum_history,
                "latest_completed_candle_open": request.candle_open_time.isoformat(),
                "latest_completed_candle_close": request.candle_close_time.isoformat(),
                "current_incomplete_candle_excluded": True,
            },
            minimum_history_required=minimum_history,
            history_candle_count=history_count,
            current_incomplete_candle_excluded=True,
            execution_mode="SHADOW",
            live_submission_allowed=False,
        )
        db.add(proposal)

        if evaluation_status == "FAILED":
            failed.append({"strategy_slug": slug, "reason": reason})
            logger.warning(
                "strategy_proposal_failed roster_run_id=%s strategy_slug=%s candle_close=%s reason=%s duration_ms=%s execution_mode=shadow live_submission=false",
                run.roster_run_id,
                slug,
                request.candle_close_time.isoformat(),
                reason,
                int((datetime.now(timezone.utc) - proposal_started).total_seconds() * 1000),
            )
        else:
            completed.append(slug)
            if action == "BUY":
                buy_count += 1
            elif action == "SELL":
                sell_count += 1
            else:
                hold_count += 1
            logger.info(
                "strategy_proposal_completed roster_run_id=%s strategy_slug=%s strategy_identity=%s action=%s duration_ms=%s execution_mode=shadow live_submission=false",
                run.roster_run_id,
                slug,
                strategy_identity,
                action,
                int((datetime.now(timezone.utc) - proposal_started).total_seconds() * 1000),
            )

    run_completed_at = datetime.now(timezone.utc)
    run.strategies_completed = completed
    run.strategies_failed = failed
    run.strategies_completed_count = len(completed)
    run.strategies_failed_count = len(failed)
    run.buy_count = buy_count
    run.sell_count = sell_count
    run.hold_count = hold_count
    run.error_summary = {"failed": failed}
    run.completed_at = run_completed_at

    await db.commit()

    logger.info(
        "strategy_roster_completed roster_run_id=%s asset_id=%s candle_close=%s requested=%s completed=%s failed=%s buy=%s sell=%s hold=%s execution_mode=shadow live_submission=false",
        run.roster_run_id,
        request.asset_id,
        request.candle_close_time.isoformat(),
        run.strategies_requested_count,
        run.strategies_completed_count,
        run.strategies_failed_count,
        run.buy_count,
        run.sell_count,
        run.hold_count,
    )

    return StrategyRosterRunResult(
        roster_run_id=run.roster_run_id,
        replayed=False,
        strategies_requested_count=run.strategies_requested_count,
        strategies_completed_count=run.strategies_completed_count,
        strategies_failed_count=run.strategies_failed_count,
        buy_count=run.buy_count,
        sell_count=run.sell_count,
        hold_count=run.hold_count,
    )


async def fetch_latest_roster_run_with_proposals(
    *,
    db: AsyncSession,
    provider: str,
    product_id: str,
    interval: str,
) -> tuple[StrategyRosterRun | None, list[StrategyRosterProposal]]:
    run = await db.scalar(
        select(StrategyRosterRun)
        .where(StrategyRosterRun.provider == provider)
        .where(StrategyRosterRun.product_id == product_id)
        .where(StrategyRosterRun.interval == interval)
        .order_by(StrategyRosterRun.candle_close_time.desc(), StrategyRosterRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return None, []

    proposals_result = await db.execute(
        select(StrategyRosterProposal)
        .where(StrategyRosterProposal.roster_run_id == run.roster_run_id)
        .order_by(StrategyRosterProposal.strategy_slug.asc())
    )
    proposals = list(proposals_result.scalars().all())
    return run, proposals
