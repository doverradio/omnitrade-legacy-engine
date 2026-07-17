from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.paper_account import PaperAccount
from app.schemas.operations import (
    LiveCryptoReadinessItemResponse,
    LiveCryptoReadinessResponse,
    OperationalAlertResponse,
    OperationalFreshnessItemResponse,
    OperationalFreshnessResponse,
    OperationalHealthIndicatorResponse,
    OperationalMonitoringResponse,
    OperationalRunStatusResponse,
    OperationalStatusResponse,
    RuntimeReadinessCycleSnapshotResponse,
    RuntimeReadinessHealthResponse,
    RuntimeReadinessResponse,
)
from app.services.data.ingestion_status import get_last_successful_full_pipeline_at, get_last_successful_ingestion_at
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.services.exchange_connections.providers.registry import list_registered_exchange_providers
from app.services.live_crypto_environment import inspect_live_crypto_environment
from app.services.paper.accounting import build_account_snapshot
from app.services.research_agents.openai.registry import get_openai_research_agent

_RUN_ID = str(uuid.uuid4())
_STARTED_AT = datetime.now(timezone.utc)
_EXPECTED_END = _STARTED_AT + timedelta(hours=72)


def compute_uptime(*, started_at: datetime, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    elapsed = current - started_at
    if elapsed.total_seconds() < 0:
        elapsed = timedelta(0)

    total_seconds = int(elapsed.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def _latest_dry_run_status(*, db: AsyncSession, provider: str, environment: str) -> str | None:
    row = await db.scalar(
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.status.in_(["DRY_RUN_READY", "DRY_RUN_BLOCKED"]))
        .order_by(LiveCryptoOrder.created_at.desc())
        .limit(1)
    )
    return None if row is None else str(row.status)


async def build_operations_status(*, db: AsyncSession) -> OperationalStatusResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    run_uptime = compute_uptime(started_at=_STARTED_AT, now=now)

    try:
        await db.execute(text("SELECT 1"))
        db_connected = True
    except Exception:
        db_connected = False

    alerts: list[OperationalAlertResponse] = []
    if not db_connected:
        live_crypto_readiness = LiveCryptoReadinessResponse(
            ready=False,
            items=[
                LiveCryptoReadinessItemResponse(
                    key="database",
                    label="Database",
                    ready=False,
                    detail="Database unavailable",
                )
            ],
        )
        alerts.append(
            OperationalAlertResponse(
                code="database_unavailable",
                severity="red",
                message="Database unavailable",
            )
        )
        run_status = OperationalRunStatusResponse(
            run_id=_RUN_ID,
            started_at=_STARTED_AT,
            expected_end=_EXPECTED_END,
            uptime=run_uptime,
            current_phase="degraded",
            health_status="red",
        )
        return OperationalStatusResponse(
            overall_health="red",
            run_status=run_status,
            system_health={
                "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
                "orchestrator": OperationalHealthIndicatorResponse(state="yellow", detail="Awaiting heartbeat"),
                "database": OperationalHealthIndicatorResponse(state="red", detail="Database unavailable"),
                "research_agent": OperationalHealthIndicatorResponse(state="yellow", detail="Unknown"),
            },
            research_status={
                "current_campaign": None,
                "current_champion": None,
                "campaign_status": "UNKNOWN",
            },
            monitoring=OperationalMonitoringResponse(
                candles_processed=0,
                signals_generated=0,
                paper_trades_executed=0,
                decision_records_created=0,
                replay_count=0,
                candidate_count=0,
                campaign_count=0,
                laboratory_runs=0,
                evolution_count=0,
                current_champion=None,
                paper_equity="0",
                signals_today=0,
                trades_today=0,
                research_memory_growth=0,
            ),
            live_crypto_readiness=live_crypto_readiness,
            alerts=alerts,
        )

    try:
        provider_states: list[tuple[str, str, object]] = []
        for metadata in list_registered_exchange_providers():
            provider_states.append((metadata.provider_key, "production", await inspect_live_crypto_environment(db=db, provider=metadata.provider_key, exchange_environment="production")))
            provider_states.append((metadata.provider_key, "sandbox", await inspect_live_crypto_environment(db=db, provider=metadata.provider_key, exchange_environment="sandbox")))

        combined_items: list[LiveCryptoReadinessItemResponse] = []
        any_production_ready = False
        for provider_key, environment, state in provider_states:
            any_production_ready = any_production_ready or (environment == "production" and bool(state.ready))
            combined_items.extend(
                LiveCryptoReadinessItemResponse(
                    key=f"{provider_key}_{environment}_{item.key}",
                    label=f"{provider_key} {environment.title()} {item.label}",
                    ready=item.ready,
                    detail=item.detail,
                )
                for item in state.items
            )
            dry_run_status = await _latest_dry_run_status(db=db, provider=provider_key, environment=environment)
            if environment == "production":
                combined_items.append(
                    LiveCryptoReadinessItemResponse(
                        key=f"{provider_key}_production_account_status",
                        label=f"{provider_key} Production Account Status",
                        ready=state.exchange_connection_id is not None,
                        detail=(
                            f"{provider_key} production account connection detected"
                            if state.exchange_connection_id is not None
                            else f"{provider_key} production account unavailable or not initialized"
                        ),
                    )
                )
                combined_items.append(
                    LiveCryptoReadinessItemResponse(
                        key=f"{provider_key}_production_dry_run_executed",
                        label=f"{provider_key} Production Dry Run Executed",
                        ready=dry_run_status == "DRY_RUN_READY",
                        detail=(
                            f"Latest {provider_key} production dry run result: {dry_run_status}"
                            if dry_run_status is not None
                            else f"{provider_key} production dry run not executed"
                        ),
                    )
                )
            else:
                combined_items.append(
                    LiveCryptoReadinessItemResponse(
                        key=f"{provider_key}_sandbox_rehearsal_result",
                        label=f"{provider_key} Sandbox Rehearsal Result",
                        ready=dry_run_status == "DRY_RUN_READY",
                        detail=(
                            f"Latest {provider_key} sandbox/mock rehearsal result: {dry_run_status}"
                            if dry_run_status is not None
                            else f"{provider_key} sandbox/mock rehearsal not executed"
                        ),
                    )
                )

        live_crypto_readiness = LiveCryptoReadinessResponse(
            ready=any_production_ready,
            items=combined_items,
        )
    except Exception as exc:
        live_crypto_readiness = LiveCryptoReadinessResponse(
            ready=False,
            items=[
                LiveCryptoReadinessItemResponse(
                    key="initializer",
                    label="Initializer",
                    ready=False,
                    detail=f"Initialization readiness unavailable: {str(exc)}",
                )
            ],
        )
    if not live_crypto_readiness.ready:
        alerts.append(
            OperationalAlertResponse(
                code="live_crypto_not_ready",
                severity="yellow",
                message="Live crypto dry-run prerequisites are incomplete",
            )
        )
    if any(item.key.endswith("sandbox_rehearsal_result") and item.ready for item in live_crypto_readiness.items):
        alerts.append(
            OperationalAlertResponse(
                code="sandbox_rehearsal_non_production",
                severity="yellow",
                message="Sandbox/provider-mock rehearsal passed separately; production readiness remains unchanged",
            )
        )

    candles_processed = await _count(db=db, sql="SELECT COUNT(*) FROM candles")
    signals_generated = await _count(db=db, sql="SELECT COUNT(*) FROM signals")
    paper_trades_executed = await _count(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true")
    decision_records_created = await _count(db=db, sql="SELECT COUNT(*) FROM decision_records")
    replay_count = await _count(db=db, sql="SELECT COUNT(*) FROM decision_quality_scores")
    candidate_count = await _count(db=db, sql="SELECT COUNT(*) FROM research_candidates")
    campaign_count = await _count(db=db, sql="SELECT COUNT(*) FROM research_campaigns")
    laboratory_runs = await _count(db=db, sql="SELECT COUNT(*) FROM research_laboratory_runs")
    evolution_count = await _count(db=db, sql="SELECT COUNT(*) FROM research_candidate_lineage")
    research_memory_growth = await _count(db=db, sql="SELECT COUNT(*) FROM research_memory_entries")

    current_campaign_row = await db.execute(
        text(
            "SELECT name, status, updated_at "
            "FROM research_campaigns "
            "ORDER BY updated_at DESC NULLS LAST "
            "LIMIT 1"
        )
    )
    current_campaign = current_campaign_row.mappings().first()

    current_champion_row = await db.execute(
        text(
            "SELECT current_champion "
            "FROM research_campaign_statistics "
            "WHERE current_champion IS NOT NULL "
            "ORDER BY updated_at DESC NULLS LAST "
            "LIMIT 1"
        )
    )
    current_champion_record = current_champion_row.mappings().first()
    current_champion = None if current_champion_record is None else current_champion_record.get("current_champion")

    latest_research_cycle = await _load_latest_research_cycle_status(db=db)
    recent_research_failures = await _count_recent_research_failures(db=db, now=now)

    paper_equity = await _get_paper_equity(db=db)

    last_candle_at = await _max_timestamp(db=db, sql="SELECT MAX(close_time) FROM candles")
    last_signal_at = await _max_timestamp(db=db, sql="SELECT MAX(signal_time) FROM signals")
    last_trade_at = await _max_timestamp(db=db, sql="SELECT MAX(executed_at) FROM trades WHERE is_paper = true")
    last_ingestion_at = get_last_successful_ingestion_at()
    last_full_pipeline_at = await _load_last_successful_full_pipeline_at(db=db) or get_last_successful_full_pipeline_at()
    orchestrator_heartbeat_at = last_full_pipeline_at or last_ingestion_at

    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    signals_today = await _count(
        db=db,
        sql="SELECT COUNT(*) FROM signals WHERE created_at >= :day_start",
        params={"day_start": day_start},
    )
    trades_today = await _count(
        db=db,
        sql="SELECT COUNT(*) FROM trades WHERE is_paper = true AND created_at >= :day_start",
        params={"day_start": day_start},
    )

    orchestrator_state, orchestrator_detail = _resolve_orchestrator_state(now=now, last_ingestion_at=orchestrator_heartbeat_at)
    if orchestrator_state == "red":
        alerts.append(
            OperationalAlertResponse(
                code="worker_stopped",
                severity="red",
                message="Worker stopped",
            )
        )

    _append_staleness_alert(
        alerts=alerts,
        code="no_new_candles",
        message="No new candles",
        now=now,
        last_seen_at=last_candle_at,
        threshold=timedelta(minutes=25),
        total_count=candles_processed,
    )
    _append_staleness_alert(
        alerts=alerts,
        code="no_new_signals",
        message="No new signals",
        now=now,
        last_seen_at=last_signal_at,
        threshold=timedelta(minutes=45),
        total_count=signals_generated,
    )
    _append_staleness_alert(
        alerts=alerts,
        code="no_new_trades",
        message="No new trades",
        now=now,
        last_seen_at=last_trade_at,
        threshold=timedelta(hours=6),
        total_count=paper_trades_executed,
    )

    if current_campaign is not None and current_campaign.get("status") == "RUNNING":
        updated_at = current_campaign.get("updated_at")
        if isinstance(updated_at, datetime) and (now - updated_at.astimezone(timezone.utc)) > timedelta(minutes=60):
            alerts.append(
                OperationalAlertResponse(
                    code="campaign_stalled",
                    severity="yellow",
                    message="Campaign stalled",
                )
            )

    openai_available = get_openai_research_agent().is_available
    if not openai_available:
        alerts.append(
            OperationalAlertResponse(
                code="research_agent_unavailable",
                severity="yellow",
                message="Research agent unavailable",
            )
        )

    if recent_research_failures > 0:
        alerts.append(
            OperationalAlertResponse(
                code="research_cycle_failures",
                severity="yellow",
                message=f"Research cycle failures detected ({recent_research_failures} recent)",
            )
        )

    overall_health = _resolve_health(alerts=alerts)
    current_phase = _resolve_phase(
        candles_processed=candles_processed,
        signals_generated=signals_generated,
        paper_trades_executed=paper_trades_executed,
        campaign_count=campaign_count,
        laboratory_runs=laboratory_runs,
        evolution_count=evolution_count,
    )

    run_status = OperationalRunStatusResponse(
        run_id=_RUN_ID,
        started_at=_STARTED_AT,
        expected_end=_EXPECTED_END,
        uptime=run_uptime,
        current_phase=current_phase,
        health_status=overall_health,
    )

    return OperationalStatusResponse(
        overall_health=overall_health,
        run_status=run_status,
        system_health={
            "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
            "orchestrator": OperationalHealthIndicatorResponse(state=orchestrator_state, detail=orchestrator_detail),
            "database": OperationalHealthIndicatorResponse(state="green", detail="Database connected"),
            "research_agent": OperationalHealthIndicatorResponse(
                state="green" if openai_available else "yellow",
                detail="OpenAI research adapter available" if openai_available else "OpenAI research adapter unavailable",
            ),
        },
        research_status={
            "current_campaign": None if current_campaign is None else str(current_campaign.get("name")),
            "current_champion": current_champion,
            "campaign_status": "IDLE" if current_campaign is None else str(current_campaign.get("status")),
            "feature_state": "enabled" if settings.research_evolution_enabled else "disabled",
            "last_cycle_status": None if latest_research_cycle is None else str(latest_research_cycle.get("status")),
            "last_cycle_reason": None if latest_research_cycle is None else str(latest_research_cycle.get("reason")),
            "last_cycle_at": None if latest_research_cycle is None else str(latest_research_cycle.get("recorded_at")),
            "recent_failure_count": recent_research_failures,
        },
        monitoring=OperationalMonitoringResponse(
            candles_processed=candles_processed,
            signals_generated=signals_generated,
            paper_trades_executed=paper_trades_executed,
            decision_records_created=decision_records_created,
            replay_count=replay_count,
            candidate_count=candidate_count,
            campaign_count=campaign_count,
            laboratory_runs=laboratory_runs,
            evolution_count=evolution_count,
            current_champion=current_champion,
            paper_equity=_decimal_to_string(paper_equity),
            signals_today=signals_today,
            trades_today=trades_today,
            research_memory_growth=research_memory_growth,
        ),
        live_crypto_readiness=live_crypto_readiness,
        alerts=alerts,
    )


async def build_operational_freshness(*, db: AsyncSession) -> OperationalFreshnessResponse:
    generated_at = datetime.now(timezone.utc)
    return OperationalFreshnessResponse(
        generated_at=generated_at,
        items=[
            OperationalFreshnessItemResponse(
                source="candles",
                latest_timestamp=await _max_timestamp(db=db, sql="SELECT MAX(close_time) FROM candles"),
                row_count=await _count(db=db, sql="SELECT COUNT(*) FROM candles"),
            ),
            OperationalFreshnessItemResponse(
                source="signals",
                latest_timestamp=await _max_timestamp(db=db, sql="SELECT MAX(signal_time) FROM signals"),
                row_count=await _count(db=db, sql="SELECT COUNT(*) FROM signals"),
            ),
            OperationalFreshnessItemResponse(
                source="decision_records",
                latest_timestamp=await _max_timestamp(db=db, sql="SELECT MAX(timestamp) FROM decision_records"),
                row_count=await _count(db=db, sql="SELECT COUNT(*) FROM decision_records"),
            ),
            OperationalFreshnessItemResponse(
                source="trades",
                latest_timestamp=await _max_timestamp(db=db, sql="SELECT MAX(executed_at) FROM trades WHERE is_paper = true"),
                row_count=await _count(db=db, sql="SELECT COUNT(*) FROM trades WHERE is_paper = true"),
            ),
            OperationalFreshnessItemResponse(
                source="risk_events",
                latest_timestamp=await _max_timestamp(db=db, sql="SELECT MAX(created_at) FROM risk_events"),
                row_count=await _count(db=db, sql="SELECT COUNT(*) FROM risk_events"),
            ),
        ],
    )


async def build_runtime_readiness(*, db: AsyncSession) -> RuntimeReadinessResponse:
    generated_at = datetime.now(timezone.utc)
    worker_uptime = compute_uptime(started_at=_STARTED_AT, now=generated_at)
    restart_count = await _count_worker_restarts(db=db)
    provider_status = await build_operations_status(db=db)
    last_autonomous_cycle = await _load_latest_cycle_snapshot(db=db, cycle_kind="autonomous")
    last_campaign_cycle = await _load_latest_cycle_snapshot(db=db, cycle_kind="campaign")
    return RuntimeReadinessResponse(
        generated_at=generated_at,
        worker_uptime=worker_uptime,
        restart_count=max(restart_count - 1, 0),
        last_successful_full_pipeline_at=await _load_last_successful_full_pipeline_at(db=db) or get_last_successful_full_pipeline_at(),
        last_kraken_candle_processed_at=await _latest_kraken_candle_close_at(db=db),
        last_autonomous_cycle=last_autonomous_cycle,
        last_campaign_preview_cycle=last_campaign_cycle,
        unresolved_exceptions=await _count_recent_runtime_exceptions(db=db, now=generated_at),
        database_health=RuntimeReadinessHealthResponse(
            state=provider_status.system_health["database"].state,
            detail=provider_status.system_health["database"].detail,
        ),
        provider_health=provider_status.live_crypto_readiness,
    )


def _resolve_health(*, alerts: list[OperationalAlertResponse]) -> str:
    if any(item.severity == "red" for item in alerts):
        return "red"
    if any(item.severity == "yellow" for item in alerts):
        return "yellow"
    return "green"


def _resolve_phase(
    *,
    candles_processed: int,
    signals_generated: int,
    paper_trades_executed: int,
    campaign_count: int,
    laboratory_runs: int,
    evolution_count: int,
) -> str:
    if campaign_count > 0 or laboratory_runs > 0 or evolution_count > 0:
        return "researching"
    if paper_trades_executed > 0 or signals_generated > 0:
        return "paper_execution"
    if candles_processed > 0:
        return "data_collection"
    return "bootstrapping"


def _resolve_orchestrator_state(*, now: datetime, last_ingestion_at: datetime | None) -> tuple[str, str]:
    if last_ingestion_at is None:
        return "yellow", "Heartbeat pending"

    last_seen = last_ingestion_at.astimezone(timezone.utc)
    if (now - last_seen) > timedelta(minutes=20):
        return "red", "Heartbeat stale"
    return "green", "Heartbeat active"


async def _load_latest_research_cycle_status(*, db: AsyncSession) -> dict[str, object] | None:
    row = await db.execute(
        text(
            "SELECT after_state, created_at "
            "FROM audit_log "
            "WHERE entity_type = 'research_cycle' "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT 1"
        )
    )
    latest = row.mappings().first()
    if latest is None:
        return None

    after_state = latest.get("after_state")
    if not isinstance(after_state, dict):
        return None
    return after_state


async def _count_recent_research_failures(*, db: AsyncSession, now: datetime) -> int:
    since = now - timedelta(hours=6)
    row = await db.execute(
        text(
            "SELECT COUNT(*) AS failure_count "
            "FROM audit_log "
            "WHERE entity_type = 'research_cycle' "
            "AND action = 'research_cycle_failed' "
            "AND created_at >= :since"
        ),
        {"since": since},
    )
    record = row.mappings().first()
    if record is None:
        return 0
    return int(record.get("failure_count") or 0)


def _append_staleness_alert(
    *,
    alerts: list[OperationalAlertResponse],
    code: str,
    message: str,
    now: datetime,
    last_seen_at: datetime | None,
    threshold: timedelta,
    total_count: int,
) -> None:
    if total_count <= 0 or last_seen_at is None:
        return

    if (now - last_seen_at.astimezone(timezone.utc)) > threshold:
        alerts.append(
            OperationalAlertResponse(
                code=code,
                severity="yellow",
                message=message,
            )
        )


async def _count(*, db: AsyncSession, sql: str, params: dict[str, object] | None = None) -> int:
    result = await db.execute(text(sql), params or {})
    value = result.scalar_one_or_none()
    return int(value or 0)


async def _max_timestamp(*, db: AsyncSession, sql: str) -> datetime | None:
    result = await db.execute(text(sql))
    value = result.scalar_one_or_none()
    return value if isinstance(value, datetime) else None


async def _count_worker_restarts(*, db: AsyncSession) -> int:
    row = await db.execute(
        text(
            "SELECT COUNT(*) AS restart_count "
            "FROM audit_log "
            "WHERE entity_type = 'orchestration_worker' "
            "AND action = 'orchestration_worker_started'"
        )
    )
    record = row.mappings().first()
    return int(record.get("restart_count") or 0) if record is not None else 0


async def _count_recent_runtime_exceptions(*, db: AsyncSession, now: datetime) -> int:
    since = now - timedelta(hours=24)
    row = await db.execute(
        text(
            "SELECT COUNT(*) AS exception_count "
            "FROM audit_log "
            "WHERE created_at >= :since "
            "AND (action = 'decision_package_replay_failed' "
            "OR action = 'campaign_orchestration_failed' "
            "OR action = 'autonomous_cycle_failed' "
            "OR action = 'strategy_roster_failed' "
            "OR action = 'research_cycle_failed' "
            "OR action = 'orchestration_worker_start_failed')"
        ),
        {"since": since},
    )
    record = row.mappings().first()
    return int(record.get("exception_count") or 0) if record is not None else 0


async def _latest_kraken_candle_close_at(*, db: AsyncSession) -> datetime | None:
    row = await db.execute(
        text(
            "SELECT MAX(c.close_time) AS close_time "
            "FROM candles c "
            "JOIN assets a ON a.id = c.asset_id "
            "WHERE a.exchange = 'kraken_spot' "
            "AND UPPER(a.symbol) IN ('BTC', 'XBT', 'XXBT')"
        )
    )
    record = row.mappings().first()
    close_time = None if record is None else record.get("close_time")
    return close_time if isinstance(close_time, datetime) else None


async def _load_latest_cycle_snapshot(*, db: AsyncSession, cycle_kind: str) -> RuntimeReadinessCycleSnapshotResponse | None:
    result = await db.execute(
        select(AutonomousCycleRun)
        .where(AutonomousCycleRun.cycle_kind == cycle_kind)
        .order_by(AutonomousCycleRun.started_at.desc(), AutonomousCycleRun.cycle_id.desc())
        .limit(1)
    )
    cycle = result.scalar_one_or_none()
    if cycle is None:
        return None
    return RuntimeReadinessCycleSnapshotResponse(
        cycle_id=str(cycle.cycle_id),
        state=cycle.state,
        started_at=cycle.started_at,
        completed_at=cycle.completed_at,
        failure_reason=cycle.failure_reason,
    )


async def _load_last_successful_full_pipeline_at(*, db: AsyncSession) -> datetime | None:
    row = await db.execute(
        text(
            "SELECT created_at AS completed_at "
            "FROM audit_log "
            "WHERE action = 'orchestration_worker_full_pipeline_completed' "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT 1"
        )
    )
    record = row.mappings().first()
    completed_at = None if record is None else record.get("completed_at")
    return completed_at if isinstance(completed_at, datetime) else None


async def _get_paper_equity(*, db: AsyncSession) -> Decimal:
    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.asc())
        .limit(1)
    )
    if account is None:
        return Decimal("0")

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )
    return snapshot.equity


def _decimal_to_string(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"))
    return format(normalized, "f")
