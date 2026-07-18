from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import join_or_begin_transaction
from app.db.tracing import trace_calls  # TEMPORARY diagnostic instrumentation
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_audit_evidence_record import LiveAuditEvidenceRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_execution_quality_metric import LiveExecutionQualityMetric
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_resilience_event import LiveResilienceEvent
from app.services.live.contracts import (
    LiveAuditEvidenceRequest,
    LiveAuditEvidenceResult,
    LiveComplianceEvidenceItem,
    LiveComplianceEvidenceReadModel,
    LiveComplianceExportBundle,
    LiveComplianceExportRequest,
)


def build_live_audit_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    event_type: str,
    action_name: str,
    attributable_actor_id: str,
    linkage: dict[str, str | None],
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "event_type": event_type,
            "action_name": action_name,
            "attributable_actor_id": attributable_actor_id,
            "linkage": linkage,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_provenance_integrity_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    event_type: str,
    actor_id: str,
    action_name: str,
    evidence_payload: dict[str, object],
    linkage: dict[str, str | None],
    provenance_metadata: dict[str, object],
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "event_type": event_type,
            "actor_id": actor_id,
            "action_name": action_name,
            "evidence_payload": evidence_payload,
            "linkage": linkage,
            "provenance_metadata": provenance_metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@trace_calls("record_live_audit_evidence")  # TEMPORARY diagnostic instrumentation
async def record_live_audit_evidence(
    *,
    db: AsyncSession,
    request: LiveAuditEvidenceRequest,
) -> LiveAuditEvidenceResult:
    linkage = {
        "live_execution_event_id": str(request.live_execution_event_id) if request.live_execution_event_id else None,
        "live_approval_event_id": str(request.live_approval_event_id) if request.live_approval_event_id else None,
        "live_resilience_event_id": str(request.live_resilience_event_id) if request.live_resilience_event_id else None,
        "live_reconciliation_event_id": str(request.live_reconciliation_event_id) if request.live_reconciliation_event_id else None,
        "live_accounting_record_id": str(request.live_accounting_record_id) if request.live_accounting_record_id else None,
        "live_execution_quality_metric_id": str(request.live_execution_quality_metric_id)
        if request.live_execution_quality_metric_id
        else None,
    }

    if not any(linkage.values()):
        raise ValueError("at least one live evidence linkage is required")
    if not request.attributable_actor_id.strip():
        raise ValueError("attributable_actor_id is required")
    if not request.attributable_actor_role.strip():
        raise ValueError("attributable_actor_role is required")

    idempotency_key = request.idempotency_key or build_live_audit_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type=request.event_type,
        action_name=request.action_name,
        attributable_actor_id=request.attributable_actor_id,
        linkage=linkage,
    )

    existing = await db.scalar(
        select(LiveAuditEvidenceRecord)
        .where(LiveAuditEvidenceRecord.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return LiveAuditEvidenceResult(
            evidence_record_id=existing.id,
            live_trading_profile_id=existing.live_trading_profile_id,
            event_type=existing.event_type,
            provenance_hash=existing.provenance_hash,
            idempotency_key=idempotency_key,
        )

    await _validate_linkage_integrity(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        request=request,
    )

    recorded_at = datetime.now(timezone.utc)
    provenance_hash = build_live_provenance_integrity_hash(
        live_trading_profile_id=request.live_trading_profile_id,
        event_type=request.event_type,
        actor_id=request.attributable_actor_id,
        action_name=request.action_name,
        evidence_payload=request.evidence_payload,
        linkage=linkage,
        provenance_metadata=request.provenance_metadata,
    )

    async with join_or_begin_transaction(db):
        record = LiveAuditEvidenceRecord(
            idempotency_key=idempotency_key,
            live_trading_profile_id=request.live_trading_profile_id,
            event_type=request.event_type,
            attributable_actor_id=request.attributable_actor_id,
            attributable_actor_role=request.attributable_actor_role,
            action_name=request.action_name,
            action_source=request.action_source,
            action_summary=request.action_summary,
            live_execution_event_id=request.live_execution_event_id,
            live_approval_event_id=request.live_approval_event_id,
            live_resilience_event_id=request.live_resilience_event_id,
            live_reconciliation_event_id=request.live_reconciliation_event_id,
            live_accounting_record_id=request.live_accounting_record_id,
            live_execution_quality_metric_id=request.live_execution_quality_metric_id,
            provenance_hash=provenance_hash,
            evidence_payload=request.evidence_payload,
            provenance={
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            recorded_at=recorded_at,
        )
        db.add(record)
        await db.flush()

    return LiveAuditEvidenceResult(
        evidence_record_id=record.id,
        live_trading_profile_id=record.live_trading_profile_id,
        event_type=record.event_type,
        provenance_hash=record.provenance_hash,
        idempotency_key=idempotency_key,
    )


async def read_live_compliance_evidence(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    event_type: str | None = None,
) -> LiveComplianceEvidenceReadModel:
    records = list(
        await db.scalars(
            select(LiveAuditEvidenceRecord)
            .where(LiveAuditEvidenceRecord.live_trading_profile_id == live_trading_profile_id)
            .order_by(LiveAuditEvidenceRecord.recorded_at.desc())
        )
    )

    if event_type is not None:
        records = [item for item in records if item.event_type == event_type]

    items = tuple(
        LiveComplianceEvidenceItem(
            evidence_record_id=item.id,
            event_type=item.event_type,
            attributable_actor_id=item.attributable_actor_id,
            attributable_actor_role=item.attributable_actor_role,
            action_name=item.action_name,
            action_source=item.action_source,
            action_summary=item.action_summary,
            provenance_hash=item.provenance_hash,
            linked_records={
                "live_execution_event_id": str(item.live_execution_event_id) if item.live_execution_event_id else "",
                "live_approval_event_id": str(item.live_approval_event_id) if item.live_approval_event_id else "",
                "live_resilience_event_id": str(item.live_resilience_event_id) if item.live_resilience_event_id else "",
                "live_reconciliation_event_id": str(item.live_reconciliation_event_id)
                if item.live_reconciliation_event_id
                else "",
                "live_accounting_record_id": str(item.live_accounting_record_id) if item.live_accounting_record_id else "",
                "live_execution_quality_metric_id": str(item.live_execution_quality_metric_id)
                if item.live_execution_quality_metric_id
                else "",
            },
            evidence_payload=item.evidence_payload,
            provenance=item.provenance,
            recorded_at=item.recorded_at,
        )
        for item in records
    )

    return LiveComplianceEvidenceReadModel(
        live_trading_profile_id=live_trading_profile_id,
        total_records=len(items),
        items=items,
    )


async def export_live_compliance_bundle(
    *,
    db: AsyncSession,
    request: LiveComplianceExportRequest,
) -> LiveComplianceExportBundle:
    read_model = await read_live_compliance_evidence(
        db=db,
        live_trading_profile_id=request.live_trading_profile_id,
        event_type=request.event_type,
    )

    return LiveComplianceExportBundle(
        live_trading_profile_id=request.live_trading_profile_id,
        exported_by=request.exported_by,
        exported_at=datetime.now(timezone.utc),
        total_records=read_model.total_records,
        records=read_model.items,
    )


async def _validate_linkage_integrity(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    request: LiveAuditEvidenceRequest,
) -> None:
    if request.live_execution_event_id is not None:
        row = await db.scalar(
            select(LiveExecutionEvent)
            .where(
                LiveExecutionEvent.id == request.live_execution_event_id,
                LiveExecutionEvent.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_execution_event_id linkage is invalid")

    if request.live_approval_event_id is not None:
        row = await db.scalar(
            select(LiveApprovalEvent)
            .where(
                LiveApprovalEvent.id == request.live_approval_event_id,
                LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_approval_event_id linkage is invalid")

    if request.live_resilience_event_id is not None:
        row = await db.scalar(
            select(LiveResilienceEvent)
            .where(
                LiveResilienceEvent.id == request.live_resilience_event_id,
                LiveResilienceEvent.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_resilience_event_id linkage is invalid")

    if request.live_reconciliation_event_id is not None:
        row = await db.scalar(
            select(LiveReconciliationEvent)
            .where(
                LiveReconciliationEvent.id == request.live_reconciliation_event_id,
                LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_reconciliation_event_id linkage is invalid")

    if request.live_accounting_record_id is not None:
        row = await db.scalar(
            select(LiveAccountingRecord)
            .where(
                LiveAccountingRecord.id == request.live_accounting_record_id,
                LiveAccountingRecord.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_accounting_record_id linkage is invalid")

    if request.live_execution_quality_metric_id is not None:
        row = await db.scalar(
            select(LiveExecutionQualityMetric)
            .where(
                LiveExecutionQualityMetric.id == request.live_execution_quality_metric_id,
                LiveExecutionQualityMetric.live_trading_profile_id == live_trading_profile_id,
            )
            .limit(1)
        )
        if row is None:
            raise ValueError("live_execution_quality_metric_id linkage is invalid")
