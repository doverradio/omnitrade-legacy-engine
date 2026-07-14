from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal, dispose_database_engine, is_retryable_db_connection_error
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.signal import Signal as SignalModel
from app.models.strategy import Strategy as StrategyModel
from app.models.validation_run import ValidationRun
from app.models.validation_run_event import ValidationRunEvent
from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.data.binance_client import BinanceUSClient
from app.services.data.http_client import AsyncHTTPClient
from app.services.data.kraken_client import KrakenSpotClient
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.decisions.ingestion import build_signal_idempotency_key
from app.services.decisions.package import DecisionPackageBuilder
from app.services.data.worker_entrypoint import run_ingestion_cycle
from app.services.decisions.ingestion import ingest_decision_records
from app.services.replay.default_agent import ReplayPackageNotFoundError, replay_decision_package_v0
from app.services.replay.identifiers import build_decision_package_id
from app.services.research_activation import run_deterministic_research_cycle_if_due
from app.services.signals.execution_orchestrator import SignalExecutionRequest, orchestrate_paper_signal_execution
from app.services.system_intelligence_snapshots import capture_system_intelligence_snapshot_if_due
from app.services.strategies import StrategyContext, strategy_registry
from app.services.strategies.registry import StrategyLookupError
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.orchestration.venue_commissioning_bridge import service as venue_commissioning_service
from app.services.strategy_outcomes import score_due_strategy_roster_proposal_outcomes
from app.services.strategy_roster import StrategyRosterRequest, run_strategy_roster_for_candle

logger = logging.getLogger(__name__)

_AUTONOMOUS_CYCLE_TRIGGER = "kraken_btc_15m_candle_close"
_AUTONOMOUS_CYCLE_PRODUCT_ID = "BTC-USD"
_AUTONOMOUS_CYCLE_INTERVAL = "15m"
_AUTONOMOUS_CYCLE_PROVIDER = "kraken_spot"
_AUTONOMOUS_CYCLE_ASSET_SYMBOLS = ("BTC", "XBT", "XXBT")

_RESEARCH_STATUS_EVENT_TYPES = {
    "disabled": "RESEARCH_CYCLE_DISABLED",
    "skipped": "RESEARCH_CYCLE_SKIPPED",
    "successful": "RESEARCH_CYCLE_SUCCEEDED",
    "failed": "RESEARCH_CYCLE_FAILED",
}

_RESEARCH_STATUS_SEVERITIES = {
    "disabled": "yellow",
    "skipped": "blue",
    "successful": "green",
    "failed": "red",
}


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
    executions_rejected: int
    executions_failed: int
    executions_skipped: int
    decisions_inserted: int
    research_cycles_started: int
    intelligence_snapshots_captured: int


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


async def _load_single_active_kraken_mandate(db: AsyncSession) -> AutonomousCapitalMandate | None:
    if not hasattr(db, "execute"):
        return None

    result = await db.execute(
        select(AutonomousCapitalMandate)
        .where(AutonomousCapitalMandate.status == "ACTIVE")
        .where(AutonomousCapitalMandate.provider == _AUTONOMOUS_CYCLE_PROVIDER)
        .order_by(AutonomousCapitalMandate.updated_at.desc())
        .limit(2)
    )
    mandates = list(result.scalars().all())
    if not mandates:
        logger.info("autonomous_cycle_skip reason=no_active_kraken_mandate")
        return None
    if len(mandates) > 1:
        logger.warning("autonomous_cycle_skip reason=ambiguous_active_kraken_mandates mandate_count=%s", len(mandates))
        return None
    return mandates[0]


async def _load_latest_kraken_btc_15m_candle(db: AsyncSession) -> Candle | None:
    if not hasattr(db, "execute"):
        return None

    asset_result = await db.execute(
        select(Asset)
        .where(Asset.is_active.is_(True))
        .where(Asset.asset_class == "crypto")
        .where(Asset.exchange == _AUTONOMOUS_CYCLE_PROVIDER)
        .where(Asset.symbol.in_(_AUTONOMOUS_CYCLE_ASSET_SYMBOLS))
        .order_by(Asset.created_at.desc())
        .limit(2)
    )
    assets = list(asset_result.scalars().all())
    if not assets:
        logger.info("autonomous_cycle_skip reason=kraken_btc_asset_missing")
        return None
    if len(assets) > 1:
        logger.warning("autonomous_cycle_skip reason=ambiguous_kraken_btc_assets asset_count=%s", len(assets))
        return None

    candle_result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == assets[0].id)
        .where(Candle.interval == _AUTONOMOUS_CYCLE_INTERVAL)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    candle = candle_result.scalars().first()
    if candle is None:
        logger.info("autonomous_cycle_skip reason=kraken_btc_15m_candle_missing")
    return candle


def _build_kraken_btc_candle_idempotency_seed(*, candle: Candle) -> str:
    close_time = candle.close_time
    close_time_utc = close_time if close_time.tzinfo is not None else close_time.replace(tzinfo=timezone.utc)
    return f"kraken-btc-15m-close:{close_time_utc.astimezone(timezone.utc).isoformat()}"


async def _run_kraken_btc_autonomous_cycle_if_due(*, db: AsyncSession) -> tuple[uuid.UUID | None, Candle | None]:
    mandate = await _load_single_active_kraken_mandate(db)
    if mandate is None:
        return None, None

    latest_candle = await _load_latest_kraken_btc_15m_candle(db)
    if latest_candle is None:
        return None, None

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(
            mandate_id=mandate.mandate_id,
            actor="orchestration_worker",
            product_id=_AUTONOMOUS_CYCLE_PRODUCT_ID,
            strategy_interval=_AUTONOMOUS_CYCLE_INTERVAL,
            trigger=_AUTONOMOUS_CYCLE_TRIGGER,
            idempotency_seed=_build_kraken_btc_candle_idempotency_seed(candle=latest_candle),
        ),
    )
    logger.info(
        "autonomous_cycle_triggered trigger=%s mandate_id=%s cycle_id=%s state=%s replayed=%s idempotency_key=%s",
        _AUTONOMOUS_CYCLE_TRIGGER,
        mandate.mandate_id,
        result.cycle_id,
        result.state,
        result.replayed,
        result.idempotency_key,
    )
    return result.cycle_id, latest_candle


async def _load_active_validation_run_ids(*, db: AsyncSession) -> list[uuid.UUID]:
    if not hasattr(db, "execute"):
        return []

    result = await db.execute(
        select(ValidationRun.validation_run_id)
        .where(ValidationRun.status == "RUNNING")
        .order_by(ValidationRun.started_at.asc(), ValidationRun.validation_run_id.asc())
    )
    return list(result.scalars().all())


async def _emit_execution_rejection_event(
    *,
    db: AsyncSession,
    signal_id: uuid.UUID,
    decision_record_id: uuid.UUID | None,
    asset: Asset,
    side: str,
    requested_quantity: Decimal,
    execution_reason_code: str,
    execution_reason_text: str,
    execution_available_quantity: str | None,
) -> None:
    validation_run_ids = await _load_active_validation_run_ids(db=db)
    if not validation_run_ids:
        return

    event_payload = {
        "severity": "yellow",
        "title": "Paper Execution Rejected",
        "description": execution_reason_text,
        "metadata": {
            "signal_id": str(signal_id),
            "decision_record_id": str(decision_record_id) if decision_record_id is not None else None,
            "asset_id": str(asset.id),
            "symbol": asset.symbol,
            "side": side,
            "requested_quantity": format(requested_quantity, "f"),
            "available_quantity": execution_available_quantity,
            "reason_code": execution_reason_code,
            "reason_text": execution_reason_text,
            "validation_run_ids": [str(item) for item in validation_run_ids],
            "timestamp": datetime.now().astimezone().isoformat(),
        },
    }
    for validation_run_id in validation_run_ids:
        db.add(
            ValidationRunEvent(
                validation_run_id=validation_run_id,
                event_type="PAPER_EXECUTION_REJECTED",
                message=execution_reason_text,
                payload=event_payload,
            )
        )


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


def _safe_research_failure_reason(exc: Exception) -> str:
    return f"research_cycle_exception:{exc.__class__.__name__}"


async def _rollback_active_session(*, db: AsyncSession) -> None:
    if not hasattr(db, "rollback"):
        return
    await db.rollback()
    if hasattr(db, "failed_transaction"):
        setattr(db, "failed_transaction", False)
    if hasattr(db, "pending"):
        db.pending.clear()


async def _record_research_cycle_status(
    *,
    db: AsyncSession,
    status: str,
    reason: str | None,
    campaign_id: uuid.UUID | None,
    candidates_generated: int,
    candidates_evaluated: int,
    descendants_generated: int,
    champion: str | None,
    error_type: str | None = None,
) -> None:
    recorded_at = datetime.now().astimezone()
    after_state = {
        "status": status,
        "reason": reason,
        "campaign_id": str(campaign_id) if campaign_id is not None else None,
        "candidates_generated": candidates_generated,
        "candidates_evaluated": candidates_evaluated,
        "descendants_generated": descendants_generated,
        "champion": champion,
        "error_type": error_type,
        "recorded_at": recorded_at.isoformat(),
    }
    db.add(
        AuditLog(
            actor="orchestration_worker",
            action=f"research_cycle_{status}",
            entity_type="research_cycle",
            entity_id=campaign_id,
            before_state=None,
            after_state=after_state,
        )
    )

    validation_run_ids = await _load_active_validation_run_ids(db=db)
    if not validation_run_ids:
        return

    event_type = _RESEARCH_STATUS_EVENT_TYPES[status]
    event_payload = {
        "severity": _RESEARCH_STATUS_SEVERITIES[status],
        "title": f"Research Cycle {status.title()}",
        "description": reason or f"Research cycle {status}.",
        "metadata": after_state,
    }
    for validation_run_id in validation_run_ids:
        db.add(
            ValidationRunEvent(
                validation_run_id=validation_run_id,
                event_type=event_type,
                message=str(event_payload["description"]),
                payload=event_payload,
            )
        )


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
    kraken_client: KrakenSpotClient | None = None,
    config: WorkerConfig,
) -> CycleStats:
    ingestion_result = await run_ingestion_cycle(
        db,
        client,
        kraken_client,
        interval=config.candle_interval,
    )

    if hasattr(db, "scalars") and hasattr(db, "scalar"):
        try:
            resumed_runs = await venue_commissioning_service["resume_runs"](
                db=db,
                actor="orchestration_worker",
                limit=10,
            )
            if resumed_runs > 0:
                logger.info("venue_commission_resume_completed resumed_runs=%s", resumed_runs)
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("venue_commission_resume_failed")

    autonomous_cycle_id: uuid.UUID | None = None
    kraken_btc_candle: Candle | None = None
    try:
        autonomous_cycle_id, kraken_btc_candle = await _run_kraken_btc_autonomous_cycle_if_due(db=db)
    except Exception:
        await _rollback_active_session(db=db)
        logger.exception("autonomous_cycle_failed trigger=%s", _AUTONOMOUS_CYCLE_TRIGGER)

    try:
        if kraken_btc_candle is None:
            kraken_btc_candle = await _load_latest_kraken_btc_15m_candle(db)
        if kraken_btc_candle is not None:
            await run_strategy_roster_for_candle(
                db=db,
                request=StrategyRosterRequest(
                    asset_id=kraken_btc_candle.asset_id,
                    provider=_AUTONOMOUS_CYCLE_PROVIDER,
                    product_id=_AUTONOMOUS_CYCLE_PRODUCT_ID,
                    interval=_AUTONOMOUS_CYCLE_INTERVAL,
                    candle_open_time=kraken_btc_candle.open_time,
                    candle_close_time=kraken_btc_candle.close_time,
                    trigger=_AUTONOMOUS_CYCLE_TRIGGER,
                    scheduled_cycle_id=autonomous_cycle_id,
                ),
            )
    except Exception:
        await _rollback_active_session(db=db)
        logger.exception(
            "strategy_roster_failed trigger=%s provider=%s product_id=%s interval=%s",
            _AUTONOMOUS_CYCLE_TRIGGER,
            _AUTONOMOUS_CYCLE_PROVIDER,
            _AUTONOMOUS_CYCLE_PRODUCT_ID,
            _AUTONOMOUS_CYCLE_INTERVAL,
        )

    if all(hasattr(db, attr) for attr in ("execute", "scalar", "commit")):
        try:
            outcome_result = await score_due_strategy_roster_proposal_outcomes(db=db)
            logger.info(
                "strategy_outcome_scoring_completed scanned=%s inserted=%s skipped_not_due=%s skipped_existing=%s skipped_missing_prices=%s execution_mode=shadow live_submission=false",
                outcome_result.scanned_proposals,
                outcome_result.inserted_outcomes,
                outcome_result.skipped_not_due,
                outcome_result.skipped_existing,
                outcome_result.skipped_missing_prices,
            )
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("strategy_outcome_scoring_failed trigger=%s", _AUTONOMOUS_CYCLE_TRIGGER)

    assets = await _load_active_assets(db)
    strategies = await _load_active_strategies(db)

    signals_created = 0
    execution_candidates = 0
    executions_attempted = 0
    executions_rejected = 0
    executions_failed = 0
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
            account = None
            execution = None
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
                    try:
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
                    except Exception:
                        executions_failed += 1
                        logger.exception(
                            "paper_execution_failed signal_id=%s asset_id=%s strategy_id=%s action=%s",
                            signal_model.id,
                            asset.id,
                            strategy_row.id,
                            generated.action,
                        )
                        db.add(
                            AuditLog(
                                actor="orchestration_worker",
                                action="orchestration_candidate_failed",
                                entity_type="signal",
                                entity_id=signal_model.id,
                                before_state={
                                    "strategy_id": str(strategy_row.id),
                                    "asset_id": str(asset.id),
                                    "side": generated.action,
                                },
                                after_state={
                                    "outcome": "FAILED",
                                },
                            )
                        )
                    else:
                        signal_model.status = _signal_status_from_execution_status(execution.execution_status)
                        execution_outcome = getattr(
                            execution,
                            "outcome",
                            "REJECTED"
                            if execution.execution_status == "rejected"
                            else "SKIPPED"
                            if execution.execution_status == "duplicate"
                            else "EXECUTED"
                            if execution.execution_status in {"executed", "pending"}
                            else "FAILED",
                        )
                        if execution_outcome == "REJECTED":
                            executions_rejected += 1
                        elif execution_outcome == "SKIPPED":
                            executions_skipped += 1
                        elif execution_outcome == "FAILED":
                            executions_failed += 1
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
                if (
                    account is not None
                    and execution is not None
                    and getattr(execution, "outcome", "REJECTED" if execution.execution_status == "rejected" else None) == "REJECTED"
                    and getattr(execution, "reason_code", None) is not None
                    and getattr(execution, "reason_text", None) is not None
                ):
                    await _emit_execution_rejection_event(
                        db=db,
                        signal_id=signal_model.id,
                        decision_record_id=decision_record.decision_id,
                        asset=asset,
                        side=generated.action,
                        requested_quantity=config.default_order_quantity,
                        execution_reason_code=execution.reason_code,
                        execution_reason_text=execution.reason_text,
                        execution_available_quantity=(
                            None
                            if getattr(execution, "reason_details", None) is None
                            else str(
                                execution.reason_details.get("held_quantity")
                                or execution.reason_details.get("cash_balance")
                            )
                        ),
                    )
                await _produce_research_evidence(
                    db=db,
                    decision_package_builder=decision_package_builder,
                    decision_record=decision_record,
                )

            await db.commit()

    research_cycles_started = 0
    try:
        research_cycle_result = await run_deterministic_research_cycle_if_due(db=db)
    except Exception as exc:
        failure_reason = _safe_research_failure_reason(exc)
        await _rollback_active_session(db=db)
        try:
            await _record_research_cycle_status(
                db=db,
                status="failed",
                reason=failure_reason,
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
                error_type=exc.__class__.__name__,
            )
        except Exception:
            await _rollback_active_session(db=db)
            await _record_research_cycle_status(
                db=db,
                status="failed",
                reason=failure_reason,
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
                error_type=exc.__class__.__name__,
            )
        await db.commit()
        logger.exception("Deterministic research cycle failed; continuing orchestration cycle without research outputs")
        research_cycle_result = None
    else:
        if research_cycle_result.started:
            await db.commit()
            research_cycles_started = 1
            research_status = "successful"
        elif research_cycle_result.reason == "research_disabled":
            research_status = "disabled"
        else:
            research_status = "skipped"

        await _record_research_cycle_status(
            db=db,
            status=research_status,
            reason=research_cycle_result.reason,
            campaign_id=research_cycle_result.campaign_id,
            candidates_generated=research_cycle_result.candidates_generated,
            candidates_evaluated=research_cycle_result.candidates_evaluated,
            descendants_generated=research_cycle_result.descendants_generated,
            champion=research_cycle_result.champion,
        )
        await db.commit()
        logger.info(
            "research_cycle_check started=%s reason=%s campaign_id=%s candidates_generated=%s candidates_evaluated=%s descendants_generated=%s champion=%s",
            research_cycle_result.started,
            research_cycle_result.reason,
            research_cycle_result.campaign_id,
            research_cycle_result.candidates_generated,
            research_cycle_result.candidates_evaluated,
            research_cycle_result.descendants_generated,
            research_cycle_result.champion,
        )

    snapshot = await capture_system_intelligence_snapshot_if_due(db=db)
    if snapshot is not None:
        await db.commit()

    return CycleStats(
        ingestion_assets_ok=ingestion_result.successful_assets,
        signals_created=signals_created,
        execution_candidates=execution_candidates,
        executions_attempted=executions_attempted,
        executions_rejected=executions_rejected,
        executions_failed=executions_failed,
        executions_skipped=executions_skipped,
        decisions_inserted=decision_inserted_total,
        research_cycles_started=research_cycles_started,
        intelligence_snapshots_captured=1 if snapshot is not None else 0,
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
        kraken_client = KrakenSpotClient(http_client)
        logger.info("kraken_ingestion_client_initialized provider=%s", "kraken_spot")

        while True:
            sleep_seconds = config.poll_interval_seconds
            try:
                async with AsyncSessionLocal() as db:
                    stats = await run_orchestration_cycle(
                        db,
                        client=client,
                        kraken_client=kraken_client,
                        config=config,
                    )

                logger.info(
                    "Pipeline cycle completed ingestion_assets_ok=%s signals_created=%s execution_candidates=%s executions_attempted=%s executions_rejected=%s executions_failed=%s executions_skipped=%s decisions_inserted=%s research_cycles_started=%s intelligence_snapshots_captured=%s",
                    stats.ingestion_assets_ok,
                    stats.signals_created,
                    stats.execution_candidates,
                    stats.executions_attempted,
                    stats.executions_rejected,
                    stats.executions_failed,
                    stats.executions_skipped,
                    stats.decisions_inserted,
                    stats.research_cycles_started,
                    stats.intelligence_snapshots_captured,
                )
            except Exception as exc:
                if is_retryable_db_connection_error(exc):
                    sleep_seconds = min(30, config.poll_interval_seconds)
                    await dispose_database_engine()
                    logger.warning(
                        "Pipeline orchestration worker detected transient database disconnect; retrying next cycle after bounded backoff",
                        exc_info=True,
                    )
                else:
                    logger.exception("Pipeline orchestration cycle failed")

            await asyncio.sleep(sleep_seconds)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
