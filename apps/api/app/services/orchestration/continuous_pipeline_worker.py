from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.capital_campaign import CapitalCampaign
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal, dispose_database_engine, is_retryable_db_connection_error
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.signal import Signal as SignalModel
from app.models.strategy import Strategy as StrategyModel
from app.models.validation_run import ValidationRun
from app.models.validation_run_event import ValidationRunEvent
from app.services.canonical_preview_package import CanonicalPreviewPackageCreateRequest, create_canonical_preview_package
from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.data.binance_client import BinanceUSClient
from app.services.data.http_client import AsyncHTTPClient
from app.services.data.kraken_client import KrakenSpotClient
from app.services.data.ingestion_status import set_last_successful_full_pipeline_at
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.decisions.ingestion import build_signal_idempotency_key
from app.services.decisions.package import DecisionPackageBuilder
from app.services.data.worker_entrypoint import KRAKEN_CANDLE_INTERVAL, run_ingestion_cycle
from app.services.decisions.ingestion import ingest_decision_records
from app.services.replay.default_agent import ReplayPackageNotFoundError, replay_decision_package_v0
from app.services.replay.identifiers import build_decision_package_id
from app.services.research_activation import run_deterministic_research_cycle_if_due
from app.services.signals.execution_orchestrator import SignalExecutionRequest, orchestrate_paper_signal_execution
from app.services.system_intelligence_snapshots import capture_system_intelligence_snapshot_if_due
from app.services.strategies import StrategyContext, strategy_registry
from app.services.strategies.registry import StrategyLookupError
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.capital_campaign_orchestration import run_campaign_orchestration_preview_for_candle
from app.services.mandates.contracts import AUTONOMY_LEVEL_2
from app.services.orchestration.venue_commissioning_bridge import service as venue_commissioning_service
from app.services.orchestration.automatic_package_executor import (
    AutomaticPackageExecutionRequest,
    execute_automatic_ready_package_through_activation,
)
from app.services.strategy_outcomes import score_due_strategy_roster_proposal_outcomes
from app.services.strategy_roster import StrategyRosterRequest, run_strategy_roster_for_candle
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_SLUG

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

_WORKER_BOOT_ACTION = "orchestration_worker_started"
_WORKER_BOOT_FAILED_ACTION = "orchestration_worker_start_failed"
_FULL_PIPELINE_COMPLETE_ACTION = "orchestration_worker_full_pipeline_completed"
_REPLAY_FAILURE_ACTION = "decision_package_replay_failed"

_CANONICAL_READY_PACKAGE_AMOUNT = Decimal("5")
_CANONICAL_READY_PACKAGE_ACTOR = "orchestration_worker:auto_ready_package"
_CANONICAL_READY_STATES = {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}
_ACTIVE_PROVING_STATES = {"ACTIVE"}
_OPEN_LIVE_ORDER_STATES = {"SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED"}
_UNRESOLVED_RECONCILIATION_STATES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}


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


def _resolve_candle_interval_for_asset(*, asset: Asset, config: WorkerConfig) -> str:
    # run_ingestion_cycle (worker_entrypoint.py) always writes Kraken candles
    # at KRAKEN_CANDLE_INTERVAL regardless of the configured
    # ORCHESTRATION_CANDLE_INTERVAL default -- querying a Kraken asset with
    # that default (e.g. "1m") reads a candle interval that is never written,
    # producing a permanent candle_count=0 for that asset.
    if asset.exchange == "kraken_spot":
        return KRAKEN_CANDLE_INTERVAL
    return config.candle_interval


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
        .where(AutonomousCapitalMandate.autonomy_level == AUTONOMY_LEVEL_2)
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


@dataclass(frozen=True, slots=True)
class _KrakenBtcCandleIdentity:
    id: uuid.UUID | None
    asset_id: uuid.UUID
    open_time: datetime
    close_time: datetime


def _capture_kraken_btc_candle_identity(candle: Candle | None) -> _KrakenBtcCandleIdentity | None:
    """Snapshot the primitive fields of a Candle into plain values immediately
    after it is loaded. A rollback triggered by any later, independently
    caught subsystem failure expires every ORM instance tracked by the shared
    session; touching candle.<attr> again after that point forces an implicit
    lazy refresh that raises MissingGreenlet under the async ORM. Primitives
    captured here are immune to that because they are no longer session-bound."""
    if candle is None:
        return None
    return _KrakenBtcCandleIdentity(
        id=getattr(candle, "id", None),
        asset_id=candle.asset_id,
        open_time=candle.open_time,
        close_time=candle.close_time,
    )


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
            candle_id=latest_candle.id,
            candle_close_time=latest_candle.close_time,
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
    try:
        async with AsyncSessionLocal() as evidence_db:
            package = await decision_package_builder.build_decision_package(db=evidence_db, decision_id=decision_record.decision_id)
            if package is None:
                return

            decision_package_id = build_decision_package_id(
                decision_id=package.decision_id,
                package_hash=package.content_hash,
                package_version=package.schema_version,
            )

            replay_result = await replay_decision_package_v0(db=evidence_db, decision_package_id=decision_package_id)
    except ReplayPackageNotFoundError:
        return
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and hasattr(current_task, "cancelling") and current_task.cancelling():
            raise
        logger.exception(
            "decision_package_replay_cancelled decision_id=%s",
            decision_record.decision_id,
        )
        db.add(
            AuditLog(
                actor="orchestration_worker",
                action=_REPLAY_FAILURE_ACTION,
                entity_type="decision_package_replay",
                entity_id=decision_record.decision_id,
                before_state=None,
                after_state={
                    "decision_id": str(decision_record.decision_id),
                    "failure_type": "CancelledError",
                },
            )
        )
        return
    except Exception as exc:
        logger.exception(
            "decision_package_replay_failed decision_id=%s failure_type=%s",
            decision_record.decision_id,
            exc.__class__.__name__,
        )
        db.add(
            AuditLog(
                actor="orchestration_worker",
                action=_REPLAY_FAILURE_ACTION,
                entity_type="decision_package_replay",
                entity_id=decision_record.decision_id,
                before_state=None,
                after_state={
                    "decision_id": str(decision_record.decision_id),
                    "failure_type": exc.__class__.__name__,
                    "failure_reason": str(exc),
                },
            )
        )
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


def _as_utc_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _coerce_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:
        return None


def _build_automatic_ready_package_idempotency_key(
    *,
    campaign_id: uuid.UUID,
    campaign_version: int,
    candle_close_time: str,
    decision_record_id: uuid.UUID,
    proposed_action: str,
    product: str,
    provider: str,
    environment: str,
) -> str:
    payload = {
        "campaign_id": str(campaign_id),
        "campaign_version": int(campaign_version),
        "candle_close_time": candle_close_time,
        "decision_record_id": str(decision_record_id),
        "proposed_action": proposed_action.strip().upper(),
        "product": product.strip().upper(),
        "provider": provider.strip().lower(),
        "environment": environment.strip().lower(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _load_cycle_by_id(*, db: AsyncSession, cycle_id: uuid.UUID) -> AutonomousCycleRun | None:
    return await db.scalar(select(AutonomousCycleRun).where(AutonomousCycleRun.cycle_id == cycle_id).limit(1))


async def _load_runtime_campaign(*, db: AsyncSession, campaign_id: uuid.UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


async def _load_live_trading_profile_for_paper_account(*, db: AsyncSession, paper_account_id: uuid.UUID) -> LiveTradingProfile | None:
    return await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
        .limit(1)
    )


async def _has_active_ready_package_for_opportunity(*, db: AsyncSession, decision_record_id: uuid.UUID) -> bool:
    row = await db.scalar(
        select(CanonicalPreviewPackage.package_id)
        .where(CanonicalPreviewPackage.decision_record_id == decision_record_id)
        .where(CanonicalPreviewPackage.package_state.in_(_CANONICAL_READY_STATES))
        .limit(1)
    )
    return row is not None


async def _has_active_proving_activation(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int, provider: str, environment: str, product: str, now: datetime
) -> bool:
    # activation_state alone is not sufficient: nothing in this codebase ever
    # transitions a CanonicalProvingActivation row to EXPIRED/COMPLETED once
    # its bounded window has elapsed (see canonical_preview_package.py -- only
    # pause/revoke are ever written), so a row can sit at activation_state=
    # 'ACTIVE' in the database indefinitely after its expires_at has passed.
    # The rest of the codebase already treats an activation as usable only
    # when BOTH conditions hold (operator_cli/service.py::_activation_is_active,
    # live_crypto_orders.py's order-submission gate) -- this check must match
    # that same convention, or a long-expired activation from an earlier
    # bounded proving/commissioning run permanently blocks all future
    # automatic ready-package creation for this scope.
    row = await db.scalar(
        select(CanonicalProvingActivation.activation_id)
        .where(CanonicalProvingActivation.campaign_id == campaign_id)
        .where(CanonicalProvingActivation.campaign_version == campaign_version)
        .where(CanonicalProvingActivation.provider == provider)
        .where(CanonicalProvingActivation.environment == environment)
        .where(CanonicalProvingActivation.product == product)
        .where(CanonicalProvingActivation.activation_state.in_(_ACTIVE_PROVING_STATES))
        .where(CanonicalProvingActivation.expires_at > now)
        .limit(1)
    )
    return row is not None


async def _has_open_live_order(*, db: AsyncSession, provider: str, environment: str, product: str) -> bool:
    row = await db.scalar(
        select(LiveCryptoOrder.live_crypto_order_id)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.product_id == product)
        .where(LiveCryptoOrder.status.in_(_OPEN_LIVE_ORDER_STATES))
        .limit(1)
    )
    return row is not None


def _latest_reconciliation_event_per_order(*, provider: str, environment: str, product: str):
    """(order_id, max sequence_number) for every order in scope.

    live_reconciliation_events is append-only (immutable audit log): an
    order accumulates a new row every time it is re-reconciled (e.g.
    partially_filled, then later filled once the provider confirms full
    execution) -- existing rows are never updated or deleted (see the
    before_update/before_delete guards on LiveReconciliationEvent). Only the
    LATEST row per order reflects its current effective state. Confirmed
    production defect: an order whose LiveCryptoOrder.status had already
    reached FILLED was still reported unresolved forever, purely because of
    its own earlier partially_filled/reconciliation_required rows from days
    earlier -- superseded history, not current state. This mirrors the
    "latest per order" rule already applied for the identical
    reconciliation_status vocabulary in
    app.services.risk.equity_evidence._count_reconciliation_uncertainty;
    that existing, correct pattern was simply never applied here too.
    """
    return (
        select(
            LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
            func.max(LiveReconciliationEvent.sequence_number).label("max_seq"),
        )
        .join(LiveCryptoOrder, LiveCryptoOrder.live_crypto_order_id == LiveReconciliationEvent.live_crypto_order_id)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.product_id == product)
        .where(LiveReconciliationEvent.live_crypto_order_id.is_not(None))
        .group_by(LiveReconciliationEvent.live_crypto_order_id)
        .subquery()
    )


async def _log_unresolved_reconciliation_diagnostics(*, db: AsyncSession, provider: str, environment: str, product: str) -> None:
    # Instrumentation only -- mirrors _has_unresolved_reconciliation's own
    # query exactly (same latest-per-order scoping, same unresolved-state
    # set) so this is guaranteed to explain precisely which record(s) that
    # function's boolean check is reacting to, never a different or looser
    # selection. Only called when that check is already about to return True,
    # so it costs nothing on the common (no unresolved reconciliation) path.
    latest = _latest_reconciliation_event_per_order(provider=provider, environment=environment, product=product)
    result = await db.execute(
        select(LiveReconciliationEvent, LiveCryptoOrder)
        .join(
            latest,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest.c.order_id,
                LiveReconciliationEvent.sequence_number == latest.c.max_seq,
            ),
        )
        .join(LiveCryptoOrder, LiveCryptoOrder.live_crypto_order_id == LiveReconciliationEvent.live_crypto_order_id)
        .where(LiveReconciliationEvent.reconciliation_status.in_(_UNRESOLVED_RECONCILIATION_STATES))
        .order_by(LiveReconciliationEvent.recorded_at.asc())
    )
    rows = result.all()
    logger.info(
        "unresolved_reconciliation_gate_triggered provider=%s environment=%s product=%s "
        "matched_record_count=%s unresolved_states=%s",
        provider,
        environment,
        product,
        len(rows),
        ",".join(sorted(_UNRESOLVED_RECONCILIATION_STATES)),
    )
    for reconciliation_event, live_order in rows:
        logger.info(
            "unresolved_reconciliation_record_detail reconciliation_event_id=%s live_crypto_order_id=%s "
            "provider_order_id=%s order_client_order_id=%s order_status=%s order_provider_status=%s "
            "reconciliation_status=%s unresolved_because=status_in_unresolved_set event_type=%s "
            "sequence_number=%s recorded_at=%s provider_recorded_at=%s created_at=%s "
            "order_submitted_at=%s order_acknowledged_at=%s order_filled_at=%s order_cancelled_at=%s",
            reconciliation_event.id,
            reconciliation_event.live_crypto_order_id,
            reconciliation_event.provider_order_id,
            live_order.client_order_id,
            live_order.status,
            live_order.provider_status,
            reconciliation_event.reconciliation_status,
            reconciliation_event.event_type,
            reconciliation_event.sequence_number,
            None if reconciliation_event.recorded_at is None else reconciliation_event.recorded_at.isoformat(),
            None if reconciliation_event.provider_recorded_at is None else reconciliation_event.provider_recorded_at.isoformat(),
            None if reconciliation_event.created_at is None else reconciliation_event.created_at.isoformat(),
            None if live_order.submitted_at is None else live_order.submitted_at.isoformat(),
            None if live_order.acknowledged_at is None else live_order.acknowledged_at.isoformat(),
            None if live_order.filled_at is None else live_order.filled_at.isoformat(),
            None if live_order.cancelled_at is None else live_order.cancelled_at.isoformat(),
        )


async def _has_unresolved_reconciliation(*, db: AsyncSession, provider: str, environment: str, product: str) -> bool:
    latest = _latest_reconciliation_event_per_order(provider=provider, environment=environment, product=product)
    row = await db.scalar(
        select(LiveReconciliationEvent.id)
        .join(
            latest,
            and_(
                LiveReconciliationEvent.live_crypto_order_id == latest.c.order_id,
                LiveReconciliationEvent.sequence_number == latest.c.max_seq,
            ),
        )
        .where(LiveReconciliationEvent.reconciliation_status.in_(_UNRESOLVED_RECONCILIATION_STATES))
        .limit(1)
    )
    if row is not None:
        await _log_unresolved_reconciliation_diagnostics(db=db, provider=provider, environment=environment, product=product)
    return row is not None


async def _attempt_automatic_ready_package_creation(
    *,
    db: AsyncSession,
    orchestration_payload: dict[str, object] | None,
) -> None:
    cycles = [] if not isinstance(orchestration_payload, dict) else list(orchestration_payload.get("cycles") or [])
    for cycle_summary in cycles:
        if not isinstance(cycle_summary, dict):
            continue
        cycle_id_raw = cycle_summary.get("cycle_id")
        if cycle_id_raw is None:
            continue

        cycle_id = uuid.UUID(str(cycle_id_raw))
        cycle = await _load_cycle_by_id(db=db, cycle_id=cycle_id)
        if cycle is None:
            continue

        campaign_id = cycle.capital_campaign_id
        campaign_version = cycle.capital_campaign_version
        decision_record_id = cycle.decision_record_id
        cycle_context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
        composition = cycle_context.get("authoritative_composition") if isinstance(cycle_context.get("authoritative_composition"), dict) else {}
        selected_decision = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}
        rejected_candidates = composition.get("rejected_candidates") if isinstance(composition.get("rejected_candidates"), list) else []
        candle = cycle_context.get("candle") if isinstance(cycle_context.get("candle"), dict) else {}

        provider = _AUTONOMOUS_CYCLE_PROVIDER
        environment = "production"
        product = _AUTONOMOUS_CYCLE_PRODUCT_ID
        proposed_action = str(composition.get("proposed_action") or cycle.proposed_action or "").strip().upper()
        decision_kind = str(selected_decision.get("decision_kind") or "").strip().upper()
        risk_verdict = str(selected_decision.get("risk_verdict") or cycle.risk_verdict or "").strip().upper()
        evidence_freshness = str(selected_decision.get("evidence_freshness") or "").strip().lower()
        sizing_trace = selected_decision.get("sizing_trace") if isinstance(selected_decision.get("sizing_trace"), dict) else {}
        final_amount = _coerce_decimal(sizing_trace.get("final_amount"))
        candle_close_time = _as_utc_iso(candle.get("close_time"))
        # A close liquidates an already-bounded position at prevailing market
        # value -- it is not a new proposed order and is not expected to
        # equal the original $5 entry amount exactly, so the canonical-amount
        # bound below only applies to new entries.
        is_close_action = "CLOSE_POSITION_PROPOSED" in {proposed_action, decision_kind}

        skip_reason = None
        if campaign_id is None or campaign_version is None:
            skip_reason = "campaign_identity_missing"
        elif provider != _AUTONOMOUS_CYCLE_PROVIDER or environment != "production" or product != _AUTONOMOUS_CYCLE_PRODUCT_ID:
            skip_reason = "scope_not_supported"
        elif cycle.termination_stage in {"hold_no_package_created", "failed_closed"}:
            skip_reason = f"termination_stage_{cycle.termination_stage}"
        elif (
            proposed_action not in {"OPEN_POSITION_PROPOSED", "BUY", "OPEN_POSITION", "CLOSE_POSITION_PROPOSED"}
            and decision_kind not in {"OPEN_POSITION_PROPOSED", "BUY", "OPEN_POSITION", "CLOSE_POSITION_PROPOSED"}
        ):
            skip_reason = "non_executable_action"
        elif decision_record_id is None:
            skip_reason = "missing_decision_record_id"
        elif evidence_freshness and evidence_freshness != "fresh":
            skip_reason = "stale_market_data"
        elif risk_verdict != "ALLOW":
            skip_reason = "risk_not_permitted"
        elif not is_close_action and final_amount != _CANONICAL_READY_PACKAGE_AMOUNT:
            skip_reason = "non_canonical_amount"
        elif candle_close_time is None:
            skip_reason = "missing_candle_close_time"

        underlying_reason: str | None = None
        rejection_reasons: list[str] = []
        if cycle.termination_stage in {"hold_no_package_created", "failed_closed"}:
            underlying_reason = str(selected_decision.get("reason") or "").strip() or None
            rejection_reasons = [
                str(item.get("reason"))
                for item in rejected_candidates
                if isinstance(item, dict) and item.get("reason")
            ]

        package_id: str | None = None
        idempotency_key: str | None = None
        if skip_reason is None:
            if await _has_active_ready_package_for_opportunity(db=db, decision_record_id=decision_record_id):
                skip_reason = "active_ready_package_exists"
            elif await _has_active_proving_activation(
                db=db,
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                provider=provider,
                environment=environment,
                product=product,
                now=datetime.now(timezone.utc),
            ):
                skip_reason = "active_proving_activation_exists"
            elif await _has_open_live_order(db=db, provider=provider, environment=environment, product=product):
                skip_reason = "open_live_order_exists"
            elif await _has_unresolved_reconciliation(db=db, provider=provider, environment=environment, product=product):
                skip_reason = "unresolved_reconciliation_exists"

        if skip_reason is None:
            runtime_campaign = await _load_runtime_campaign(db=db, campaign_id=campaign_id)
            if runtime_campaign is None or runtime_campaign.paper_account_id is None:
                skip_reason = "runtime_campaign_or_paper_account_missing"
            else:
                profile = await _load_live_trading_profile_for_paper_account(
                    db=db,
                    paper_account_id=runtime_campaign.paper_account_id,
                )
                if profile is None:
                    skip_reason = "live_trading_profile_missing"
                else:
                    idempotency_key = _build_automatic_ready_package_idempotency_key(
                        campaign_id=campaign_id,
                        campaign_version=campaign_version,
                        candle_close_time=candle_close_time,
                        decision_record_id=decision_record_id,
                        proposed_action=proposed_action or decision_kind,
                        product=product,
                        provider=provider,
                        environment=environment,
                    )
                    payload = await create_canonical_preview_package(
                        db=db,
                        request=CanonicalPreviewPackageCreateRequest(
                            campaign_id=campaign_id,
                            campaign_version=campaign_version,
                            paper_account_id=runtime_campaign.paper_account_id,
                            live_trading_profile_id=profile.id,
                            provider=provider,
                            environment=environment,
                            product=product,
                            max_proposed_order_amount=_CANONICAL_READY_PACKAGE_AMOUNT,
                            actor=_CANONICAL_READY_PACKAGE_ACTOR,
                            idempotency_key=idempotency_key,
                        ),
                    )
                    package = payload.get("package") if isinstance(payload, dict) else None
                    package_id = None if not isinstance(package, dict) else str(package.get("package_id") or "") or None
                    if bool(payload.get("idempotent")):
                        logger.info(
                            "automatic_ready_package_replayed campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s",
                            campaign_id,
                            campaign_version,
                            cycle_id,
                            candle_close_time,
                            decision_record_id,
                            package_id,
                            idempotency_key,
                        )
                    else:
                        logger.info(
                            "automatic_ready_package_created campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s",
                            campaign_id,
                            campaign_version,
                            cycle_id,
                            candle_close_time,
                            decision_record_id,
                            package_id,
                            idempotency_key,
                        )

        if skip_reason in {None, "active_ready_package_exists"} and campaign_id is not None and campaign_version is not None and decision_record_id is not None:
            try:
                await execute_automatic_ready_package_through_activation(
                    db=db,
                    request=AutomaticPackageExecutionRequest(
                        campaign_id=campaign_id,
                        campaign_version=campaign_version,
                        decision_record_id=decision_record_id,
                        package_id=None if package_id is None else uuid.UUID(package_id),
                    ),
                )
            except Exception:
                logger.exception(
                    "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s cycle_id=%s decision_record_id=%s package_id=%s reason=unexpected_executor_failure failed_closed=True",
                    campaign_id, campaign_version, cycle_id, decision_record_id, package_id,
                )

        if skip_reason is not None:
            logger.info(
                "automatic_ready_package_skipped campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s reason=%s underlying_reason=%s rejection_reasons=%s",
                campaign_id,
                campaign_version,
                cycle_id,
                candle_close_time,
                decision_record_id,
                package_id,
                idempotency_key,
                skip_reason,
                underlying_reason,
                json.dumps(rejection_reasons, sort_keys=True, separators=(",", ":")),
            )


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
    kraken_btc_identity: _KrakenBtcCandleIdentity | None = None
    try:
        autonomous_cycle_id, kraken_btc_candle = await _run_kraken_btc_autonomous_cycle_if_due(db=db)
        kraken_btc_identity = _capture_kraken_btc_candle_identity(kraken_btc_candle)
    except Exception:
        await _rollback_active_session(db=db)
        logger.exception("autonomous_cycle_failed trigger=%s", _AUTONOMOUS_CYCLE_TRIGGER)

    # The strategy roster must run, and its StrategyRosterRun row must be
    # committed, before campaign orchestration composes this candle -- the
    # aggregator resolves the roster run by an exact (asset, provider,
    # product, interval, candle_close_time, trigger) match and never falls
    # back to "latest", so composing first always sees no matching run yet
    # and skips with strategy_aggregate_skipped reason=exact_roster_run_unavailable.
    try:
        if kraken_btc_identity is None:
            kraken_btc_identity = _capture_kraken_btc_candle_identity(await _load_latest_kraken_btc_15m_candle(db))
        if kraken_btc_identity is not None:
            await run_strategy_roster_for_candle(
                db=db,
                request=StrategyRosterRequest(
                    asset_id=kraken_btc_identity.asset_id,
                    provider=_AUTONOMOUS_CYCLE_PROVIDER,
                    product_id=_AUTONOMOUS_CYCLE_PRODUCT_ID,
                    interval=_AUTONOMOUS_CYCLE_INTERVAL,
                    candle_open_time=kraken_btc_identity.open_time,
                    candle_close_time=kraken_btc_identity.close_time,
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
            orchestration_payload = await run_campaign_orchestration_preview_for_candle(
                db=db,
                trigger=_AUTONOMOUS_CYCLE_TRIGGER,
            )
            payload = orchestration_payload if isinstance(orchestration_payload, dict) else {}
            cycle_count = int(payload.get("cycle_count") or 0)
            preview_reason = str(payload.get("reason") or "")
            considered_campaigns = payload.get("considered_campaigns") if isinstance(payload.get("considered_campaigns"), list) else []
            eligible_campaigns = payload.get("eligible_campaigns") if isinstance(payload.get("eligible_campaigns"), list) else []
            skipped_campaigns = payload.get("skipped_campaigns") if isinstance(payload.get("skipped_campaigns"), list) else []
            logger.info(
                "campaign_orchestration_preview_result trigger=%s resolved_candle_id=%s resolved_candle_symbol=%s resolved_candle_product=%s resolved_candle_provider=%s resolved_candle_interval=%s resolved_candle_close_time=%s preview_reason=%s cycle_count=%s considered_campaigns=%s eligible_campaigns=%s skipped_campaigns=%s",
                _AUTONOMOUS_CYCLE_TRIGGER,
                None if kraken_btc_identity is None else kraken_btc_identity.id,
                _AUTONOMOUS_CYCLE_PRODUCT_ID.split("-")[0],
                _AUTONOMOUS_CYCLE_PRODUCT_ID,
                _AUTONOMOUS_CYCLE_PROVIDER,
                _AUTONOMOUS_CYCLE_INTERVAL,
                None if kraken_btc_identity is None else _as_utc_iso(kraken_btc_identity.close_time),
                preview_reason,
                cycle_count,
                json.dumps(considered_campaigns, sort_keys=True, separators=(",", ":")),
                json.dumps(eligible_campaigns, sort_keys=True, separators=(",", ":")),
                json.dumps(skipped_campaigns, sort_keys=True, separators=(",", ":")),
            )
            if cycle_count == 0:
                skip_reason = preview_reason or "no_campaign_candidates"
                logger.info(
                    "campaign_orchestration_preview_skipped trigger=%s resolved_candle_id=%s resolved_candle_symbol=%s resolved_candle_product=%s resolved_candle_provider=%s resolved_candle_interval=%s resolved_candle_close_time=%s reason=%s cycle_count=%s",
                    _AUTONOMOUS_CYCLE_TRIGGER,
                    None if kraken_btc_identity is None else kraken_btc_identity.id,
                    _AUTONOMOUS_CYCLE_PRODUCT_ID.split("-")[0],
                    _AUTONOMOUS_CYCLE_PRODUCT_ID,
                    _AUTONOMOUS_CYCLE_PROVIDER,
                    _AUTONOMOUS_CYCLE_INTERVAL,
                    None if kraken_btc_identity is None else _as_utc_iso(kraken_btc_identity.close_time),
                    skip_reason,
                    cycle_count,
                )
            await _attempt_automatic_ready_package_creation(
                db=db,
                orchestration_payload=orchestration_payload,
            )
            await db.commit()
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("campaign_orchestration_failed trigger=%s", _AUTONOMOUS_CYCLE_TRIGGER)

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

        if strategy_row.slug == AGGREGATE_STRATEGY_SLUG:
            # The aggregate catalog row (app.services.strategy_roster.decision_aggregator)
            # is a real, active Strategy record purely so canonical package
            # composition can resolve its identity for binding continuity
            # (_ensure_aggregate_strategy_catalog_entry in authoritative.py).
            # It represents the ensemble outcome, not an individually
            # executable strategy module, so it must never reach the generic
            # per-strategy paper-execution queue below -- it has no module in
            # strategy_registry by design, and its Decision Arena/aggregation
            # role is already fully served by the strategy roster pipeline.
            logger.info(
                "paper_execution_skip reason=aggregate_identity_not_executable strategy_id=%s strategy_slug=%s",
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
            # Each (strategy, asset) pair is its own transactional unit,
            # delimited by the db.commit() at the end of this block -- that
            # existing per-iteration commit boundary is what owns this
            # transaction, so it is also what must own the rollback on
            # failure. Without this, any exception from ingest_decision_records,
            # _load_decision_record_for_signal, _emit_execution_rejection_event,
            # _produce_research_evidence, or the commit itself (including one
            # triggered by a session already left invalid by the handled
            # orchestrate_paper_signal_execution failure below) propagated
            # completely uncaught out of run_orchestration_cycle -- surfacing
            # only at the top-level "Pipeline orchestration cycle failed"
            # handler, poisoning nothing else, but losing this cycle's
            # remaining paper-execution work and leaving the transaction
            # unrolled until the process-level catch-all called rollback
            # implicitly by discarding the session.
            strategy_id_value = strategy_row.id
            asset_id_value = asset.id
            signal_id_value: uuid.UUID | None = None
            try:
                account = None
                execution = None
                resolved_candle_interval = _resolve_candle_interval_for_asset(asset=asset, config=config)
                candles = await _load_latest_candles(
                    db,
                    asset_id=asset.id,
                    interval=resolved_candle_interval,
                    limit=config.candle_lookback_limit,
                )
                if len(candles) < 2:
                    logger.info(
                        "paper_execution_skip reason=insufficient_candles strategy_id=%s asset_id=%s candle_count=%s minimum_required=%s resolved_candle_interval=%s",
                        strategy_row.id,
                        asset.id,
                        len(candles),
                        2,
                        resolved_candle_interval,
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
                    interval=resolved_candle_interval,
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
                signal_id_value = signal_model.id

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
            except Exception as exc:
                await _rollback_active_session(db=db)
                executions_failed += 1
                logger.exception(
                    "paper_execution_iteration_failed stage=paper_execution_iteration strategy_id=%s asset_id=%s signal_id=%s exception_type=%s",
                    strategy_id_value,
                    asset_id_value,
                    signal_id_value,
                    exc.__class__.__name__,
                )
                continue

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

    completed_at = datetime.now(timezone.utc)
    set_last_successful_full_pipeline_at(completed_at)
    db.add(
        AuditLog(
            actor="orchestration_worker",
            action=_FULL_PIPELINE_COMPLETE_ACTION,
            entity_type="orchestration_worker",
            entity_id=None,
            before_state=None,
            after_state={
                "completed_at": completed_at.isoformat(),
                "ingestion_assets_ok": ingestion_result.successful_assets,
                "signals_created": signals_created,
                "execution_candidates": execution_candidates,
                "executions_attempted": executions_attempted,
                "executions_rejected": executions_rejected,
                "executions_failed": executions_failed,
                "executions_skipped": executions_skipped,
                "decisions_inserted": decision_inserted_total,
                "research_cycles_started": research_cycles_started,
                "intelligence_snapshots_captured": 1 if snapshot is not None else 0,
            },
        )
    )
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
    started_at = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex

    setup_logging()
    config = WorkerConfig.from_env()

    try:
        async with AsyncSessionLocal() as boot_db:
            boot_db.add(
                AuditLog(
                    actor="orchestration_worker",
                    action=_WORKER_BOOT_ACTION,
                    entity_type="orchestration_worker",
                    entity_id=None,
                    before_state=None,
                    after_state={
                        "started_at": started_at.isoformat(),
                        "run_id": run_id,
                    },
                )
            )
            await boot_db.commit()
    except Exception:
        logger.warning("Unable to persist orchestration worker startup event", exc_info=True)
        try:
            async with AsyncSessionLocal() as boot_failed_db:
                boot_failed_db.add(
                    AuditLog(
                        actor="orchestration_worker",
                        action=_WORKER_BOOT_FAILED_ACTION,
                        entity_type="orchestration_worker",
                        entity_id=None,
                        before_state=None,
                        after_state={
                            "started_at": started_at.isoformat(),
                            "run_id": run_id,
                        },
                    )
                )
                await boot_failed_db.commit()
        except Exception:
            logger.warning("Unable to persist orchestration worker startup failure event", exc_info=True)

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
