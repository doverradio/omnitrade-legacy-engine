from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.contracts import (
    LiveFillReconciliationRequest,
    LiveOrderReconciliationRequest,
    LiveReconciliationResult,
)


def build_live_reconciliation_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    source_execution_event_id: uuid.UUID,
    provider_name: str,
    provider_order_id: str,
    provider_fill_id: str | None,
    event_type: str,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "source_execution_event_id": str(source_execution_event_id),
            "provider_name": provider_name,
            "provider_order_id": provider_order_id,
            "provider_fill_id": provider_fill_id,
            "event_type": event_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_reconciliation_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    idempotency_key: str,
    recorded_at: datetime,
    payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "sequence_number": sequence_number,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "recorded_at": recorded_at.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def record_live_order_reconciliation(
    *,
    db: AsyncSession,
    request: LiveOrderReconciliationRequest,
) -> LiveReconciliationResult:
    idempotency_key = request.idempotency_key or build_live_reconciliation_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        provider_name=request.provider_name,
        provider_order_id=request.provider_order_id,
        provider_fill_id=None,
        event_type="order_reconciled",
    )

    existing = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return LiveReconciliationResult(
            accepted=True,
            status="replayed",
            reason=None,
            live_trading_profile_id=existing.live_trading_profile_id,
            source_execution_event_id=existing.source_execution_event_id,
            reconciliation_event_id=existing.id,
            accounting_record_ids=tuple(),
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
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_found",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )
    if source_event.event_type != "execution_intent_created":
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_reconcilable",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )

    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveReconciliationEvent.sequence_number)).where(
                LiveReconciliationEvent.live_trading_profile_id == request.live_trading_profile_id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        payload = {
            "provider_order_id": request.provider_order_id,
            "client_order_id": request.client_order_id,
            "status": request.reconciliation_status,
        }
        event = LiveReconciliationEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_reconciliation_event_hash(
                live_trading_profile_id=request.live_trading_profile_id,
                sequence_number=sequence_number,
                event_type="order_reconciled",
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                payload=payload,
            ),
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="order_reconciled",
            reconciliation_status=request.reconciliation_status,
            provider_name=request.provider_name,
            provider_order_id=request.provider_order_id,
            provider_fill_id=None,
            event_payload=payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

    return LiveReconciliationResult(
        accepted=True,
        status="recorded",
        reason=None,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        reconciliation_event_id=event.id,
        accounting_record_ids=tuple(),
        idempotency_key=idempotency_key,
    )


async def record_live_fill_reconciliation(
    *,
    db: AsyncSession,
    request: LiveFillReconciliationRequest,
) -> LiveReconciliationResult:
    idempotency_key = request.idempotency_key or build_live_reconciliation_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        provider_name=request.provider_name,
        provider_order_id=request.provider_order_id,
        provider_fill_id=request.provider_fill_id,
        event_type="fill_reconciled",
    )

    existing = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        accounting_rows = await db.scalars(
            select(LiveAccountingRecord)
            .where(LiveAccountingRecord.reconciliation_event_id == existing.id)
            .order_by(LiveAccountingRecord.created_at.asc())
        )
        return LiveReconciliationResult(
            accepted=True,
            status="replayed",
            reason=None,
            live_trading_profile_id=existing.live_trading_profile_id,
            source_execution_event_id=existing.source_execution_event_id,
            reconciliation_event_id=existing.id,
            accounting_record_ids=tuple(item.id for item in accounting_rows),
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
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_found",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )
    if source_event.event_type != "execution_intent_created":
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_reconcilable",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )

    fill_quantity = Decimal(request.fill_quantity)
    cumulative_filled = Decimal(request.cumulative_filled_quantity)
    order_quantity = Decimal(request.order_quantity)
    fill_price = Decimal(request.fill_price)
    fee_amount = Decimal(request.fee_amount)
    gross_notional = fill_quantity * fill_price
    is_partial = cumulative_filled < order_quantity

    reconciliation_status = "partially_filled" if is_partial else "filled"
    accounting_record_type = "partial_fill_accounting" if is_partial else "fill_accounting"

    net_cash_impact = gross_notional + fee_amount
    if request.side == "buy":
        net_cash_impact = net_cash_impact * Decimal("-1")

    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveReconciliationEvent.sequence_number)).where(
                LiveReconciliationEvent.live_trading_profile_id == request.live_trading_profile_id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        payload = {
            "provider_order_id": request.provider_order_id,
            "provider_fill_id": request.provider_fill_id,
            "client_order_id": request.client_order_id,
            "fill_quantity": request.fill_quantity,
            "cumulative_filled_quantity": request.cumulative_filled_quantity,
            "order_quantity": request.order_quantity,
            "fill_price": request.fill_price,
            "fee_amount": request.fee_amount,
            "fee_currency": request.fee_currency,
            "partial_fill": is_partial,
        }
        event = LiveReconciliationEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_reconciliation_event_hash(
                live_trading_profile_id=request.live_trading_profile_id,
                sequence_number=sequence_number,
                event_type="fill_reconciled",
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                payload=payload,
            ),
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="fill_reconciled",
            reconciliation_status=reconciliation_status,
            provider_name=request.provider_name,
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            event_payload=payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

        accounting = LiveAccountingRecord(
            idempotency_key=f"{idempotency_key}:fill",
            live_trading_profile_id=request.live_trading_profile_id,
            reconciliation_event_id=event.id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            record_type=accounting_record_type,
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            symbol=request.symbol,
            side=request.side,
            filled_quantity=fill_quantity,
            fill_price=fill_price,
            gross_notional=gross_notional,
            fee_amount=fee_amount,
            fee_currency=request.fee_currency,
            net_cash_impact=net_cash_impact,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                "reconciliation_status": reconciliation_status,
                **request.provenance_metadata,
            },
            recorded_at=recorded_at,
        )
        db.add(accounting)

        fee_attribution = LiveAccountingRecord(
            idempotency_key=f"{idempotency_key}:fee",
            live_trading_profile_id=request.live_trading_profile_id,
            reconciliation_event_id=event.id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            record_type="fee_attribution",
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            symbol=request.symbol,
            side=request.side,
            filled_quantity=fill_quantity,
            fill_price=fill_price,
            gross_notional=gross_notional,
            fee_amount=fee_amount,
            fee_currency=request.fee_currency,
            net_cash_impact=fee_amount,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                "attribution": "fill_fee",
                **request.provenance_metadata,
            },
            recorded_at=recorded_at,
        )
        db.add(fee_attribution)
        await db.flush()

    return LiveReconciliationResult(
        accepted=True,
        status="recorded",
        reason=None,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        reconciliation_event_id=event.id,
        accounting_record_ids=(accounting.id, fee_attribution.id),
        idempotency_key=idempotency_key,
    )
