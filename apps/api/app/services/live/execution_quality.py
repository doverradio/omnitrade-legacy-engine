from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_execution_quality_metric import LiveExecutionQualityMetric
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.contracts import (
    LiveExecutionQualityCaptureRequest,
    LiveExecutionQualityCaptureResult,
    LiveExecutionQualityReadModel,
    LiveExecutionQualityReadModelItem,
)


def build_live_execution_quality_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    source_execution_event_id: uuid.UUID,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "source_execution_event_id": str(source_execution_event_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _extract_expected_price(source_event: LiveExecutionEvent) -> tuple[Decimal | None, str]:
    payload = source_event.event_payload or {}
    provider_payload = payload.get("provider_payload", {})
    expected = _to_decimal(payload.get("limit_price"))
    if expected is None:
        expected = _to_decimal(payload.get("expected_price"))
    if expected is None and isinstance(provider_payload, dict):
        expected = _to_decimal(provider_payload.get("expected_price"))
    if expected is None:
        return None, "unknown"
    return expected, "available"


def _resolve_fill_lineage(
    *,
    reconciliation_events: list[LiveReconciliationEvent],
    accounting_records: list[LiveAccountingRecord],
) -> tuple[LiveReconciliationEvent | None, LiveAccountingRecord | None]:
    fill_events = [item for item in reconciliation_events if item.event_type == "fill_reconciled"]
    if not fill_events:
        return None, None
    fill_events.sort(key=lambda x: x.sequence_number, reverse=True)
    chosen_event = fill_events[0]

    fill_records = [
        item
        for item in accounting_records
        if item.reconciliation_event_id == chosen_event.id and item.record_type in {"fill_accounting", "partial_fill_accounting"}
    ]
    if not fill_records:
        return chosen_event, None
    fill_records.sort(key=lambda x: x.created_at, reverse=True)
    return chosen_event, fill_records[0]


async def capture_live_execution_quality(
    *,
    db: AsyncSession,
    request: LiveExecutionQualityCaptureRequest,
) -> LiveExecutionQualityCaptureResult:
    idempotency_key = request.idempotency_key or build_live_execution_quality_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
    )

    existing = await db.scalar(
        select(LiveExecutionQualityMetric)
        .where(LiveExecutionQualityMetric.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return LiveExecutionQualityCaptureResult(
            accepted=True,
            status="replayed",
            reason=None,
            live_trading_profile_id=existing.live_trading_profile_id,
            source_execution_event_id=existing.source_execution_event_id,
            quality_metric_id=existing.id,
            idempotency_key=idempotency_key,
        )

    source_event = await db.scalar(
        select(LiveExecutionEvent)
        .where(
            LiveExecutionEvent.id == request.source_execution_event_id,
            LiveExecutionEvent.live_trading_profile_id == request.live_trading_profile_id,
        )
        .limit(1)
    )
    if source_event is None:
        return LiveExecutionQualityCaptureResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_found",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            quality_metric_id=None,
            idempotency_key=idempotency_key,
        )
    if source_event.event_type != "execution_intent_created":
        return LiveExecutionQualityCaptureResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_telemetry_eligible",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            quality_metric_id=None,
            idempotency_key=idempotency_key,
        )

    reconciliation_events = list(
        await db.scalars(
            select(LiveReconciliationEvent)
            .where(LiveReconciliationEvent.source_execution_event_id == request.source_execution_event_id)
            .order_by(LiveReconciliationEvent.sequence_number.asc())
        )
    )
    accounting_records = list(
        await db.scalars(
            select(LiveAccountingRecord)
            .where(LiveAccountingRecord.source_execution_event_id == request.source_execution_event_id)
            .order_by(LiveAccountingRecord.created_at.asc())
        )
    )

    expected_price, expected_state = _extract_expected_price(source_event)
    fill_reconciliation, fill_accounting = _resolve_fill_lineage(
        reconciliation_events=reconciliation_events,
        accounting_records=accounting_records,
    )

    side = str(source_event.event_payload.get("side") or "buy")
    symbol = str(source_event.event_payload.get("symbol") or "UNKNOWN")

    actual_price = fill_accounting.fill_price if fill_accounting is not None else None
    if fill_reconciliation is None or fill_accounting is None:
        actual_state = "unavailable"
    elif actual_price is None:
        actual_state = "unknown"
    else:
        actual_state = "available"

    slippage_abs: Decimal | None = None
    slippage_bps: Decimal | None = None
    slippage_state = "unknown"
    if expected_state == "available" and actual_state == "available" and expected_price is not None and actual_price is not None:
        signed_diff = (actual_price - expected_price) if side == "buy" else (expected_price - actual_price)
        slippage_abs = abs(signed_diff)
        if expected_price > Decimal("0"):
            slippage_bps = (signed_diff / expected_price) * Decimal("10000")
        slippage_state = "available"
    elif actual_state == "unavailable":
        slippage_state = "unavailable"

    recorded_at = datetime.now(timezone.utc)
    metric = LiveExecutionQualityMetric(
        idempotency_key=idempotency_key,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        source_reconciliation_event_id=fill_reconciliation.id if fill_reconciliation else None,
        source_accounting_record_id=fill_accounting.id if fill_accounting else None,
        provider_name=source_event.provider_name,
        symbol=symbol,
        side=side,
        expected_price=expected_price,
        expected_price_state=expected_state,
        actual_fill_price=actual_price,
        actual_price_state=actual_state,
        slippage_abs=slippage_abs,
        slippage_bps=slippage_bps,
        slippage_state=slippage_state,
        market_context=request.market_context,
        telemetry_context={
            "reconciliation_event_count": len(reconciliation_events),
            "accounting_record_count": len(accounting_records),
            "source_execution_event_type": source_event.event_type,
        },
        provenance={
            "requested_by": request.requested_by,
            "recorded_at": recorded_at.isoformat(),
            **request.provenance_metadata,
        },
        recorded_at=recorded_at,
    )

    async with db.begin():
        db.add(metric)
        await db.flush()

    return LiveExecutionQualityCaptureResult(
        accepted=True,
        status="recorded",
        reason=None,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        quality_metric_id=metric.id,
        idempotency_key=idempotency_key,
    )


async def read_live_execution_quality(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    symbol: str | None = None,
    provider_name: str | None = None,
) -> LiveExecutionQualityReadModel:
    metrics = list(
        await db.scalars(
            select(LiveExecutionQualityMetric)
            .where(LiveExecutionQualityMetric.live_trading_profile_id == live_trading_profile_id)
            .order_by(LiveExecutionQualityMetric.recorded_at.desc())
        )
    )

    if symbol is not None:
        metrics = [item for item in metrics if item.symbol == symbol]
    if provider_name is not None:
        metrics = [item for item in metrics if item.provider_name == provider_name]

    available = [item for item in metrics if item.slippage_state == "available" and item.slippage_bps is not None]
    unknown_or_unavailable = [item for item in metrics if item.slippage_state != "available"]

    average_bps: Decimal | None = None
    if available:
        total = sum((Decimal(str(item.slippage_bps)) for item in available), Decimal("0"))
        average_bps = total / Decimal(len(available))

    items = tuple(
        LiveExecutionQualityReadModelItem(
            quality_metric_id=item.id,
            provider_name=item.provider_name,
            symbol=item.symbol,
            side=item.side,
            expected_price=str(item.expected_price) if item.expected_price is not None else None,
            expected_price_state=item.expected_price_state,
            actual_fill_price=str(item.actual_fill_price) if item.actual_fill_price is not None else None,
            actual_price_state=item.actual_price_state,
            slippage_abs=str(item.slippage_abs) if item.slippage_abs is not None else None,
            slippage_bps=str(item.slippage_bps) if item.slippage_bps is not None else None,
            slippage_state=item.slippage_state,
            market_context=item.market_context,
            telemetry_context=item.telemetry_context,
            recorded_at=item.recorded_at,
        )
        for item in metrics
    )

    return LiveExecutionQualityReadModel(
        live_trading_profile_id=live_trading_profile_id,
        total_records=len(metrics),
        available_slippage_records=len(available),
        unknown_or_unavailable_records=len(unknown_or_unavailable),
        average_slippage_bps=str(average_bps) if average_bps is not None else None,
        items=items,
    )
