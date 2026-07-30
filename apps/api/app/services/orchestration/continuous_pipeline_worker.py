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
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError
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
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageCreateRequest,
    create_canonical_preview_package,
    create_controlled_proof_decision_record,
)
from app.services.controlled_proof import (
    block_exit_recovery,
    claim_exit_recovery_by_id,
    block_controlled_proof,
    claim_controlled_proof_by_id,
    compute_controlled_proof_open_exposure_usd,
    controlled_proof_entry_blocker,
    evaluate_controlled_proof_risk,
    find_pending_controlled_proof_id,
    find_pending_exit_recovery_id,
    get_controlled_proof_view,
    get_exit_recovery_view,
    link_controlled_proof_entry,
    link_controlled_proof_package,
    link_controlled_proof_sell_package,
    record_controlled_proof_waiting,
    record_exit_recovery_waiting,
    refresh_exit_recovery_completion,
    refresh_exit_recovery_outcomes,
    resolve_controlled_proof_leg_execution_lineage,
    resolve_controlled_proof_strategy_identity,
    should_propose_controlled_sell,
    supersede_expired_preview_exit_recovery_sell_package,
    supersede_stale_exit_recovery_sell_package,
)
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
from app.services.capital_campaign_orchestration.authoritative import ScorecardSessionRecoveryError
from app.services.mandates.contracts import AUTONOMY_LEVEL_2, MANDATE_PURPOSE_CONTROLLED_PROOF, MANDATE_PURPOSE_PRODUCTION
from app.services.mandates.evidence import MandateEvaluationWriteRequest, evaluate_and_record_mandate
from app.services.orchestration import asset_roster
from app.services.orchestration.venue_commissioning_bridge import service as venue_commissioning_service
from app.services.orchestration.automatic_package_executor import (
    AutomaticPackageExecutionRequest,
    execute_automatic_ready_package_through_activation,
)
from app.services.orchestration.autonomous_execution_claims import (
    advance_claimed_execution,
    claim_activated_package,
    sweep_stale_autonomous_execution_claims,
)
from app.services.orchestration.reconciliation_guard import (
    UNRESOLVED_RECONCILIATION_STATES,
    has_unresolved_reconciliation as _shared_has_unresolved_reconciliation,
    latest_reconciliation_event_per_order,
)
from app.services.orchestration.reconciliation_scheduler import poll_unresolved_live_orders
from app.services.strategy_outcomes import score_due_strategy_roster_proposal_outcomes
from app.services.strategy_roster import StrategyRosterRequest, run_strategy_roster_for_candle
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_SLUG

logger = logging.getLogger(__name__)

_AUTONOMOUS_CYCLE_TRIGGER = "kraken_btc_15m_candle_close"
_AUTONOMOUS_CYCLE_INTERVAL = "15m"

# Roster/product-symbol logic lives in asset_roster.py, shared with
# canonical_campaign_binding.py (which cannot import this module directly:
# it would create a cycle via app.services.autonomous_cycle ->
# canonical_campaign_binding). Rebound here under their original names so
# every existing call site/monkeypatch in this module and its tests is
# unaffected.
_AUTONOMOUS_CYCLE_PRODUCT_ID = asset_roster.AUTONOMOUS_CYCLE_PRODUCT_ID
_AUTONOMOUS_CYCLE_PROVIDER = asset_roster.AUTONOMOUS_CYCLE_PROVIDER
_AUTONOMOUS_CYCLE_ASSET_SYMBOLS = asset_roster.AUTONOMOUS_CYCLE_ASSET_SYMBOLS
_ADDITIONAL_PRODUCT_ASSET_SYMBOLS = asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS

# Used only when the resolved roster contains more than the single canonical
# BTC-USD product. _trigger_to_instrument (capital_campaign_orchestration.
# authoritative) parses trigger.split("_")[1] as a coin symbol to decide
# whether to scope campaign composition to one instrument; "roster" does not
# match any real instrument, so composition correctly falls through to
# evaluating every instrument in the campaign's allowed_instruments instead
# of collapsing to one -- exactly the multi-asset evaluation this exists for.
_AUTONOMOUS_MULTI_ASSET_TRIGGER = "kraken_roster_15m_candle_close"


async def _resolve_autonomous_cycle_products(*, settings, db: AsyncSession) -> list[str]:
    if getattr(settings, "asset_discovery_mode", "env") == "campaign_db":
        return await asset_roster.resolve_autonomous_cycle_products_from_campaign(db=db, settings=settings)
    return asset_roster.resolve_autonomous_cycle_products(settings=settings)


def _resolve_autonomous_cycle_trigger(*, products: list[str]) -> str:
    return _AUTONOMOUS_CYCLE_TRIGGER if products == [_AUTONOMOUS_CYCLE_PRODUCT_ID] else _AUTONOMOUS_MULTI_ASSET_TRIGGER


def _asset_symbols_for_product(*, product_id: str) -> tuple[str, ...]:
    if product_id == _AUTONOMOUS_CYCLE_PRODUCT_ID:
        return _AUTONOMOUS_CYCLE_ASSET_SYMBOLS
    return _ADDITIONAL_PRODUCT_ASSET_SYMBOLS.get(product_id, ())


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
_UNRESOLVED_RECONCILIATION_STATES = UNRESOLVED_RECONCILIATION_STATES


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


async def _load_latest_kraken_asset_15m_candle(db: AsyncSession, *, product_id: str, symbols: tuple[str, ...]) -> Candle | None:
    if not hasattr(db, "execute"):
        return None
    if not symbols:
        logger.warning("autonomous_cycle_skip reason=unknown_asset_symbols product_id=%s", product_id)
        return None

    asset_result = await db.execute(
        select(Asset)
        .where(Asset.is_active.is_(True))
        .where(Asset.asset_class == "crypto")
        .where(Asset.exchange == _AUTONOMOUS_CYCLE_PROVIDER)
        .where(Asset.symbol.in_(symbols))
        .order_by(Asset.created_at.desc())
        .limit(2)
    )
    assets = list(asset_result.scalars().all())
    if not assets:
        logger.info("autonomous_cycle_skip reason=kraken_asset_missing product_id=%s", product_id)
        return None
    if len(assets) > 1:
        logger.warning("autonomous_cycle_skip reason=ambiguous_kraken_assets product_id=%s asset_count=%s", product_id, len(assets))
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
        logger.info("autonomous_cycle_skip reason=kraken_15m_candle_missing product_id=%s", product_id)
    return candle


async def _load_latest_kraken_btc_15m_candle(db: AsyncSession) -> Candle | None:
    """Preserved as the exact BTC-only entry point for any remaining direct
    caller; delegates to the generalized per-product loader."""
    return await _load_latest_kraken_asset_15m_candle(
        db, product_id=_AUTONOMOUS_CYCLE_PRODUCT_ID, symbols=_AUTONOMOUS_CYCLE_ASSET_SYMBOLS,
    )


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


def _build_kraken_asset_candle_idempotency_seed(*, product_id: str, candle: Candle) -> str:
    close_time = candle.close_time
    close_time_utc = close_time if close_time.tzinfo is not None else close_time.replace(tzinfo=timezone.utc)
    return f"kraken-{product_id.lower()}-15m-close:{close_time_utc.astimezone(timezone.utc).isoformat()}"


async def _trigger_autonomous_cycles_for_products(
    *, db: AsyncSession, products: list[str], trigger: str,
) -> dict[str, tuple[uuid.UUID | None, _KrakenBtcCandleIdentity | None]]:
    """Per-product analogue of _run_kraken_btc_autonomous_cycle_if_due,
    looped over the resolved roster. Every product is isolated in its own
    try/except: one asset's failure is logged and skipped, never blocking
    evaluation of the others. Returns {product_id: (cycle_id, candle_identity)},
    with (None, None) for any product that was skipped or failed. For the
    default single-product (BTC-only) roster this produces the exact same
    single autonomous_cycle_triggered log line as before, since `products`
    has exactly one entry and `trigger` equals _AUTONOMOUS_CYCLE_TRIGGER."""
    results: dict[str, tuple[uuid.UUID | None, _KrakenBtcCandleIdentity | None]] = {
        product_id: (None, None) for product_id in products
    }

    # In single-asset (default) mode -- trigger is still the original
    # _AUTONOMOUS_CYCLE_TRIGGER -- the BTC-USD leg always delegates to the
    # original, independently tested/mockable entry point rather than
    # reimplementing its query shape here. This is what makes the default
    # roster's runtime behavior, including every existing unit/integration
    # test that monkeypatches _run_kraken_btc_autonomous_cycle_if_due
    # directly, byte identical to before this change.
    #
    # In multi-asset mode (trigger is _AUTONOMOUS_MULTI_ASSET_TRIGGER), BTC
    # must instead go through the same generalized per-product path as
    # every other product: it needs its StrategyRosterRun persisted under
    # the SHARED trigger too, or campaign composition (which resolves each
    # instrument's roster run by an exact trigger match) would never find
    # BTC's evidence -- _run_kraken_btc_autonomous_cycle_if_due always uses
    # the hardcoded single-asset trigger internally and cannot do this.
    remaining_products = list(products)
    if _AUTONOMOUS_CYCLE_PRODUCT_ID in results and trigger == _AUTONOMOUS_CYCLE_TRIGGER:
        try:
            btc_cycle_id, btc_candle = await _run_kraken_btc_autonomous_cycle_if_due(db=db)
            results[_AUTONOMOUS_CYCLE_PRODUCT_ID] = (btc_cycle_id, _capture_kraken_btc_candle_identity(btc_candle))
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("autonomous_cycle_asset_failed trigger=%s product_id=%s", trigger, _AUTONOMOUS_CYCLE_PRODUCT_ID)
        remaining_products = [product_id for product_id in products if product_id != _AUTONOMOUS_CYCLE_PRODUCT_ID]

    if not remaining_products:
        return results

    mandate = await _load_single_active_kraken_mandate(db)
    if mandate is None:
        return results

    for product_id in remaining_products:
        try:
            symbols = _asset_symbols_for_product(product_id=product_id)
            latest_candle = await _load_latest_kraken_asset_15m_candle(db, product_id=product_id, symbols=symbols)
            if latest_candle is None:
                continue

            result = await run_autonomous_preview_cycle(
                db=db,
                request=AutonomousCycleRequest(
                    mandate_id=mandate.mandate_id,
                    actor="orchestration_worker",
                    product_id=product_id,
                    strategy_interval=_AUTONOMOUS_CYCLE_INTERVAL,
                    trigger=trigger,
                    idempotency_seed=_build_kraken_asset_candle_idempotency_seed(product_id=product_id, candle=latest_candle),
                    candle_id=latest_candle.id,
                    candle_close_time=latest_candle.close_time,
                ),
            )
            logger.info(
                "autonomous_cycle_triggered trigger=%s product_id=%s mandate_id=%s cycle_id=%s state=%s replayed=%s idempotency_key=%s",
                trigger, product_id, mandate.mandate_id, result.cycle_id, result.state, result.replayed, result.idempotency_key,
            )
            results[product_id] = (result.cycle_id, _capture_kraken_btc_candle_identity(latest_candle))
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("autonomous_cycle_asset_failed trigger=%s product_id=%s", trigger, product_id)
            continue

    return results


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


async def _load_originating_autonomous_cycle(
    *, db: AsyncSession, cycle_id: uuid.UUID | None,
) -> AutonomousCycleRun | None:
    if cycle_id is None:
        return None
    return await db.scalar(
        select(AutonomousCycleRun).where(
            AutonomousCycleRun.cycle_id == cycle_id,
            AutonomousCycleRun.cycle_kind == "autonomous",
        ).limit(1)
    )


async def _ensure_campaign_cycle_mandate_evaluation(
    *,
    db: AsyncSession,
    campaign_cycle: AutonomousCycleRun,
    autonomous_cycle: AutonomousCycleRun | None,
    strategy_identity: str,
    product: str,
    side: str,
    proposed_notional: Decimal,
    decision_record_id: uuid.UUID | None = None,
    controlled_proof_forced_entry: bool = False,
) -> str | None:
    if all(
        getattr(campaign_cycle, field, None) is not None
        for field in ("mandate_id", "mandate_version_id", "mandate_evaluation_id")
    ):
        return None
    if autonomous_cycle is None:
        return "originating_autonomous_cycle_missing"
    autonomous_context = autonomous_cycle.cycle_context if isinstance(autonomous_cycle.cycle_context, dict) else {}
    campaign_context = campaign_cycle.cycle_context if isinstance(campaign_cycle.cycle_context, dict) else {}
    if (
        str(autonomous_context.get("trigger") or "") != str(campaign_context.get("trigger") or "")
        or str(autonomous_context.get("product_id") or "").upper() != product.upper()
    ):
        return "autonomous_campaign_cycle_correlation_mismatch"
    if campaign_cycle.cycle_kind != "campaign" or campaign_cycle.decision_record_id is None:
        return "campaign_cycle_identity_invalid"

    # A Controlled Proof forced entry must resolve, pin, and evaluate under
    # the dedicated CONTROLLED_PROOF mandate -- it must never inherit
    # autonomous_cycle.mandate_id, which is always the ordinary PRODUCTION
    # mandate lineage of the ambient organic cycle this forced entry rides
    # alongside. Fails closed (a distinct, diagnosable reason_code) rather
    # than silently falling back to the organic mandate when the dedicated
    # mandate is unconfigured -- this branch must never share a mandate_id
    # with the else branch below.
    controlled_proof_open_exposure_usd = Decimal("0")
    expected_mandate_purpose = MANDATE_PURPOSE_PRODUCTION
    if controlled_proof_forced_entry:
        mandate_id = getattr(get_settings(), "controlled_proof_mandate_id", None)
        if mandate_id is None:
            return "controlled_proof_mandate_missing"
        expected_mandate_purpose = MANDATE_PURPOSE_CONTROLLED_PROOF
        if campaign_cycle.capital_campaign_id is not None:
            runtime_campaign = await _load_runtime_campaign(db=db, campaign_id=campaign_cycle.capital_campaign_id)
            if runtime_campaign is not None and runtime_campaign.paper_account_id is not None:
                profile = await _load_live_trading_profile_for_paper_account(
                    db=db, paper_account_id=runtime_campaign.paper_account_id,
                )
                if profile is not None:
                    controlled_proof_open_exposure_usd = await compute_controlled_proof_open_exposure_usd(
                        db=db, live_trading_profile_id=profile.id,
                    )
        pinned_mandate_version_id = None
    else:
        if autonomous_cycle.mandate_id is None or autonomous_cycle.mandate_version_id is None:
            return "originating_autonomous_mandate_identity_missing"
        mandate_id = autonomous_cycle.mandate_id
        pinned_mandate_version_id = autonomous_cycle.mandate_version_id

    # For a controlled-proof-forced entry, the caller passes the freshly
    # created Controlled Proof DecisionRecord's id here -- never the
    # organic campaign_cycle.decision_record_id -- because that is the
    # decision create_canonical_preview_package will actually bind the
    # resulting package to. Using the organic id here (the default, correct
    # for every non-forced cycle) would make this evaluation reference a
    # decision the package never ends up using, and
    # create_canonical_preview_package's own mandate_evaluation.decision_id
    # == decision.decision_id check would then fail every time
    # (canonical_mandate_evaluation_mismatch).
    effective_decision_id = campaign_cycle.decision_record_id if decision_record_id is None else decision_record_id
    evaluation = await evaluate_and_record_mandate(
        db=db,
        request=MandateEvaluationWriteRequest(
            mandate_id=mandate_id,
            actor="orchestration_worker",
            strategy_version=strategy_identity,
            product=product,
            side=side,
            proposed_notional_usd=proposed_notional,
            current_open_exposure_usd=Decimal("0"),
            daily_deployed_usd=Decimal("0"),
            daily_realized_loss_usd=Decimal("0"),
            campaign_drawdown_usd=Decimal("0"),
            consecutive_losses=0,
            current_position_count=0,
            risk_verdict="ACCEPTED",
            evidence_age_seconds=0,
            kill_switch_engaged=False,
            observed_at=datetime.now(timezone.utc),
            decision_id=effective_decision_id,
            request_context={
                "purpose": "automatic_ready_package_campaign_authority",
                "autonomous_cycle_id": str(autonomous_cycle.cycle_id),
                "campaign_orchestration_cycle_id": str(campaign_cycle.cycle_id),
                "campaign_id": None if campaign_cycle.capital_campaign_id is None else str(campaign_cycle.capital_campaign_id),
                "campaign_version": campaign_cycle.capital_campaign_version,
                "controlled_proof_forced_entry": controlled_proof_forced_entry,
            },
            idempotency_key=f"campaign-cycle-mandate-eval:{campaign_cycle.cycle_id}",
            audit_correlation_id=campaign_cycle.audit_correlation_id,
            software_build_version=campaign_cycle.software_build_version,
            expected_mandate_purpose=expected_mandate_purpose,
            controlled_proof_open_exposure_usd=controlled_proof_open_exposure_usd,
        ),
    )
    if (
        evaluation.mandate_id != mandate_id
        or (pinned_mandate_version_id is not None and evaluation.mandate_version_id != pinned_mandate_version_id)
        or evaluation.decision_id != effective_decision_id
        or evaluation.authorization_result != "AUTHORIZED"
        or evaluation.approval_result != "APPROVAL_SATISFIED_BY_ACTIVE_MANDATE"
    ):
        return "campaign_mandate_evaluation_mismatched_or_rejected"
    campaign_cycle.mandate_id = evaluation.mandate_id
    campaign_cycle.mandate_version_id = evaluation.mandate_version_id
    campaign_cycle.mandate_evaluation_id = evaluation.evaluation_id
    campaign_cycle.cycle_context = {
        **(campaign_cycle.cycle_context or {}),
        "originating_autonomous_cycle_id": str(autonomous_cycle.cycle_id),
    }
    await db.flush()
    return None


async def _load_runtime_campaign(*, db: AsyncSession, campaign_id: uuid.UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


async def _load_live_trading_profile_for_paper_account(*, db: AsyncSession, paper_account_id: uuid.UUID) -> LiveTradingProfile | None:
    return await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
        .limit(1)
    )


async def _has_active_ready_package_for_opportunity(
    *, db: AsyncSession, decision_record_id: uuid.UUID, now: datetime | None = None,
) -> bool:
    observed_at = now or datetime.now(timezone.utc)
    row = await db.scalar(
        select(CanonicalPreviewPackage.package_id)
        .where(CanonicalPreviewPackage.decision_record_id == decision_record_id)
        .where(CanonicalPreviewPackage.package_state.in_(_CANONICAL_READY_STATES))
        .where(CanonicalPreviewPackage.preview_expires_at > observed_at)
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
    return latest_reconciliation_event_per_order(
        provider=provider, environment=environment, product=product,
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
    # Query itself now lives in reconciliation_guard.has_unresolved_reconciliation
    # (shared with Controlled Proof's stale-proof recovery safety check, so
    # both agree on the identical definition of "unresolved" for the same
    # market scope) -- this thin wrapper only adds the worker's own
    # diagnostics logging on top.
    result = await _shared_has_unresolved_reconciliation(db=db, provider=provider, environment=environment, product=product)
    if result:
        await _log_unresolved_reconciliation_diagnostics(db=db, provider=provider, environment=environment, product=product)
    return result


async def _attempt_automatic_ready_package_creation(
    *,
    db: AsyncSession,
    orchestration_payload: dict[str, object] | None,
    originating_autonomous_cycle_id: uuid.UUID | None = None,
    autonomous_cycle_ids_by_product: dict[str, uuid.UUID | None] | None = None,
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
        # The winning instrument as selected by campaign composition's own
        # deterministic ranking (authoritative.py's candidate_rows.sort +
        # selected_decision["instrument"]) -- never a hardcoded product.
        # Falls back to the canonical BTC-USD product only when no
        # instrument was selected (HOLD/no-candidate cycles), where this
        # value is never actually acted on downstream.
        product = str(selected_decision.get("instrument") or "").strip().upper() or _AUTONOMOUS_CYCLE_PRODUCT_ID
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

        # Controlled Proof: purely additive. A cheap, side-effect-light
        # lookup every cycle (single indexed SELECT, no matching row in the
        # overwhelming common case of no pending proof) that only ever
        # transitions an operator-requested proof from REQUESTED to CLAIMED.
        # It does not create, skip, or alter any decision, gate, or package
        # -- everything below this line runs exactly as it did before this
        # existed. Deliberately isolated in its own try/except: a defect or
        # outage in the controlled-proof subsystem must never block, skip,
        # or otherwise affect the core autonomous package-creation path it
        # is layered on top of.
        # Operator Controlled Proof candidates are dispatched by
        # _attempt_operator_controlled_proof_entry before this autonomous
        # loop.  Never claim one here: doing so would make its authority and
        # progress depend on this ambient cycle's selected action/skip state.
        controlled_proof = None

        skip_reason = None
        if campaign_id is None or campaign_version is None:
            skip_reason = "campaign_identity_missing"
        elif (
            provider != _AUTONOMOUS_CYCLE_PROVIDER
            or environment != "production"
            or product not in await _resolve_autonomous_cycle_products(settings=get_settings(), db=db)
        ):
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

        # Controlled Proof: an operator-triggered proving workflow
        # (docs/CONTROLLED_PROOF_ACTIVATION.md), not a rider on whatever the
        # organic autonomous strategy happens to be doing this tick.
        # Whenever a proof is claimed for this exact scope, it constructs
        # and evaluates its OWN candidate here -- unconditionally, never
        # gated on the organic skip_reason/proposed_action/risk_verdict
        # above (no dependency on non_executable_action, strategy_hold_
        # signal, termination_stage_hold_no_package_created, or an organic
        # Risk rejection). A claimed proof's own candidate takes priority
        # over the organic decision for this cycle's package-creation
        # attempt -- this system runs exactly one campaign/product, so the
        # two can never coexist as separate packages in the same cycle
        # regardless; an operator's explicit authorization for that one
        # slot must not silently lose to the ambient strategy.
        #
        # Only market-data facts already computed for this exact product
        # this tick are still reused (evidence_freshness, candle_close_time,
        # decision_record_id-presence as a "did this tick's composition run
        # at all" pipeline-health check) -- these describe the state of the
        # world, not a strategy decision, and a Controlled Proof BUY still
        # needs fresh market evidence like any other. Isolated in its own
        # try/except: a defect here must never affect the core pipeline.
        controlled_proof_forced_entry = False
        if controlled_proof is not None:
            try:
                wants_sell = await should_propose_controlled_sell(db=db, proof=controlled_proof)
                already_has_entry = controlled_proof.decision_record_id is not None
                target_action = (
                    "CLOSE_POSITION_PROPOSED" if wants_sell
                    else None if already_has_entry
                    else "OPEN_POSITION_PROPOSED"
                )
            except Exception:
                logger.exception("controlled_proof_sell_readiness_check_failed proof_id=%s", controlled_proof.proof_id)
                target_action = None
            if target_action is not None:
                if decision_record_id is None:
                    skip_reason = "missing_decision_record_id"
                elif evidence_freshness and evidence_freshness != "fresh":
                    skip_reason = "stale_market_data"
                elif candle_close_time is None:
                    skip_reason = "missing_candle_close_time"
                else:
                    # The organic risk_verdict (if any) reflects a
                    # completely different, organically-composed candidate
                    # -- it must never gate or stand in for a forced entry's
                    # own risk decision. A genuine, fresh Risk Engine
                    # evaluation of this exact forced candidate is required
                    # instead. Isolated in its own try/except: a defect
                    # here must fail closed, never fall through to treating
                    # a missing result as ALLOW.
                    controlled_proof_risk_outcome = None
                    try:
                        risk_runtime_campaign = await _load_runtime_campaign(db=db, campaign_id=campaign_id)
                        if risk_runtime_campaign is None or risk_runtime_campaign.paper_account_id is None:
                            raise LookupError("runtime_campaign_or_paper_account_missing")
                        controlled_proof_risk_outcome = await evaluate_controlled_proof_risk(
                            db=db,
                            proof_id=controlled_proof.proof_id,
                            campaign_id=campaign_id,
                            campaign_version=campaign_version,
                            paper_account_id=risk_runtime_campaign.paper_account_id,
                            product_id=product,
                            side="SELL" if target_action == "CLOSE_POSITION_PROPOSED" else "BUY",
                            notional_usd=_CANONICAL_READY_PACKAGE_AMOUNT,
                            actor="system:controlled_proof_worker",
                        )
                    except Exception:
                        logger.exception(
                            "controlled_proof_risk_evaluation_call_failed proof_id=%s campaign_id=%s campaign_version=%s",
                            controlled_proof.proof_id, campaign_id, campaign_version,
                        )
                        controlled_proof_risk_outcome = None

                    if controlled_proof_risk_outcome is None or controlled_proof_risk_outcome.verdict == "UNAVAILABLE":
                        skip_reason = "controlled_proof_risk_unavailable"
                    elif controlled_proof_risk_outcome.verdict == "RESIZE":
                        # The canonical package pipeline requires exactly
                        # $5 (create_canonical_preview_package rejects any
                        # other max_proposed_order_amount) -- there is no
                        # code path to execute at a smaller, risk-approved
                        # size, so a RESIZE can never be silently proceeded
                        # with at the full requested amount. Blocks with a
                        # distinct reason so it is diagnosable as "risk
                        # wants a smaller size", not "risk said no". Left
                        # retryable (proof stays CLAIMED, bounded by its own
                        # expires_at) rather than terminal -- market/account
                        # state that produced a resize this attempt may not
                        # next attempt.
                        skip_reason = "controlled_proof_risk_resize"
                    elif controlled_proof_risk_outcome.verdict == "DENY":
                        # Risk Engine authority is final: a genuine DENY
                        # transitions the proof to a truthful, terminal
                        # BLOCKED state with the exact reason -- it must
                        # never be left sitting in CLAIMED with only a log
                        # line as evidence (see block_controlled_proof).
                        skip_reason = "controlled_proof_risk_denied"
                        try:
                            await block_controlled_proof(
                                db=db, proof=controlled_proof,
                                reason=f"controlled_proof_risk_denied:{controlled_proof_risk_outcome.reason_code}",
                                actor="system:controlled_proof_worker",
                            )
                        except Exception:
                            logger.exception(
                                "controlled_proof_block_on_deny_failed proof_id=%s", controlled_proof.proof_id,
                            )
                    else:
                        skip_reason = None
                        proposed_action = target_action
                        decision_kind = target_action
                        is_close_action = target_action == "CLOSE_POSITION_PROPOSED"
                        final_amount = _CANONICAL_READY_PACKAGE_AMOUNT
                        controlled_proof_forced_entry = True

        underlying_reason: str | None = None
        rejection_reasons: list[str] = []
        if cycle.termination_stage in {"hold_no_package_created", "failed_closed"}:
            underlying_reason = str(selected_decision.get("reason") or "").strip() or None
            rejection_reasons = [
                str(item.get("reason"))
                for item in rejected_candidates
                if isinstance(item, dict) and item.get("reason")
            ]

        autonomous_cycle = None
        if skip_reason is None:
            bundle_complete = all(
                getattr(cycle, field, None) is not None
                for field in ("mandate_id", "mandate_version_id", "mandate_evaluation_id")
            )
            if bundle_complete:
                skip_reason = None
            else:
                # Must be the autonomous cycle for the SAME product as the
                # winning candidate, not just "the BTC cycle from this tick"
                # -- otherwise a non-BTC winner's mandate-evaluation
                # correlation check (product_id comparison below) would
                # always mismatch against BTC's own autonomous_context.
                resolved_originating_cycle_id = (
                    (autonomous_cycle_ids_by_product or {}).get(product) or originating_autonomous_cycle_id
                )
                autonomous_cycle = await _load_originating_autonomous_cycle(
                    db=db, cycle_id=resolved_originating_cycle_id,
                )
                strategy_identity = str(selected_decision.get("strategy_identity") or "").strip()
                if not strategy_identity and controlled_proof_forced_entry:
                    # The HOLD cycle's own composition never selected a
                    # winning strategy (nothing to select for a HOLD) --
                    # resolve a real, mandate-authorized one independently.
                    # The dedicated CONTROLLED_PROOF mandate, never the
                    # ordinary production mandate setting -- its own
                    # allowed_strategy_versions is the only list this forced
                    # entry's strategy identity may ever be drawn from.
                    governing_mandate_id = getattr(get_settings(), "controlled_proof_mandate_id", None)
                    if governing_mandate_id is not None:
                        try:
                            strategy_identity = await resolve_controlled_proof_strategy_identity(
                                db=db, mandate_id=governing_mandate_id,
                            ) or ""
                        except Exception:
                            logger.exception(
                                "controlled_proof_strategy_identity_resolution_failed proof_id=%s",
                                getattr(controlled_proof, "proof_id", None),
                            )
                            strategy_identity = ""
                side = "SELL" if is_close_action else "BUY"
                if not strategy_identity:
                    skip_reason = "campaign_strategy_identity_missing"
                elif final_amount is None or final_amount <= 0:
                    skip_reason = "campaign_notional_missing"
                else:
                    # A controlled-proof-forced entry's mandate evaluation
                    # must reference the truthful Controlled Proof
                    # DecisionRecord, not the organic cycle's -- created
                    # here, before mandate evaluation, so the evaluation is
                    # never computed against a decision identity that
                    # predates (and diverges from) the one
                    # create_canonical_preview_package will actually bind
                    # the resulting package to. Isolated in its own
                    # try/except like every other controlled-proof step: a
                    # failure here must fail closed, never fall through to
                    # the organic id.
                    controlled_proof_decision_record_id = None
                    if controlled_proof_forced_entry:
                        try:
                            controlled_proof_decision_record_id = await create_controlled_proof_decision_record(
                                db=db, campaign_id=campaign_id, controlled_proof_id=controlled_proof.proof_id,
                                forced_action=proposed_action, product=product, provider=provider,
                                actor="system:controlled_proof_worker", strategy_identity=strategy_identity,
                            )
                        except Exception:
                            logger.exception(
                                "controlled_proof_decision_record_creation_failed proof_id=%s",
                                getattr(controlled_proof, "proof_id", None),
                            )
                            controlled_proof_decision_record_id = None
                    if controlled_proof_forced_entry and controlled_proof_decision_record_id is None:
                        skip_reason = "controlled_proof_decision_record_unavailable"
                    else:
                        skip_reason = await _ensure_campaign_cycle_mandate_evaluation(
                            db=db,
                            campaign_cycle=cycle,
                            autonomous_cycle=autonomous_cycle,
                            strategy_identity=strategy_identity,
                            product=product,
                            side=side,
                            proposed_notional=final_amount,
                            decision_record_id=controlled_proof_decision_record_id,
                            controlled_proof_forced_entry=controlled_proof_forced_entry,
                        )

        missing_evidence_fields = [
            field for field in ("mandate_id", "mandate_version_id", "mandate_evaluation_id")
            if getattr(cycle, field, None) is None
        ]
        logger.info(
            "automatic_package_identity_bundle autonomous_cycle_id=%s campaign_orchestration_cycle_id=%s decision_record_id=%s mandate_id=%s mandate_version_id=%s mandate_evaluation_id=%s preview_id=%s action=%s evidence_complete=%s missing_evidence_fields=%s package_creation_eligible=%s",
            None if autonomous_cycle is None else autonomous_cycle.cycle_id,
            cycle.cycle_id, decision_record_id, cycle.mandate_id, cycle.mandate_version_id,
            cycle.mandate_evaluation_id, getattr(cycle, "preview_id", None), proposed_action or decision_kind,
            not missing_evidence_fields, json.dumps(missing_evidence_fields),
            skip_reason is None and not missing_evidence_fields,
        )
        if skip_reason is None and missing_evidence_fields:
            skip_reason = "missing_mandate_evaluation_identity"

        package_id: str | None = None
        idempotency_key: str | None = None
        # Defaults to the organic value so a retry cycle that skips package
        # creation entirely (skip_reason="active_ready_package_exists" --
        # e.g. a package from a prior cycle still sitting in READY) still
        # has a defined identity to pass into activation below. Only
        # overwritten with the package's own truthful value when this cycle
        # actually (re)creates or replays the package.
        linked_decision_record_id = decision_record_id
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
                            expected_decision_record_id=None if controlled_proof_forced_entry else decision_record_id,
                            mandate_id=getattr(cycle, "mandate_id", None),
                            mandate_version_id=getattr(cycle, "mandate_version_id", None),
                            mandate_evaluation_id=getattr(cycle, "mandate_evaluation_id", None),
                            commissioning_entry_mode="controlled_proof" if controlled_proof_forced_entry else None,
                            forced_action=proposed_action if controlled_proof_forced_entry else None,
                            controlled_proof_id=getattr(controlled_proof, "proof_id", None) if controlled_proof_forced_entry else None,
                        ),
                    )
                    package = payload.get("package") if isinstance(payload, dict) else None
                    package_id = None if not isinstance(package, dict) else str(package.get("package_id") or "") or None
                    # The package's own decision_record_id is the truthful
                    # value actually bound to it -- for a controlled-proof-
                    # forced entry this is a freshly created record
                    # describing the forced action, never the organic
                    # cycle's own decision_record_id computed above. Falls
                    # back to the organic value only if the payload omits it
                    # (e.g. an unrelated failure shape).
                    linked_decision_record_id = decision_record_id
                    if isinstance(package, dict) and package.get("decision_record_id"):
                        try:
                            linked_decision_record_id = uuid.UUID(str(package["decision_record_id"]))
                        except ValueError:
                            linked_decision_record_id = decision_record_id
                    if bool(payload.get("idempotent")):
                        logger.info(
                            "automatic_ready_package_replayed campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s controlled_proof_id=%s",
                            campaign_id,
                            campaign_version,
                            cycle_id,
                            candle_close_time,
                            linked_decision_record_id,
                            package_id,
                            idempotency_key,
                            getattr(controlled_proof, "proof_id", None),
                        )
                    else:
                        logger.info(
                            "automatic_ready_package_created campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s controlled_proof_id=%s",
                            campaign_id,
                            campaign_version,
                            cycle_id,
                            candle_close_time,
                            linked_decision_record_id,
                            package_id,
                            idempotency_key,
                            getattr(controlled_proof, "proof_id", None),
                        )

                    # Controlled Proof linkage: purely additive, after the
                    # existing, unmodified package creation above already
                    # succeeded. Only when a proof is actually claimed for
                    # this exact scope. Idempotent: link_controlled_proof_
                    # entry/_package/_sell_package each no-op once already
                    # linked, so replays of the same cycle never relink or
                    # double-propose either side.
                    if controlled_proof is not None and linked_decision_record_id is not None and package_id is not None:
                        try:
                            if not is_close_action:
                                await link_controlled_proof_entry(
                                    db=db, proof=controlled_proof, decision_record_id=linked_decision_record_id,
                                    mandate_id=getattr(cycle, "mandate_id", None),
                                    mandate_version_id=getattr(cycle, "mandate_version_id", None),
                                    mandate_evaluation_id=getattr(cycle, "mandate_evaluation_id", None),
                                )
                                await link_controlled_proof_package(
                                    db=db, proof=controlled_proof, package_id=uuid.UUID(package_id),
                                )
                            elif controlled_proof.package_id is not None:
                                await link_controlled_proof_sell_package(
                                    db=db, proof=controlled_proof, sell_package_id=uuid.UUID(package_id),
                                )
                        except Exception:
                            logger.exception(
                                "controlled_proof_linkage_failed proof_id=%s decision_record_id=%s package_id=%s is_close_action=%s",
                                getattr(controlled_proof, "proof_id", None), linked_decision_record_id, package_id, is_close_action,
                            )

        if skip_reason in {None, "active_ready_package_exists"} and campaign_id is not None and campaign_version is not None and decision_record_id is not None:
            try:
                progression = await execute_automatic_ready_package_through_activation(
                    db=db,
                    request=AutomaticPackageExecutionRequest(
                        campaign_id=campaign_id,
                        campaign_version=campaign_version,
                        decision_record_id=linked_decision_record_id,
                        package_id=None if package_id is None else uuid.UUID(package_id),
                    ),
                )
                logger.info(
                    "automatic_package_progression_result package_id=%s campaign_id=%s campaign_version=%s decision_record_id=%s mandate_id=%s starting_state=%s final_state=%s authority_source=%s authorization_result=%s dry_run_result=%s activation_result=%s replayed=%s reason_code=%s failed_closed=%s live_submission_called=false provider_submission_called=false",
                    progression.package_id, progression.campaign_id, progression.campaign_version,
                    progression.decision_record_id, progression.mandate_id, progression.starting_state,
                    progression.activation_state if progression.activation_state == "ACTIVATED" else progression.authorization_state,
                    progression.authority_source, progression.authorization_state, progression.dry_run_state,
                    progression.activation_state, progression.replayed, progression.final_reason_code,
                    progression.failed_closed,
                )
                # Defense in depth alongside automatic_package_executor._outcome's
                # own activation_state/failed_closed consistency guarantee:
                # never act on an outcome the executor itself flagged as
                # failed_closed, regardless of what activation_state reports.
                if (
                    progression.activation_state == "ACTIVATED"
                    and not progression.failed_closed
                    and progression.package_id is not None
                ):
                    claim_outcome = await claim_activated_package(
                        db=db,
                        package_id=progression.package_id,
                    )
                    if claim_outcome.claim is not None:
                        await advance_claimed_execution(db=db, claim=claim_outcome.claim)
                    else:
                        logger.info(
                            "autonomous_execution_claim_skipped package_id=%s campaign_id=%s campaign_version=%s reason=%s provider_call_made=false",
                            progression.package_id, progression.campaign_id,
                            progression.campaign_version, claim_outcome.reason_code,
                        )
            except Exception:
                logger.exception(
                    "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s cycle_id=%s decision_record_id=%s package_id=%s reason=unexpected_executor_failure failed_closed=True",
                    campaign_id, campaign_version, cycle_id, decision_record_id, package_id,
                )
                logger.info(
                    "automatic_package_progression_result package_id=%s campaign_id=%s campaign_version=%s decision_record_id=%s mandate_id=None starting_state=UNKNOWN final_state=FAILED_CLOSED authority_source=None authorization_result=NOT_ATTEMPTED dry_run_result=NOT_ATTEMPTED activation_result=NOT_ATTEMPTED replayed=false reason_code=unexpected_executor_failure failed_closed=true live_submission_called=false provider_submission_called=false",
                    package_id, campaign_id, campaign_version, decision_record_id,
                )

        if skip_reason is not None:
            logger.info(
                "automatic_ready_package_skipped campaign_id=%s campaign_version=%s cycle_id=%s candle_close_time=%s decision_record_id=%s package_id=%s idempotency_key=%s reason=%s underlying_reason=%s rejection_reasons=%s controlled_proof_id=%s",
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
                getattr(controlled_proof, "proof_id", None),
            )


async def _run_autonomous_and_campaign_orchestration_attempt(*, db: AsyncSession) -> None:
    """Composes the campaign orchestration cycle for the latest already-
    ingested candle and attempts automatic-ready-package creation --
    including the Controlled Proof claim and forced-entry path. Shared by
    both the regular timer-driven poll (run_orchestration_cycle, below) and
    an operator-triggered immediate dispatch (see
    dispatch_controlled_proof_immediate_attempt) -- exactly one code path
    for this, never two divergent ones. No new gate, check, or shortcut is
    introduced here: strategy composition, fresh risk evaluation, mandate
    authorization, activation authorization, and audit persistence all run
    exactly as they do on a normal poll.

    Deliberately excludes ingestion, venue-commissioning resume, and stale-
    claim sweep -- those are independent maintenance passes over already-
    ingested state, not required for a single composition+package attempt,
    and remain solely the regular timer-driven cycle's responsibility.
    """
    # Additive operator workflow: attempt it independently before ambient
    # strategy composition.  A failure is contained and cannot change the
    # autonomous cycle that follows.
    #
    # refresh_exit_recovery_outcomes() is deliberately NOT called here --
    # it runs exactly once per cycle, unconditionally, from
    # run_orchestration_cycle itself (before ingestion/venue-commissioning/
    # claim-sweep/campaign-path stages can skip or fail it), independent of
    # live-order reconciliation candidate count. Calling it again here
    # would run it twice per cycle.
    if hasattr(db, "scalars") and hasattr(db, "scalar"):
        try:
            pending_recovery_id = await find_pending_exit_recovery_id(db=db)
            if pending_recovery_id is not None:
                await _attempt_operator_controlled_proof_entry(db=db, recovery_id=pending_recovery_id)
            pending_proof_id = await find_pending_controlled_proof_id(db=db)
            if pending_proof_id is not None:
                await _attempt_operator_controlled_proof_entry(db=db, proof_id=pending_proof_id)
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("controlled_proof_periodic_dispatch_failed")

    autonomous_cycle_products = await _resolve_autonomous_cycle_products(settings=get_settings(), db=db)
    autonomous_cycle_trigger = _resolve_autonomous_cycle_trigger(products=autonomous_cycle_products)

    autonomous_cycle_id: uuid.UUID | None = None
    kraken_btc_identity: _KrakenBtcCandleIdentity | None = None
    cycle_results: dict[str, tuple[uuid.UUID | None, _KrakenBtcCandleIdentity | None]] = {}
    try:
        cycle_results = await _trigger_autonomous_cycles_for_products(
            db=db, products=autonomous_cycle_products, trigger=autonomous_cycle_trigger,
        )
        autonomous_cycle_id, kraken_btc_identity = cycle_results.get(_AUTONOMOUS_CYCLE_PRODUCT_ID, (None, None))
    except Exception:
        await _rollback_active_session(db=db)
        logger.exception("autonomous_cycle_failed trigger=%s", autonomous_cycle_trigger)

    # The strategy roster must run, and its StrategyRosterRun row must be
    # committed, before campaign orchestration composes this candle -- the
    # aggregator resolves the roster run by an exact (asset, provider,
    # product, interval, candle_close_time, trigger) match and never falls
    # back to "latest", so composing first always sees no matching run yet
    # and skips with strategy_aggregate_skipped reason=exact_roster_run_unavailable.
    # Looped per product and isolated per product: one asset's roster
    # failure is logged and skipped, never blocking the others.
    for product_id in autonomous_cycle_products:
        try:
            scheduled_cycle_id, identity = cycle_results.get(product_id, (None, None))
            if identity is None and product_id == _AUTONOMOUS_CYCLE_PRODUCT_ID:
                # Delegates to the original, independently mockable loader
                # for the BTC fallback path specifically -- same reasoning
                # as _trigger_autonomous_cycles_for_products above.
                identity = _capture_kraken_btc_candle_identity(await _load_latest_kraken_btc_15m_candle(db))
            elif identity is None:
                symbols = _asset_symbols_for_product(product_id=product_id)
                identity = _capture_kraken_btc_candle_identity(
                    await _load_latest_kraken_asset_15m_candle(db, product_id=product_id, symbols=symbols)
                )
            if identity is None:
                continue
            await run_strategy_roster_for_candle(
                db=db,
                request=StrategyRosterRequest(
                    asset_id=identity.asset_id,
                    provider=_AUTONOMOUS_CYCLE_PROVIDER,
                    product_id=product_id,
                    interval=_AUTONOMOUS_CYCLE_INTERVAL,
                    candle_open_time=identity.open_time,
                    candle_close_time=identity.close_time,
                    trigger=autonomous_cycle_trigger,
                    scheduled_cycle_id=scheduled_cycle_id,
                ),
            )
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception(
                "strategy_roster_failed trigger=%s provider=%s product_id=%s interval=%s",
                autonomous_cycle_trigger,
                _AUTONOMOUS_CYCLE_PROVIDER,
                product_id,
                _AUTONOMOUS_CYCLE_INTERVAL,
            )

    if all(hasattr(db, attr) for attr in ("execute", "scalar", "commit")):
        try:
            orchestration_payload = await run_campaign_orchestration_preview_for_candle(
                db=db,
                trigger=autonomous_cycle_trigger,
            )
            payload = orchestration_payload if isinstance(orchestration_payload, dict) else {}
            cycle_count = int(payload.get("cycle_count") or 0)
            preview_reason = str(payload.get("reason") or "")
            considered_campaigns = payload.get("considered_campaigns") if isinstance(payload.get("considered_campaigns"), list) else []
            eligible_campaigns = payload.get("eligible_campaigns") if isinstance(payload.get("eligible_campaigns"), list) else []
            skipped_campaigns = payload.get("skipped_campaigns") if isinstance(payload.get("skipped_campaigns"), list) else []
            logger.info(
                "campaign_orchestration_preview_result trigger=%s roster_products=%s resolved_candle_id=%s resolved_candle_symbol=%s resolved_candle_product=%s resolved_candle_provider=%s resolved_candle_interval=%s resolved_candle_close_time=%s preview_reason=%s cycle_count=%s considered_campaigns=%s eligible_campaigns=%s skipped_campaigns=%s",
                autonomous_cycle_trigger,
                ",".join(autonomous_cycle_products),
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
                    autonomous_cycle_trigger,
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
                originating_autonomous_cycle_id=autonomous_cycle_id,
                autonomous_cycle_ids_by_product={
                    product_id: cycle_id for product_id, (cycle_id, _identity) in cycle_results.items()
                },
            )
            await db.commit()
        except ScorecardSessionRecoveryError:
            await _rollback_active_session(db=db)
            logger.exception(
                "campaign_orchestration_session_unrecoverable trigger=%s session_replaced_next_cycle=true",
                _AUTONOMOUS_CYCLE_TRIGGER,
            )
            raise
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("campaign_orchestration_failed trigger=%s", _AUTONOMOUS_CYCLE_TRIGGER)


_SENSITIVE_BOUND_PARAM_KEY_FRAGMENTS = (
    "password", "secret", "token", "api_key", "apikey", "credential", "authorization",
)


def _exception_qualname(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


def _redact_bound_params(params: object) -> object:
    """Best-effort secret-safe echo of DBAPIError.params for diagnostics.

    Controlled Proof persistence never binds credentials, but this guards
    against ever logging a sensitive value should one appear in a bound
    parameter set in the future.
    """
    def _redact_mapping(mapping: dict) -> dict:
        return {
            key: "***REDACTED***"
            if any(fragment in str(key).lower() for fragment in _SENSITIVE_BOUND_PARAM_KEY_FRAGMENTS)
            else value
            for key, value in mapping.items()
        }

    if isinstance(params, dict):
        return _redact_mapping(params)
    if isinstance(params, (list, tuple)):
        return [_redact_mapping(item) if isinstance(item, dict) else item for item in params]
    return params


# CanonicalPreviewPackage.package_state values a linked SELL package can
# still legitimately progress from -- i.e. it has not yet reached a claim
# (still PACKAGE_ONLY lineage) and has not been terminally resolved.
# EXPIRED/INVALIDATED/SUPERSEDED/FAILED_CLOSED/COMPLETED are deliberately
# excluded: those require the same operator-authorized exit-recovery
# supersession this fix does not change.
_SELL_PACKAGE_PROGRESSION_RETRYABLE_STATES = {
    "CREATED", "READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED",
}


async def _attempt_operator_controlled_proof_entry(
    *, db: AsyncSession, proof_id: uuid.UUID | None = None,
    recovery_id: uuid.UUID | None = None,
) -> None:
    """Build one Controlled Proof candidate without an autonomous action.

    This is the trigger/candidate seam; everything from canonical package
    creation onward is the same package/mandate/activation/execution path
    used by automation.  Every pre-package stop is persisted on the proof.
    """
    recovery = None
    if recovery_id is not None:
        claimed_recovery = await claim_exit_recovery_by_id(db=db, recovery_id=recovery_id)
        if claimed_recovery is None:
            return
        recovery, proof = claimed_recovery
        proof_id = proof.proof_id
    else:
        if proof_id is None:
            return
        proof = await claim_controlled_proof_by_id(db=db, proof_id=proof_id)
        if proof is None:
            return
    actor = "system:controlled_proof_worker"
    stage = "claimed"
    is_sell: bool | None = None
    package_id: uuid.UUID | None = None
    async def _record_wait(reason: str) -> None:
        if recovery is not None:
            await record_exit_recovery_waiting(db=db, recovery=recovery, reason=reason)
        else:
            await record_controlled_proof_waiting(db=db, proof=proof, reason=reason, actor=actor)

    async def _record_block(reason: str) -> None:
        if recovery is not None:
            await block_exit_recovery(db=db, recovery=recovery, reason=reason)
        else:
            await block_controlled_proof(db=db, proof=proof, reason=reason, actor=actor)

    async def _progress_package(*, package_id: uuid.UUID, decision_record_id: uuid.UUID) -> None:
        progression = await execute_automatic_ready_package_through_activation(
            db=db,
            request=AutomaticPackageExecutionRequest(
                campaign_id=proof.campaign_id, campaign_version=proof.campaign_version,
                decision_record_id=decision_record_id, package_id=package_id,
            ),
        )
        if progression.activation_state == "ACTIVATED" and not progression.failed_closed:
            claim_outcome = await claim_activated_package(db=db, package_id=package_id)
            logger.info(
                "controlled_proof_execution_claim_outcome proof_id=%s package_id=%s claim_id=%s "
                "claim_created=%s reason=%s provider_call_made=false",
                proof.proof_id, package_id,
                None if claim_outcome.claim is None else claim_outcome.claim.claim_id,
                claim_outcome.created, claim_outcome.reason_code,
            )
            if claim_outcome.claim is not None:
                await advance_claimed_execution(db=db, claim=claim_outcome.claim)
        else:
            # execute_automatic_ready_package_through_activation did not
            # report a clean ACTIVATED outcome, so nothing further in this
            # branch ever runs (no claim, no advance, no provider call).
            logger.info(
                "controlled_proof_package_activation_not_achieved proof_id=%s package_id=%s "
                "activation_state=%s failed_closed=%s final_reason_code=%s starting_state=%s",
                proof.proof_id, package_id, progression.activation_state,
                progression.failed_closed, progression.final_reason_code, progression.starting_state,
            )
            # A claimed Exit Recovery must never fall through to db.commit()
            # here with zero recorded reason -- previously it did, leaving
            # an IN_PROGRESS recovery indistinguishable from one that is
            # simply mid-flight (confirmed production incident: dispatch
            # completed, recovery stayed IN_PROGRESS with no blocked_reason
            # or failure_reason). progression.failed_closed distinguishes a
            # definitive, config/scope-level stop (e.g. a mandate/campaign
            # scope mismatch -- not retryable without an operator fixing
            # the underlying condition or issuing a fresh authorization)
            # from a softer non-achievement this exact recovery may still
            # resolve on a later cycle within its own bounded, already-
            # authorized expiry window -- the same distinction failed_closed
            # already draws for every other caller of this executor.
            # Ordinary (non-recovery) Controlled Proof progression is
            # unchanged: this block only ever touches the recovery's own
            # state, never proof.status or proof.failure_reason.
            if recovery is not None:
                if progression.failed_closed:
                    await _record_block(f"activation_failed_closed:{progression.final_reason_code}")
                else:
                    await _record_wait(f"activation_not_achieved:{progression.final_reason_code}")
        await db.commit()
    logger.info(
        "controlled_proof_selected_for_evaluation proof_id=%s proof_status=%s "
        "buy_package_id=%s sell_package_id=%s",
        proof.proof_id, proof.status, proof.package_id, proof.sell_package_id,
    )
    try:
        if proof.sell_package_id is not None:
            # Supervision is part of the periodic operator workflow, not a
            # read-side accident: refresh terminal reconciliation/P&L state
            # even when no operator polls the HTTP view.
            stage = "post_link_supervision_refresh"
            await get_controlled_proof_view(db=db, proof_id=proof.proof_id)
            if recovery is not None:
                await refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)
                await db.commit()
                if recovery.status == "IN_PROGRESS":
                    sell_package = await db.get(CanonicalPreviewPackage, proof.sell_package_id)
                    if sell_package is None or sell_package.decision_record_id is None:
                        await _record_block("linked_sell_package_unavailable")
                        await db.commit()
                        return
                    authorization_expires_at = sell_package.authorization_expires_at
                    normalized_authorization_expires_at = (
                        authorization_expires_at.replace(tzinfo=timezone.utc)
                        if authorization_expires_at is not None and authorization_expires_at.tzinfo is None
                        else authorization_expires_at
                    )
                    # Distinct staleness shapes, distinct supersession paths:
                    # an already-ACTIVATED package whose post-activation
                    # authorization window later elapsed (below) versus a
                    # package that never reached ACTIVATED at all before its
                    # own canonical preview window expired -- the latter is
                    # a deterministic, permanent dead end for _progress_
                    # package (authorize_canonical_preview_package_under_
                    # mandate/run_dry_run_for_canonical_preview_package both
                    # fail closed on preview_expires_at <= now with no path
                    # to recovery), so it must also be reissued fresh rather
                    # than retried.
                    preview_expired_before_activation = (
                        sell_package.package_state in {"READY", "AUTHORIZED", "DRY_RUN_PASSED"}
                        and sell_package.preview_expires_at <= datetime.now(timezone.utc)
                    )
                    if (
                        normalized_authorization_expires_at is not None
                        and normalized_authorization_expires_at <= datetime.now(timezone.utc)
                    ):
                        try:
                            await supersede_stale_exit_recovery_sell_package(
                                db=db, recovery=recovery, proof=proof, package=sell_package,
                            )
                            await db.commit()
                            logger.info(
                                "controlled_proof_exit_recovery_fresh_sell_authority_started "
                                "proof_id=%s recovery_id=%s superseded_package_id=%s side=SELL reason=authorization_expired",
                                proof.proof_id, recovery.recovery_id, sell_package.package_id,
                            )
                        except InvalidRequestError as exc:
                            await _record_block(f"stale_sell_package_replacement_blocked:{exc.message}")
                            await db.commit()
                            return
                    elif preview_expired_before_activation:
                        try:
                            await supersede_expired_preview_exit_recovery_sell_package(
                                db=db, recovery=recovery, proof=proof, package=sell_package,
                            )
                            await db.commit()
                            logger.info(
                                "controlled_proof_exit_recovery_fresh_sell_authority_started "
                                "proof_id=%s recovery_id=%s superseded_package_id=%s side=SELL reason=preview_expired",
                                proof.proof_id, recovery.recovery_id, sell_package.package_id,
                            )
                        except InvalidRequestError as exc:
                            await _record_block(f"stale_sell_package_preview_replacement_blocked:{exc.message}")
                            await db.commit()
                            return
                    else:
                        await _progress_package(
                            package_id=sell_package.package_id,
                            decision_record_id=sell_package.decision_record_id,
                        )
                        return
            else:
                # Ordinary periodic supervision (no exit-recovery authority
                # in play). A linked SELL package whose first _progress_
                # package attempt did not reach ACTIVATED cleanly previously
                # had no path back to execution here -- it sat as PACKAGE_
                # ONLY forever, until an operator noticed and authorized
                # exit-recovery, or the proof simply expired (confirmed
                # production incident). Retry the same governed progression
                # exactly once per cycle, but only when every condition
                # below proves it is still safe and still this package's
                # job to do: the package itself is loaded fresh, its state
                # has not terminally resolved, its own authorization/
                # preview window has not expired, and canonical lineage
                # still shows no claim or order exists yet. Any other
                # lineage state (claimed, ordered, or genuinely
                # inconsistent) is left completely untouched -- retrying
                # progression is never a substitute for exit-recovery
                # supersession of a truly stale package.
                sell_package = await db.get(CanonicalPreviewPackage, proof.sell_package_id)
                if sell_package is not None and sell_package.package_state in _SELL_PACKAGE_PROGRESSION_RETRYABLE_STATES:
                    authorization_expires_at = sell_package.authorization_expires_at
                    normalized_authorization_expires_at = (
                        authorization_expires_at.replace(tzinfo=timezone.utc)
                        if authorization_expires_at is not None and authorization_expires_at.tzinfo is None
                        else authorization_expires_at
                    )
                    not_yet_expired = (
                        normalized_authorization_expires_at is None
                        or normalized_authorization_expires_at > datetime.now(timezone.utc)
                    )
                    if not_yet_expired:
                        sell_lineage = await resolve_controlled_proof_leg_execution_lineage(
                            db=db, proof=proof, package_id=proof.sell_package_id, side="SELL",
                        )
                        if sell_lineage.state == "PACKAGE_ONLY":
                            stage = "sell_package_progression_retry"
                            await _progress_package(
                                package_id=sell_package.package_id,
                                decision_record_id=sell_package.decision_record_id,
                            )
                return
        is_sell = recovery is not None or proof.package_id is not None
        if is_sell:
            logger.info(
                "controlled_proof_exit_evaluation_started proof_id=%s proof_status=%s "
                "buy_package_id=%s sell_package_id=%s",
                proof.proof_id, proof.status, proof.package_id, proof.sell_package_id,
            )
            stage = "sell_eligibility_check"
            sell_eligible = await should_propose_controlled_sell(db=db, proof=proof)
            if not sell_eligible:
                await _record_wait("sell_prerequisites_unmet")
                await db.commit()
                return
            logger.info(
                "controlled_proof_sell_eligible proof_id=%s proof_status=%s next_stage=sell_risk_evaluation",
                proof.proof_id, proof.status,
            )
        side = "SELL" if is_sell else "BUY"
        forced_action = "CLOSE_POSITION_PROPOSED" if is_sell else "OPEN_POSITION_PROPOSED"
        runtime = await _load_runtime_campaign(db=db, campaign_id=proof.campaign_id)
        if runtime is None or runtime.paper_account_id is None:
            await _record_wait("runtime_campaign_or_paper_account_missing")
            await db.commit()
            return
        profile = await _load_live_trading_profile_for_paper_account(db=db, paper_account_id=runtime.paper_account_id)
        if profile is None:
            await _record_wait("live_trading_profile_missing")
            await db.commit()
            return
        capital_blocker = None if is_sell else await controlled_proof_entry_blocker(db=db, proof=proof)
        if capital_blocker is not None:
            await _record_block(f"controlled_proof_entry_blocked:{capital_blocker}")
            await db.commit()
            return
        if await _has_open_live_order(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
            await _record_wait("open_live_order_exists")
            await db.commit()
            return
        if await _has_unresolved_reconciliation(db=db, provider=proof.provider, environment=proof.environment, product=proof.product_id):
            await _record_wait("unresolved_reconciliation_exists")
            await db.commit()
            return

        notional = min(Decimal(proof.max_notional_usd), _CANONICAL_READY_PACKAGE_AMOUNT)
        if notional != _CANONICAL_READY_PACKAGE_AMOUNT:
            await _record_block("controlled_proof_notional_below_canonical_bound")
            await db.commit()
            return
        stage = "risk_evaluation"
        risk = await evaluate_controlled_proof_risk(
            db=db, proof_id=proof.proof_id, campaign_id=proof.campaign_id,
            campaign_version=proof.campaign_version, paper_account_id=runtime.paper_account_id,
            product_id=proof.product_id, side=side, notional_usd=notional, actor=actor,
        )
        if risk.verdict == "DENY":
            await _record_block(f"controlled_proof_risk_denied:{risk.reason_code}")
            await db.commit()
            return
        if risk.verdict != "ALLOW":
            await _record_wait(f"controlled_proof_risk_{risk.verdict.lower()}:{risk.reason_code}")
            await db.commit()
            return

        settings = get_settings()
        # Deliberately NOT automatic_mandate_package_activation_mandate_id
        # (the ordinary production mandate setting): Controlled Proof pins
        # its own dedicated CONTROLLED_PROOF-purpose mandate so it never
        # competes with, or is blocked by, ordinary production's cumulative
        # daily_deployed_usd -- and so ordinary production can never be
        # governed by a mandate meant only for bounded $5 proofs.
        mandate_id = getattr(settings, "controlled_proof_mandate_id", None)
        if mandate_id is None:
            await _record_wait("controlled_proof_mandate_missing")
            await db.commit()
            return
        strategy_identity = await resolve_controlled_proof_strategy_identity(db=db, mandate_id=mandate_id)
        if not strategy_identity:
            await _record_wait("campaign_strategy_identity_missing")
            await db.commit()
            return
        stage = "decision_record_creation"
        decision_id = await create_controlled_proof_decision_record(
            db=db, campaign_id=proof.campaign_id, controlled_proof_id=proof.proof_id,
            forced_action=forced_action, product=proof.product_id,
            provider=proof.provider, actor=actor, strategy_identity=strategy_identity,
            controlled_proof_exit_recovery_id=None if recovery is None else recovery.recovery_id,
        )
        stage = "mandate_evaluation"
        controlled_proof_open_exposure_usd = await compute_controlled_proof_open_exposure_usd(
            db=db, live_trading_profile_id=profile.id,
        )
        evaluation = await evaluate_and_record_mandate(
            db=db,
            request=MandateEvaluationWriteRequest(
                mandate_id=mandate_id, actor=actor, strategy_version=strategy_identity,
                product=proof.product_id, side=side, proposed_notional_usd=notional,
                current_open_exposure_usd=Decimal("0"), daily_deployed_usd=Decimal("0"),
                daily_realized_loss_usd=Decimal("0"), campaign_drawdown_usd=Decimal("0"),
                consecutive_losses=0, current_position_count=0, risk_verdict="ACCEPTED",
                evidence_age_seconds=0, kill_switch_engaged=False,
                observed_at=datetime.now(timezone.utc), decision_id=decision_id,
                request_context={"purpose": "controlled_proof", "controlled_proof_id": str(proof.proof_id)},
                idempotency_key=(
                    f"controlled-proof-mandate-eval:{proof.proof_id}:{side}:exit-recovery:{recovery.recovery_id}:decision:{decision_id}"
                    if recovery is not None
                    else f"controlled-proof-mandate-eval:{proof.proof_id}:{side}"
                ),
                audit_correlation_id=proof.audit_correlation_id, software_build_version=None,
                expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF,
                controlled_proof_open_exposure_usd=controlled_proof_open_exposure_usd,
            ),
        )
        if evaluation.authorization_result != "AUTHORIZED":
            await _record_block("controlled_proof_mandate_not_authorized")
            await db.commit()
            return

        package_identity = (
            f"controlled-proof:{proof.proof_id}:{side}:exit-recovery:{recovery.recovery_id}"
            if recovery is not None
            else f"controlled-proof:{proof.proof_id}:{side}"
        )
        package_key = hashlib.sha256(package_identity.encode()).hexdigest()
        stage = "package_creation"
        payload = await create_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageCreateRequest(
                campaign_id=proof.campaign_id, campaign_version=proof.campaign_version,
                paper_account_id=runtime.paper_account_id, live_trading_profile_id=profile.id,
                provider=proof.provider, environment=proof.environment, product=proof.product_id,
                max_proposed_order_amount=notional, actor=actor, idempotency_key=package_key,
                expected_decision_record_id=decision_id, mandate_id=evaluation.mandate_id,
                mandate_version_id=evaluation.mandate_version_id,
                mandate_evaluation_id=evaluation.evaluation_id,
                commissioning_entry_mode="controlled_proof", forced_action=forced_action,
                controlled_proof_id=proof.proof_id,
                controlled_proof_exit_recovery_id=None if recovery is None else recovery.recovery_id,
            ),
        )
        package_payload = payload.get("package") if isinstance(payload, dict) else None
        package_id = uuid.UUID(str(package_payload["package_id"])) if isinstance(package_payload, dict) and package_payload.get("package_id") else None
        if package_id is None:
            await _record_wait("canonical_package_unavailable")
            await db.commit()
            return
        if is_sell:
            stage = "sell_package_linking"
            await link_controlled_proof_sell_package(
                db=db, proof=proof, sell_package_id=package_id,
                preserve_terminal_status=recovery is not None,
            )
        else:
            stage = "entry_linking"
            await link_controlled_proof_entry(
                db=db, proof=proof, decision_record_id=decision_id, mandate_id=evaluation.mandate_id,
                mandate_version_id=evaluation.mandate_version_id, mandate_evaluation_id=evaluation.evaluation_id,
            )
            await link_controlled_proof_package(db=db, proof=proof, package_id=package_id)
        stage = "post_link_commit"
        await db.commit()

        stage = "package_activation_progression"
        await _progress_package(package_id=package_id, decision_record_id=decision_id)
    except Exception as exc:
        await _rollback_active_session(db=db)
        if recovery_id is not None:
            claimed_recovery = await claim_exit_recovery_by_id(db=db, recovery_id=recovery_id)
            if claimed_recovery is not None:
                recovery, _proof = claimed_recovery
                if isinstance(exc, IntegrityError):
                    await block_exit_recovery(
                        db=db, recovery=recovery,
                        reason="fresh_authority_persistence_integrity_failure",
                    )
                elif isinstance(exc, (LookupError, InvalidRequestError)):
                    await block_exit_recovery(
                        db=db, recovery=recovery,
                        reason="fresh_authority_evidence_validation_failure",
                    )
                else:
                    await record_exit_recovery_waiting(
                        db=db, recovery=recovery,
                        reason=f"entry_attempt_failed:{exc.__class__.__name__}",
                    )
                await db.commit()
        elif proof_id is not None:
            proof = await claim_controlled_proof_by_id(db=db, proof_id=proof_id)
        if recovery_id is None and proof is not None:
            await record_controlled_proof_waiting(
                db=db, proof=proof, reason=f"entry_attempt_failed:{exc.__class__.__name__}", actor=actor,
            )
            await db.commit()
        orig_exc = getattr(exc, "orig", None)
        sell_package_id_for_log = (
            package_id if (is_sell and package_id is not None)
            else (getattr(proof, "sell_package_id", None) if proof is not None else None)
        )
        logger.exception(
            "controlled_proof_entry_attempt_failed proof_id=%s recovery_id=%s stage=%s "
            "controlled_proof_run_id=%s sell_package_id=%s position_id=%s "
            "exception_class=%s exception_message=%s "
            "orig_exception_class=%s orig_exception_message=%s "
            "sql_statement=%s sql_params=%s",
            proof_id,
            recovery_id,
            stage,
            proof_id,
            sell_package_id_for_log,
            getattr(proof, "position_id", None) if proof is not None else None,
            _exception_qualname(exc),
            str(exc),
            _exception_qualname(orig_exc),
            str(orig_exc) if orig_exc is not None else None,
            getattr(exc, "statement", None) if isinstance(exc, DBAPIError) else None,
            _redact_bound_params(getattr(exc, "params", None)) if isinstance(exc, DBAPIError) else None,
        )


async def dispatch_controlled_proof_immediate_attempt(*, proof_id: uuid.UUID) -> None:
    """Operator-triggered immediate acceleration for a just-ACCEPTED
    Controlled Proof: runs exactly the same composition+package-attempt
    path a regular poll would (see
    _run_autonomous_and_campaign_orchestration_attempt) in a fresh,
    independent session, without waiting for the next candle-close poll.

    Best-effort, not a correctness dependency: on any failure here
    (including this task never running at all, e.g. a process restart),
    the regular timer-driven poll -- entirely unaffected by this function
    -- will still discover and process the proof on its own normal
    cadence. This function only ever makes that happen sooner; it never
    changes whether it eventually happens.

    Never bypasses any gate: this calls the same operator-candidate function
    the timer loop calls, so fresh risk evaluation, mandate authorization,
    activation authorization, and audit persistence all run for real.
    Claim-level idempotency (SELECT ... FOR UPDATE in
    claim_controlled_proof_by_id) and every downstream
    idempotency key already guarantee a concurrent or duplicate dispatch
    can never claim, package, or activate the same proof twice -- this
    function adds no new locking because none is needed.
    """
    logger.info("controlled_proof_dispatch_started proof_id=%s", proof_id)
    try:
        async with AsyncSessionLocal() as db:
            await _attempt_operator_controlled_proof_entry(db=db, proof_id=proof_id)
    except Exception:
        logger.exception("controlled_proof_dispatch_failed proof_id=%s", proof_id)
        return
    logger.info("controlled_proof_dispatch_completed proof_id=%s", proof_id)


# Holds strong references to in-flight dispatch tasks so they are never
# garbage-collected mid-run (a well-known asyncio pitfall for fire-and-
# forget tasks with no other referrer) -- each task removes itself on
# completion via its own done-callback.
_controlled_proof_dispatch_tasks: set[asyncio.Task] = set()


def schedule_controlled_proof_immediate_dispatch(*, proof_id: uuid.UUID) -> None:
    """Fire-and-forget scheduling for dispatch_controlled_proof_immediate_
    attempt. Must only be called after the caller's own transaction that
    created/accepted the proof has already committed -- calling this
    before that commit risks the dispatch's own fresh session racing the
    still-uncommitted proof row under READ COMMITTED isolation and simply
    finding nothing (see the call site in app.api.routes.operator_actions
    for why it is scheduled there, after submit_operator_action returns,
    rather than inside the RUN_CONTROLLED_PROOF handler itself)."""
    task = asyncio.create_task(dispatch_controlled_proof_immediate_attempt(proof_id=proof_id))
    _controlled_proof_dispatch_tasks.add(task)
    task.add_done_callback(_controlled_proof_dispatch_tasks.discard)


async def dispatch_controlled_proof_exit_recovery_attempt(*, proof_id: uuid.UUID) -> None:
    logger.info("controlled_proof_exit_recovery_dispatch_started proof_id=%s", proof_id)
    try:
        async with AsyncSessionLocal() as db:
            view = await get_exit_recovery_view(db=db, proof_id=proof_id)
            await _attempt_operator_controlled_proof_entry(db=db, recovery_id=view["recovery_id"])
    except Exception:
        logger.exception("controlled_proof_exit_recovery_dispatch_failed proof_id=%s", proof_id)
        return
    logger.info("controlled_proof_exit_recovery_dispatch_completed proof_id=%s", proof_id)


def schedule_controlled_proof_exit_recovery_dispatch(*, proof_id: uuid.UUID) -> None:
    task = asyncio.create_task(dispatch_controlled_proof_exit_recovery_attempt(proof_id=proof_id))
    _controlled_proof_dispatch_tasks.add(task)
    task.add_done_callback(_controlled_proof_dispatch_tasks.discard)


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

    # Recovery pass for durable autonomous execution claims, deliberately
    # independent of this cycle's own decision composition -- see
    # sweep_stale_autonomous_execution_claims. Without this, a claim whose
    # originating decision_record_id never recurs (e.g. a Controlled-Proof-
    # forced one-shot entry) is never revisited again by anything below.
    if hasattr(db, "scalars") and hasattr(db, "scalar") and hasattr(db, "commit"):
        try:
            swept = await sweep_stale_autonomous_execution_claims(db=db)
            if swept > 0:
                logger.info("autonomous_execution_claim_sweep_completed swept=%s", swept)
            await db.commit()
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("autonomous_execution_claim_sweep_cycle_failed")

    # Automatic reconciliation, run before this cycle's own orchestration
    # attempt below so a fill discovered here (e.g. a Controlled Proof BUY
    # that just filled) is already visible to _has_unresolved_reconciliation
    # and should_propose_controlled_sell within the SAME cycle, not only
    # starting from the next one. Independent of decision composition, same
    # defensive hasattr guards as the claim sweep above (many existing
    # tests call this cycle with a bare fake db).
    if hasattr(db, "scalars") and hasattr(db, "scalar") and hasattr(db, "commit"):
        try:
            poll_outcome = await poll_unresolved_live_orders(db=db)
            if poll_outcome.candidates_discovered > 0:
                logger.info(
                    "live_order_reconciliation_cycle_completed candidates=%s reconciled=%s still_pending=%s failed=%s",
                    poll_outcome.candidates_discovered, poll_outcome.reconciled,
                    poll_outcome.still_pending, poll_outcome.failed,
                )
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("live_order_reconciliation_cycle_failed")

    # Recovered-outcome backfill sweep: its own guarded call site,
    # deliberately independent of live-order reconciliation candidate
    # count (poll_unresolved_live_orders above finds zero candidates for
    # an order that already reconciled before this process started) and
    # of whatever happens in ingestion/venue-commissioning/claim-sweep
    # earlier in this cycle -- a transient failure there must never
    # silently prevent an already-proven, already-published recovered
    # outcome from being projected onto its stuck proof. Reuses the
    # existing, fully idempotent projector/validation unchanged; see
    # project_blocked_exit_recovery_outcome.
    if hasattr(db, "scalars") and hasattr(db, "scalar") and hasattr(db, "commit"):
        try:
            await refresh_exit_recovery_outcomes(db=db)
            await db.commit()
        except Exception:
            await _rollback_active_session(db=db)
            logger.exception("recovered_exit_recovery_outcome_sweep_failed")

    await _run_autonomous_and_campaign_orchestration_attempt(db=db)

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
