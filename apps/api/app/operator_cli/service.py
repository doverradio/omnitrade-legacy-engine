from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle


def _coerce_decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


async def execute_preview_cycle(
    *,
    mandate_id: UUID,
    actor: str,
    product_id: str,
    strategy_interval: str,
    trigger: str,
    idempotency_seed: str | None,
    software_build_version: str | None,
    forced_action: str | None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=mandate_id,
                actor=actor,
                product_id=product_id,
                strategy_interval=strategy_interval,
                trigger=trigger,
                idempotency_seed=idempotency_seed,
                software_build_version=software_build_version,
                forced_action=forced_action,
            ),
        )

    return {
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

    return {
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
            "deterministic_explanation": cycle.deterministic_explanation if cycle else None,
        },
    }


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

    async with AsyncSessionLocal() as db:
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

    candle_summary: dict[str, Any] | None = None
    if candle_symbol:
        candle_summary = await fetch_candle_readiness(
            symbol=candle_symbol,
            interval=candle_interval,
            exchange=candle_exchange,
            max_age_minutes=candle_max_age_minutes,
            lookback_limit=200,
        )

    return {
        "environment": settings.environment,
        "database_url_configured": bool(settings.database_url),
        "mandate_id": mandate.mandate_id if mandate else None,
        "mandate_status": mandate.status if mandate else None,
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
