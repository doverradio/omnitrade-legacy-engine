from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.signal import Signal as SignalModel
from app.models.strategy import Strategy as StrategyModel
from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.data.binance_client import BinanceUSClient
from app.services.data.http_client import AsyncHTTPClient
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.decisions.ingestion import build_signal_idempotency_key
from app.services.decisions.package import DecisionPackageBuilder
from app.services.data.worker_entrypoint import run_ingestion_cycle
from app.services.decisions.ingestion import ingest_decision_records
from app.services.replay.default_agent import ReplayPackageNotFoundError, replay_decision_package_v0
from app.services.replay.identifiers import build_decision_package_id
from app.services.signals.execution_orchestrator import SignalExecutionRequest, orchestrate_paper_signal_execution
from app.services.strategies import StrategyContext, strategy_registry
from app.services.strategies.registry import StrategyLookupError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    poll_interval_seconds: int
    candle_interval: str
    candle_lookback_limit: int
    default_order_quantity: Decimal

    @staticmethod
    def from_env() -> "WorkerConfig":
        poll_interval = int(os.getenv("ORCHESTRATION_POLL_INTERVAL_SECONDS", "300"))
        candle_interval = os.getenv("ORCHESTRATION_CANDLE_INTERVAL", "1m")
        lookback_limit = int(os.getenv("ORCHESTRATION_CANDLE_LOOKBACK_LIMIT", "120"))
        default_quantity = Decimal(os.getenv("ORCHESTRATION_DEFAULT_ORDER_QUANTITY", "1"))

        if poll_interval <= 0:
            raise ValueError("ORCHESTRATION_POLL_INTERVAL_SECONDS must be > 0")
        if lookback_limit <= 1:
            raise ValueError("ORCHESTRATION_CANDLE_LOOKBACK_LIMIT must be > 1")
        if default_quantity <= 0:
            raise ValueError("ORCHESTRATION_DEFAULT_ORDER_QUANTITY must be > 0")

        return WorkerConfig(
            poll_interval_seconds=poll_interval,
            candle_interval=candle_interval,
            candle_lookback_limit=lookback_limit,
            default_order_quantity=default_quantity,
        )


@dataclass(frozen=True, slots=True)
class CycleStats:
    ingestion_assets_ok: int
    signals_created: int
    execution_candidates: int
    executions_attempted: int
    executions_skipped: int
    decisions_inserted: int


async def _load_active_assets(db: AsyncSession) -> list[Asset]:
    result = await db.execute(
        select(Asset)
        .where(Asset.is_active.is_(True))
        .order_by(Asset.asset_class.asc(), Asset.symbol.asc())
    )
    return list(result.scalars().all())


async def _load_active_strategies(db: AsyncSession) -> list[StrategyModel]:
    result = await db.execute(
        select(StrategyModel)
        .where(StrategyModel.is_active.is_(True))
        .order_by(StrategyModel.created_at.asc())
    )
    return list(result.scalars().all())


async def _load_latest_parameter_set(db: AsyncSession, *, strategy_id: uuid.UUID) -> ParameterSet | None:
    result = await db.execute(
        select(ParameterSet)
        .where(ParameterSet.strategy_id == strategy_id)
        .order_by(ParameterSet.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _load_latest_candles(
    db: AsyncSession,
    *,
    asset_id: uuid.UUID,
    interval: str,
    limit: int,
) -> list[Candle]:
    result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .order_by(Candle.open_time.desc())
        .limit(limit)
    )
    candles = list(result.scalars().all())
    candles.reverse()
    return candles


async def _load_primary_account_by_asset_class(db: AsyncSession, *, asset_class: str) -> PaperAccount | None:
    result = await db.execute(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .where(PaperAccount.asset_class == asset_class)
        .order_by(PaperAccount.created_at.asc())
        .limit(1)
    )
    return result.scalars().first()


async def _signal_exists(
    db: AsyncSession,
    *,
    strategy_id: uuid.UUID,
    parameter_set_id: uuid.UUID,
    asset_id: uuid.UUID,
    signal_time: datetime,
) -> bool:
    result = await db.execute(
        select(SignalModel.id)
        .where(SignalModel.strategy_id == strategy_id)
        .where(SignalModel.parameter_set_id == parameter_set_id)
        .where(SignalModel.asset_id == asset_id)
        .where(SignalModel.signal_time == signal_time)
        .limit(1)
    )
    return result.scalars().first() is not None


async def _load_decision_record_for_signal(
    *,
    db: AsyncSession,
    signal_id: uuid.UUID,
) -> DecisionRecord | None:
    idempotency_key = build_signal_idempotency_key(signal_id)
    result = await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.idempotency_key == idempotency_key)
        .limit(1)
    )
    return result.scalars().first()


async def _produce_research_evidence(
    *,
    db: AsyncSession,
    decision_package_builder: DecisionPackageBuilder,
    decision_record: DecisionRecord,
) -> None:
    package = await decision_package_builder.build_decision_package(db=db, decision_id=decision_record.decision_id)
    if package is None:
        return

    decision_package_id = build_decision_package_id(
        decision_id=package.decision_id,
        package_hash=package.content_hash,
        package_version=package.schema_version,
    )

    try:
        replay_result = await replay_decision_package_v0(db=db, decision_package_id=decision_package_id)
    except ReplayPackageNotFoundError:
        return

    quality_result = evaluate_replay_result_v0(replay_result=replay_result)
    _ = evaluate_decision_quality_v0(decision_quality_result=quality_result)


def _to_strategy_context(
    *,
    candles: list[Candle],
    asset: Asset,
    interval: str,
    strategy_params: dict,
) -> StrategyContext:
    candle_dicts = [
        {
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "timestamp": candle.open_time,
        }
        for candle in candles
    ]

    return StrategyContext(
        candles=candle_dicts,
        asset_metadata={
            "asset_id": str(asset.id),
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
        },
        interval=interval,
        current_position=None,
        strategy_parameters=dict(strategy_params),
    )


def _signal_status_from_execution_status(execution_status: str) -> str:
    if execution_status in {"executed", "duplicate"}:
        return "executed"
    if execution_status == "rejected":
        return "risk_rejected"
    if execution_status == "pending":
        return "risk_approved"
    return "generated"


async def run_orchestration_cycle(
    db: AsyncSession,
    *,
    client: BinanceUSClient,
    config: WorkerConfig,
) -> CycleStats:
    ingestion_result = await run_ingestion_cycle(
        db,
        client,
        interval=config.candle_interval,
    )

    assets = await _load_active_assets(db)
    strategies = await _load_active_strategies(db)

    signals_created = 0
    execution_candidates = 0
    executions_attempted = 0
    executions_skipped = 0
    decision_inserted_total = 0
    decision_package_builder = DecisionPackageBuilder()

    for strategy_row in strategies:
        if not getattr(strategy_row, "is_active", True):
            logger.info(
                "paper_execution_skip reason=disabled_strategy strategy_id=%s strategy_slug=%s",
                strategy_row.id,
                strategy_row.slug,
            )
            continue

        try:
            strategy_impl = strategy_registry.get(strategy_row.slug)
        except StrategyLookupError:
            logger.info(
                "paper_execution_skip reason=unregistered_strategy strategy_id=%s strategy_slug=%s",
                strategy_row.id,
                strategy_row.slug,
            )
            logger.warning("Skipping unregistered strategy slug=%s", strategy_row.slug)
            continue

        parameter_set = await _load_latest_parameter_set(db, strategy_id=strategy_row.id)
        if parameter_set is None:
            logger.info(
                "paper_execution_skip reason=missing_parameter_set strategy_id=%s strategy_slug=%s",
                strategy_row.id,
                strategy_row.slug,
            )
            logger.warning("Skipping strategy without parameter_set strategy_id=%s slug=%s", strategy_row.id, strategy_row.slug)
            continue

        for asset in assets:
            candles = await _load_latest_candles(
                db,
                asset_id=asset.id,
                interval=config.candle_interval,
                limit=config.candle_lookback_limit,
            )
            if len(candles) < 2:
                logger.info(
                    "paper_execution_skip reason=insufficient_candles strategy_id=%s asset_id=%s candle_count=%s minimum_required=%s",
                    strategy_row.id,
                    asset.id,
                    len(candles),
                    2,
                )
                continue

            signal_time = candles[-1].open_time
            exists = await _signal_exists(
                db,
                strategy_id=strategy_row.id,
                parameter_set_id=parameter_set.id,
                asset_id=asset.id,
                signal_time=signal_time,
            )
            if exists:
                logger.info(
                    "paper_execution_skip reason=duplicate_existing_signal strategy_id=%s parameter_set_id=%s asset_id=%s signal_time=%s",
                    strategy_row.id,
                    parameter_set.id,
                    asset.id,
                    signal_time.isoformat(),
                )
                continue

            context = _to_strategy_context(
                candles=candles,
                asset=asset,
                interval=config.candle_interval,
                strategy_params=parameter_set.params,
            )
            generated = strategy_impl.generate_signal(context)

            signal_model = SignalModel(
                strategy_id=strategy_row.id,
                parameter_set_id=parameter_set.id,
                asset_id=asset.id,
                signal_time=signal_time,
                action=generated.action,
                raw_strength=generated.strength,
                ai_confidence=generated.strength,
                regime_tag=None,
                status="generated",
            )
            db.add(signal_model)
            await db.flush()

            signals_created += 1

            if generated.action in {"buy", "sell"}:
                execution_candidates += 1
                account = await _load_primary_account_by_asset_class(db, asset_class=asset.asset_class)
                if account is not None:
                    executions_attempted += 1
                    execution = await orchestrate_paper_signal_execution(
                        db=db,
                        request=SignalExecutionRequest(
                            signal_id=signal_model.id,
                            paper_account_id=account.id,
                            asset_id=asset.id,
                            side=generated.action,
                            quantity=config.default_order_quantity,
                            actor="orchestration_worker",
                        ),
                    )
                    signal_model.status = _signal_status_from_execution_status(execution.execution_status)
                else:
                    executions_skipped += 1
                    logger.info(
                        "paper_execution_skip reason=no_active_paper_account signal_id=%s action=%s status=%s account_id=%s",
                        signal_model.id,
                        generated.action,
                        signal_model.status,
                        None,
                    )
                    logger.warning(
                        "No active paper account for asset_class=%s asset=%s; signal persisted without execution",
                        asset.asset_class,
                        asset.symbol,
                    )
            else:
                executions_skipped += 1
                logger.info(
                    "paper_execution_skip reason=non_actionable_action signal_id=%s action=%s status=%s account_id=%s",
                    signal_model.id,
                    generated.action,
                    signal_model.status,
                    None,
                )

            decision_result = await ingest_decision_records(db=db, signal_ids=[signal_model.id])
            decision_inserted_total += decision_result.inserted_records

            decision_record = await _load_decision_record_for_signal(db=db, signal_id=signal_model.id)
            if decision_record is not None:
                await _produce_research_evidence(
                    db=db,
                    decision_package_builder=decision_package_builder,
                    decision_record=decision_record,
                )

            await db.commit()

    return CycleStats(
        ingestion_assets_ok=ingestion_result.successful_assets,
        signals_created=signals_created,
        execution_candidates=execution_candidates,
        executions_attempted=executions_attempted,
        executions_skipped=executions_skipped,
        decisions_inserted=decision_inserted_total,
    )


async def run_forever() -> None:
    setup_logging()
    config = WorkerConfig.from_env()

    logger.info(
        "Starting continuous pipeline worker poll_interval_seconds=%s candle_interval=%s candle_lookback_limit=%s default_order_quantity=%s",
        config.poll_interval_seconds,
        config.candle_interval,
        config.candle_lookback_limit,
        config.default_order_quantity,
    )

    async with AsyncHTTPClient() as http_client:
        client = BinanceUSClient(http_client)

        while True:
            try:
                async with AsyncSessionLocal() as db:
                    stats = await run_orchestration_cycle(db, client=client, config=config)

                logger.info(
                    "Pipeline cycle completed ingestion_assets_ok=%s signals_created=%s execution_candidates=%s executions_attempted=%s executions_skipped=%s decisions_inserted=%s",
                    stats.ingestion_assets_ok,
                    stats.signals_created,
                    stats.execution_candidates,
                    stats.executions_attempted,
                    stats.executions_skipped,
                    stats.decisions_inserted,
                )
            except Exception:
                logger.exception("Pipeline orchestration cycle failed")

            await asyncio.sleep(config.poll_interval_seconds)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
