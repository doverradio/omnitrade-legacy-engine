from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle


def _coerce_decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds()))


def _preview_command_mode(*, replayed: bool, command_name: str) -> str:
    if command_name == "preview-show":
        return "VIEW_EXISTING"
    return "IDEMPOTENT_REPLAY" if replayed else "NEW_PREVIEW"


def _decision_classification(*, proposed_action: str | None, risk_verdict: str | None, deterministic_explanation: list[str], failure_reason: str | None) -> str:
    action = (proposed_action or "").upper()
    risk = (risk_verdict or "").upper()
    explanation_blob = " ".join(deterministic_explanation).lower()
    reason = (failure_reason or "").lower()

    if reason.startswith("mandate_status_") or "mandate_not_active" in explanation_blob or "mandate_version_invalid" in reason:
        return "MANDATE_REJECTED"
    if "reconciliation_not_ready" in reason or "provider_not_ready" in reason or "insufficient_candle_context" in explanation_blob or "exchange_connection_not_found" in reason:
        return "INFRASTRUCTURE_BLOCKED"
    if risk == "REJECTED":
        return "RISK_REJECTED"
    if action == "HOLD":
        if "strategy_evaluated" in explanation_blob or "signal_action=hold" in explanation_blob:
            return "STRATEGY_DERIVED"
        return "SAFETY_HOLD" if explanation_blob else "INFRASTRUCTURE_BLOCKED"
    if action in {"BUY", "SELL"}:
        return "STRATEGY_DERIVED"
    return "INFRASTRUCTURE_BLOCKED"


def _capital_state(*, preview: CryptoOrderPreview | None, proposed_action: str | None) -> str:
    if preview is not None:
        return "PREVIEW_ONLY"
    if (proposed_action or "").upper() == "HOLD":
        return "NONE"
    return "UNKNOWN"


def _build_timeline_payload(
    *,
    command_mode: str,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cycle_created_at = _parse_datetime(getattr(cycle, "created_at", None)) or _parse_datetime(getattr(cycle, "started_at", None))
    decision_created_at = _parse_datetime(getattr(decision, "timestamp", None))
    snapshot_created_at = _parse_datetime(getattr(snapshot, "timestamp", None))
    preview_created_at = _parse_datetime(getattr(preview, "created_at", None))

    cycle_context = getattr(cycle, "cycle_context", None) or {}
    timeline_context = {}
    if isinstance(cycle_context, dict):
        strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
        signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context, dict) else {}
        if isinstance(signal_payload, dict):
            timeline_context = signal_payload.get("timeline") if isinstance(signal_payload.get("timeline"), dict) else {}
        if not timeline_context and isinstance(cycle_context.get("timeline"), dict):
            timeline_context = cycle_context.get("timeline")

    latest_completed_candle_open = _parse_datetime(timeline_context.get("latest_completed_candle_open")) if isinstance(timeline_context, dict) else None
    latest_completed_candle_close = _parse_datetime(timeline_context.get("latest_completed_candle_close")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_open = _parse_datetime(timeline_context.get("oldest_candle_used_open")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_close = _parse_datetime(timeline_context.get("oldest_candle_used_close")) if isinstance(timeline_context, dict) else None
    evaluated_at = _parse_datetime(timeline_context.get("evaluated_at")) or decision_created_at or cycle_created_at or now

    cycle_age_seconds = _seconds_between(now, cycle_created_at)
    decision_age_seconds = _seconds_between(now, decision_created_at)
    snapshot_age_seconds = _seconds_between(now, snapshot_created_at)
    market_data_age_seconds = _seconds_between(now, latest_completed_candle_close)

    history_candle_count = timeline_context.get("history_candle_count") if isinstance(timeline_context, dict) else None
    current_candle_excluded = bool(timeline_context.get("current_incomplete_candle_excluded")) if isinstance(timeline_context, dict) else None
    decision_applies_to = timeline_context.get("decision_applies_to") if isinstance(timeline_context, dict) else None

    mismatch_warning = False
    if cycle_age_seconds is not None and decision_age_seconds is not None:
        if abs(cycle_age_seconds - decision_age_seconds) > 120:
            mismatch_warning = True

    return {
        "evaluated_at": evaluated_at,
        "cycle_created_at": cycle_created_at,
        "decision_created_at": decision_created_at,
        "snapshot_created_at": snapshot_created_at,
        "preview_created_at": preview_created_at,
        "latest_completed_candle_open": latest_completed_candle_open,
        "latest_completed_candle_close": latest_completed_candle_close,
        "oldest_candle_used_open": oldest_candle_used_open,
        "oldest_candle_used_close": oldest_candle_used_close,
        "history_candle_count": history_candle_count,
        "cycle_age_seconds": cycle_age_seconds,
        "decision_age_seconds": decision_age_seconds,
        "snapshot_age_seconds": snapshot_age_seconds,
        "market_data_age_seconds": market_data_age_seconds,
        "current_incomplete_candle_excluded": current_candle_excluded,
        "decision_applies_to": decision_applies_to,
        "age_sources": {
            "cycle_age_seconds": "autonomous_cycle_runs.created_at",
            "decision_age_seconds": "decision_records.timestamp",
            "snapshot_age_seconds": "decision_snapshots.timestamp",
            "market_data_age_seconds": "candles.close_time",
        },
        "timestamp_mismatch_warning": mismatch_warning,
    }


def _build_preview_evidence_payload(
    *,
    command_name: str,
    result: Any,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    evaluation_mode = _preview_command_mode(replayed=bool(getattr(result, "replayed", False)), command_name=command_name)
    command_mode = evaluation_mode
    if command_name == "preview-show":
        command_mode = "VIEW_EXISTING"

    proposed_action = getattr(result, "proposed_action", None) or getattr(cycle, "proposed_action", None) or "HOLD"
    risk_verdict = getattr(result, "risk_verdict", None) or getattr(cycle, "risk_verdict", None)
    deterministic_explanation = list(getattr(result.diagnostics, "deterministic_explanation", []) if getattr(result, "diagnostics", None) else [])
    if not deterministic_explanation and cycle is not None:
        deterministic_explanation = list(getattr(cycle, "deterministic_explanation", []) or [])

    timeline = _build_timeline_payload(
        command_mode=command_mode,
        cycle=cycle,
        decision=decision,
        snapshot=snapshot,
        preview=preview,
    )

    decision_classification = _decision_classification(
        proposed_action=proposed_action,
        risk_verdict=risk_verdict,
        deterministic_explanation=deterministic_explanation,
        failure_reason=getattr(result.diagnostics, "failure_reason", None) if getattr(result, "diagnostics", None) else getattr(cycle, "failure_reason", None),
    )

    capital_state = _capital_state(preview=preview, proposed_action=proposed_action)
    new_evaluation = command_mode == "NEW_PREVIEW"
    outcome = (proposed_action or "FAILED").upper() if command_mode != "VIEW_EXISTING" else (getattr(decision, "outcome", None) or (proposed_action or "FAILED")).upper()

    if command_mode == "VIEW_EXISTING":
        record_created = timeline.get("decision_created_at") or timeline.get("cycle_created_at")
    elif command_mode == "IDEMPOTENT_REPLAY":
        record_created = timeline.get("cycle_created_at")
    else:
        record_created = timeline.get("cycle_created_at") or timeline.get("decision_created_at")

    timeline_warning = bool(timeline.get("timestamp_mismatch_warning"))

    return {
        "command_mode": command_mode,
        "evaluation_mode": evaluation_mode,
        "outcome": outcome,
        "decision_classification": decision_classification,
        "capital_state": capital_state,
        "new_evaluation": new_evaluation,
        "record_created_at": record_created,
        "timeline": timeline,
        "timeline_warning": timeline_warning,
    }


async def execute_preview_cycle(
    *,
    mandate_id: UUID | None,
    actor: str,
    product_id: str,
    strategy_interval: str,
    trigger: str,
    idempotency_seed: str | None,
    software_build_version: str | None,
    forced_action: str | None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        resolved_mandate_id = mandate_id
        if resolved_mandate_id is None:
            resolved_mandate_id = await db.scalar(
                select(AutonomousCapitalMandate.mandate_id)
                .where(AutonomousCapitalMandate.status == "ACTIVE")
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            if resolved_mandate_id is None:
                resolved_mandate_id = await db.scalar(
                    select(AutonomousCapitalMandate.mandate_id)
                    .order_by(desc(AutonomousCapitalMandate.updated_at))
                    .limit(1)
                )
        if resolved_mandate_id is None:
            raise ValueError("No mandate found. Seed or create a mandate before running preview.")

        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=resolved_mandate_id,
                actor=actor,
                product_id=product_id,
                strategy_interval=strategy_interval,
                trigger=trigger,
                idempotency_seed=idempotency_seed,
                software_build_version=software_build_version,
                forced_action=forced_action,
            ),
        )

        cycle = await db.get(AutonomousCycleRun, result.cycle_id)
        decision = await db.get(DecisionRecord, result.decision_record_id) if result.decision_record_id else None
        snapshot = await db.get(DecisionSnapshot, result.decision_record_id) if result.decision_record_id else None
        preview = await db.get(CryptoOrderPreview, result.preview_id) if result.preview_id else None

    payload = {
        "cycle_id": result.cycle_id,
        "state": result.state,
        "idempotency_key": result.idempotency_key,
        "mandate_id": result.mandate_id,
        "mandate_version_id": result.mandate_version_id,
        "proposed_action": result.proposed_action,
        "mandate_verdict": result.mandate_verdict,
        "risk_verdict": result.risk_verdict,
        "decision_record_id": result.decision_record_id,
        "preview_id": result.preview_id,
        "mandate_evaluation_id": result.mandate_evaluation_id,
        "risk_event_id": result.risk_event_id,
        "audit_correlation_id": result.audit_correlation_id,
        "replayed": result.replayed,
        "cycle_context": result.cycle_context,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "diagnostics": {
            "duration_ms": result.diagnostics.duration_ms,
            "evaluation_stage": result.diagnostics.evaluation_stage,
            "termination_stage": result.diagnostics.termination_stage,
            "failure_reason": result.diagnostics.failure_reason,
            "deterministic_explanation": list(result.diagnostics.deterministic_explanation),
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview",
            result=result,
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


def _resolve_git_sha() -> str | None:
    configured_sha = (
        Path(__file__).resolve().parents[4] / ".git" / "HEAD"
    )
    if configured_sha.exists():
        try:
            head_value = configured_sha.read_text(encoding="utf-8").strip()
            if head_value.startswith("ref:"):
                ref_path = head_value.split(":", 1)[1].strip()
                ref_file = configured_sha.parent / ref_path
                if ref_file.exists():
                    return ref_file.read_text(encoding="utf-8").strip()[:12]
            if head_value:
                return head_value[:12]
        except OSError:
            return None
    return None


async def fetch_preview_evidence(*, preview_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        preview = await db.get(CryptoOrderPreview, preview_id)
        if preview is None:
            raise ValueError(f"Preview {preview_id} not found")

        decision: DecisionRecord | None = None
        snapshot: DecisionSnapshot | None = None
        if preview.decision_record_id is not None:
            decision = await db.get(DecisionRecord, preview.decision_record_id)
            snapshot = await db.get(DecisionSnapshot, preview.decision_record_id)

        cycle: AutonomousCycleRun | None = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.preview_id == preview.crypto_order_preview_id)
            .order_by(desc(AutonomousCycleRun.started_at))
            .limit(1)
        )

    payload = {
        "preview": {
            "crypto_order_preview_id": preview.crypto_order_preview_id,
            "status": preview.status,
            "provider": preview.provider,
            "environment": preview.environment,
            "product_id": preview.product_id,
            "side": preview.side,
            "order_type": preview.order_type,
            "requested_amount": _coerce_decimal(preview.requested_amount),
            "requested_amount_currency": preview.requested_amount_currency,
            "quote_size": _coerce_decimal(preview.quote_size),
            "base_size": _coerce_decimal(preview.base_size),
            "estimated_average_price": _coerce_decimal(preview.estimated_average_price),
            "estimated_total_value": _coerce_decimal(preview.estimated_total_value),
            "estimated_base_size": _coerce_decimal(preview.estimated_base_size),
            "estimated_quote_size": _coerce_decimal(preview.estimated_quote_size),
            "estimated_fee": _coerce_decimal(preview.estimated_fee),
            "estimated_fee_currency": preview.estimated_fee_currency,
            "estimated_slippage": _coerce_decimal(preview.estimated_slippage),
            "estimated_commission_total": _coerce_decimal(preview.estimated_commission_total),
            "best_bid": _coerce_decimal(preview.best_bid),
            "best_ask": _coerce_decimal(preview.best_ask),
            "status_reason": preview.failure_reason,
            "warning_messages": list(preview.warning_messages or []),
            "readiness_verdict": preview.readiness_verdict,
            "risk_verdict": preview.risk_verdict,
            "risk_explanation": preview.risk_explanation,
            "decision_record_id": preview.decision_record_id,
            "risk_event_id": preview.risk_event_id,
            "audit_correlation_id": preview.audit_correlation_id,
            "created_at": preview.created_at,
            "updated_at": preview.updated_at,
            "expires_at": preview.expires_at,
        },
        "decision_record": {
            "decision_id": decision.decision_id if decision else None,
            "timeframe": decision.timeframe if decision else None,
            "trade_accepted": decision.trade_accepted if decision else None,
            "trade_rejected_reason": decision.trade_rejected_reason if decision else None,
            "outcome": decision.outcome if decision else None,
            "generated_signals": decision.generated_signals if decision else None,
            "indicators": decision.indicators if decision else None,
            "risk_adjustments": decision.risk_adjustments if decision else None,
            "supporting_strategies": decision.supporting_strategies if decision else None,
            "opposing_strategies": decision.opposing_strategies if decision else None,
            "execution_details": decision.execution_details if decision else None,
        },
        "decision_snapshot": {
            "decision_id": snapshot.decision_id if snapshot else None,
            "strategy_version": snapshot.strategy_version if snapshot else None,
            "configuration_version": snapshot.configuration_version if snapshot else None,
            "decision_engine_version": snapshot.decision_engine_version if snapshot else None,
            "generated_features": snapshot.generated_features if snapshot else None,
            "strategy_inputs": snapshot.strategy_inputs if snapshot else None,
            "risk_inputs": snapshot.risk_inputs if snapshot else None,
        },
        "cycle": {
            "cycle_id": cycle.cycle_id if cycle else None,
            "state": cycle.state if cycle else None,
            "evaluation_stage": cycle.evaluation_stage if cycle else None,
            "termination_stage": cycle.termination_stage if cycle else None,
            "failure_reason": cycle.failure_reason if cycle else None,
            "mandate_id": cycle.mandate_id if cycle else None,
            "mandate_version_id": cycle.mandate_version_id if cycle else None,
            "proposed_action": cycle.proposed_action if cycle else None,
            "risk_verdict": cycle.risk_verdict if cycle else None,
            "started_at": cycle.started_at if cycle else None,
            "completed_at": cycle.completed_at if cycle else None,
            "created_at": cycle.created_at if cycle else None,
            "deterministic_explanation": cycle.deterministic_explanation if cycle else None,
            "cycle_context": cycle.cycle_context if cycle else None,
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview-show",
            result=type("_PreviewResult", (), {"replayed": False, "proposed_action": preview.side, "risk_verdict": preview.risk_verdict, "diagnostics": type("_Diag", (), {"deterministic_explanation": cycle.deterministic_explanation if cycle else [], "failure_reason": cycle.failure_reason if cycle else None})()})(),
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


async def fetch_candle_readiness(
    *,
    symbol: str,
    interval: str,
    exchange: str | None,
    max_age_minutes: int,
    lookback_limit: int,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().lower() if exchange else None

    async with AsyncSessionLocal() as db:
        asset_query = select(Asset).where(func.upper(Asset.symbol) == normalized_symbol)
        if normalized_exchange:
            asset_query = asset_query.where(func.lower(Asset.exchange) == normalized_exchange)
        assets = (await db.execute(asset_query.order_by(desc(Asset.created_at)).limit(2))).scalars().all()

        if not assets:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "asset_not_found",
            }

        if len(assets) > 1:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "ambiguous_asset_resolution",
            }

        asset = assets[0]
        latest_candle = await db.scalar(
            select(Candle)
            .where(Candle.asset_id == asset.id, Candle.interval == interval)
            .order_by(desc(Candle.open_time))
            .limit(1)
        )
        row_count = (
            await db.scalar(
                select(func.count())
                .select_from(Candle)
                .where(Candle.asset_id == asset.id, Candle.interval == interval)
            )
            or 0
        )

    if latest_candle is None:
        return {
            "symbol": asset.symbol,
            "exchange": asset.exchange,
            "interval": interval,
            "asset_id": asset.id,
            "row_count": int(row_count),
            "latest_open_time": None,
            "latest_close_time": None,
            "age_minutes": None,
            "ready": False,
            "reason": "no_candles",
        }

    now = datetime.now(timezone.utc)
    close_time = latest_candle.close_time
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - close_time).total_seconds() // 60))
    ready = age_minutes <= max_age_minutes

    return {
        "symbol": asset.symbol,
        "exchange": asset.exchange,
        "interval": interval,
        "asset_id": asset.id,
        "row_count": int(row_count),
        "latest_open_time": latest_candle.open_time,
        "latest_close_time": latest_candle.close_time,
        "age_minutes": age_minutes,
        "ready": ready,
        "reason": "ok" if ready else "stale_candles",
        "max_age_minutes": max_age_minutes,
        "lookback_limit": lookback_limit,
    }


async def fetch_operator_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        await db.execute(select(1))

        if mandate_id is not None:
            mandate: AutonomousCapitalMandate | None = await db.get(AutonomousCapitalMandate, mandate_id)
            if mandate is None:
                raise ValueError(f"Mandate {mandate_id} not found")
            cycle_stmt = (
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.mandate_id == mandate_id)
                .order_by(desc(AutonomousCycleRun.started_at))
                .limit(1)
            )
        else:
            mandate = await db.scalar(
                select(AutonomousCapitalMandate)
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            cycle_stmt = select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1)

        latest_cycle = await db.scalar(cycle_stmt)
        latest_preview = await db.scalar(select(CryptoOrderPreview).order_by(desc(CryptoOrderPreview.created_at)).limit(1))
        connections = (
            await db.execute(select(ExchangeConnection).order_by(ExchangeConnection.provider.asc(), ExchangeConnection.environment.asc()))
        ).scalars().all()
        campaign_count = int((await db.scalar(select(func.count()).select_from(CapitalCampaign))) or 0)
        decision_count = int((await db.scalar(select(func.count()).select_from(DecisionRecord))) or 0)
        open_preview_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(CryptoOrderPreview)
                    .where(CryptoOrderPreview.expires_at > now)
                )
            )
            or 0
        )

        open_live_orders = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(
                        func.lower(LiveCryptoOrder.status).notin_(
                            [
                                "filled",
                                "cancelled",
                                "failed",
                                "rejected",
                                "expired",
                                "settled",
                                "completed",
                            ]
                        )
                    )
                )
            )
            or 0
        )

    candle_summary: dict[str, Any] | None = None
    if candle_symbol:
        candle_summary = await fetch_candle_readiness(
            symbol=candle_symbol,
            interval=candle_interval,
            exchange=candle_exchange,
            max_age_minutes=candle_max_age_minutes,
            lookback_limit=200,
        )

    kraken_production = None
    for item in connections:
        if item.provider == "kraken_spot" and item.environment == "production":
            kraken_production = item
            break

    latest_strategy: dict[str, Any] = {"name": None, "version": None}
    open_positions: int | None = None
    if latest_cycle is not None:
        context = latest_cycle.cycle_context or {}
        strategy = context.get("strategy") if isinstance(context, dict) else None
        reconciliation = context.get("reconciliation_status") if isinstance(context, dict) else None
        if isinstance(strategy, dict):
            latest_strategy = {
                "name": strategy.get("name"),
                "version": strategy.get("version"),
            }
        if isinstance(reconciliation, dict) and isinstance(reconciliation.get("open_position_count"), int):
            open_positions = reconciliation.get("open_position_count")

    latest_signal = latest_cycle.proposed_action if latest_cycle else None
    worker_heartbeat = latest_cycle.completed_at if latest_cycle and latest_cycle.completed_at else None
    if worker_heartbeat is None and latest_cycle is not None:
        worker_heartbeat = latest_cycle.started_at

    system_health = "healthy"
    if kraken_production is not None and kraken_production.status not in {"connected"}:
        system_health = "degraded"
    if candle_summary and not candle_summary.get("ready"):
        system_health = "degraded"

    preview_operator_recommendation = "No action required."
    if latest_cycle is not None:
        action = str(latest_cycle.proposed_action or "").upper()
        state = str(latest_cycle.state or "").upper()
        risk_verdict = str(latest_cycle.risk_verdict or "").upper()
        if state == "FAILED":
            preview_operator_recommendation = "Inspect latest cycle failure before proceeding."
        elif action == "HOLD":
            preview_operator_recommendation = "Waiting for next qualifying BUY."
        elif action in {"BUY", "SELL"} and risk_verdict == "REJECTED":
            preview_operator_recommendation = "Inspect Risk rejection."
        elif action in {"BUY", "SELL"}:
            preview_operator_recommendation = "Review latest preview evidence and approval readiness."

    api_status = "responsive"
    database_status = "connected"
    kraken_status = "Unavailable"
    if kraken_production is not None:
        readiness = kraken_production.last_readiness_verdict or "Unknown"
        kraken_status = f"{kraken_production.status} ({readiness})"

    worker_status = "Unavailable"
    if worker_heartbeat is not None:
        heartbeat_value = worker_heartbeat if worker_heartbeat.tzinfo is not None else worker_heartbeat.replace(tzinfo=timezone.utc)
        age_minutes = int(max(0, (now - heartbeat_value).total_seconds() // 60))
        worker_status = f"heartbeat {age_minutes}m ago"

    git_sha = _resolve_git_sha()

    return {
        "environment": settings.environment,
        "git_sha": git_sha,
        "api_status": api_status,
        "database_status": database_status,
        "worker_status": worker_status,
        "worker_heartbeat": worker_heartbeat,
        "kraken_status": kraken_status,
        "system_health": system_health,
        "database_url_configured": bool(settings.database_url),
        "mandate_id": mandate.mandate_id if mandate else None,
        "mandate_status": mandate.status if mandate else None,
        "latest_strategy": latest_strategy,
        "latest_signal": latest_signal,
        "campaign_count": campaign_count,
        "decision_count": decision_count,
        "open_positions": open_positions,
        "open_previews": open_preview_count,
        "open_live_orders": open_live_orders,
        "research_status": "available" if settings.research_evolution_enabled else "disabled",
        "operator_recommendation": preview_operator_recommendation,
        "safety_flags": {
            "live_crypto_order_submission_enabled": settings.live_crypto_order_submission_enabled,
            "live_crypto_dry_run_enabled": settings.live_crypto_dry_run_enabled,
            "live_crypto_max_order_usd": _coerce_decimal(settings.live_crypto_max_order_usd),
            "live_crypto_preparation_enabled": settings.live_crypto_preparation_enabled,
        },
        "latest_cycle": {
            "cycle_id": latest_cycle.cycle_id if latest_cycle else None,
            "state": latest_cycle.state if latest_cycle else None,
            "proposed_action": latest_cycle.proposed_action if latest_cycle else None,
            "risk_verdict": latest_cycle.risk_verdict if latest_cycle else None,
            "failure_reason": latest_cycle.failure_reason if latest_cycle else None,
            "started_at": latest_cycle.started_at if latest_cycle else None,
            "completed_at": latest_cycle.completed_at if latest_cycle else None,
        },
        "latest_preview": {
            "crypto_order_preview_id": latest_preview.crypto_order_preview_id if latest_preview else None,
            "status": latest_preview.status if latest_preview else None,
            "provider": latest_preview.provider if latest_preview else None,
            "product_id": latest_preview.product_id if latest_preview else None,
            "side": latest_preview.side if latest_preview else None,
            "created_at": latest_preview.created_at if latest_preview else None,
            "expires_at": latest_preview.expires_at if latest_preview else None,
        },
        "connection_summary": [
            {
                "exchange_connection_id": item.exchange_connection_id,
                "provider": item.provider,
                "environment": item.environment,
                "status": item.status,
                "credentials_valid": item.credentials_valid,
                "last_readiness_verdict": item.last_readiness_verdict,
                "last_verified_at": item.last_verified_at,
                "last_heartbeat_at": item.last_heartbeat_at,
            }
            for item in connections
        ],
        "candle_summary": candle_summary,
    }


async def fetch_watch_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    return await fetch_operator_status(
        mandate_id=mandate_id,
        candle_symbol=candle_symbol,
        candle_interval=candle_interval,
        candle_exchange=candle_exchange,
        candle_max_age_minutes=candle_max_age_minutes,
    )


async def fetch_strategy_roster_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_run = await db.scalar(
            select(StrategyRosterRun)
            .where(StrategyRosterRun.provider == provider)
            .where(StrategyRosterRun.product_id == product_id)
            .where(StrategyRosterRun.interval == interval)
            .order_by(desc(StrategyRosterRun.candle_close_time), desc(StrategyRosterRun.created_at))
            .limit(1)
        )

        if latest_run is None:
            return {
                "provider": provider,
                "product_id": product_id,
                "interval": interval,
                "roster_run": None,
                "proposals": [],
            }

        proposals = list(
            (
                await db.execute(
                    select(StrategyRosterProposal)
                    .where(StrategyRosterProposal.roster_run_id == latest_run.roster_run_id)
                    .order_by(StrategyRosterProposal.strategy_slug.asc())
                )
            ).scalars().all()
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "roster_run": {
            "roster_run_id": latest_run.roster_run_id,
            "asset_id": latest_run.asset_id,
            "candle_open_time": latest_run.candle_open_time,
            "candle_close_time": latest_run.candle_close_time,
            "trigger": latest_run.trigger,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "strategies_requested": list(latest_run.strategies_requested or []),
            "strategies_completed": list(latest_run.strategies_completed or []),
            "strategies_failed": list(latest_run.strategies_failed or []),
            "buy_count": latest_run.buy_count,
            "sell_count": latest_run.sell_count,
            "hold_count": latest_run.hold_count,
            "execution_mode": latest_run.execution_mode,
            "live_submission_allowed": latest_run.live_submission_allowed,
            "scheduled_cycle_id": latest_run.scheduled_cycle_id,
        },
        "proposals": [
            {
                "proposal_id": item.proposal_id,
                "strategy_slug": item.strategy_slug,
                "strategy_version": item.strategy_version,
                "strategy_identity": item.strategy_identity,
                "parameter_set_identity": item.parameter_set_identity,
                "action": item.action,
                "evaluation_status": item.evaluation_status,
                "strength": item.strength,
                "confidence": item.confidence,
                "reason": item.reason,
                "deterministic_explanation": list(item.deterministic_explanation or []),
                "indicator_values": item.indicator_values,
                "market_window_evidence": item.market_window_evidence,
                "evaluated_at": item.evaluated_at,
            }
            for item in proposals
        ],
    }
