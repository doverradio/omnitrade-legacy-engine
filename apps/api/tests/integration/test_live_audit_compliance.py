from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_audit_evidence_record import LiveAuditEvidenceRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_execution_quality_metric import LiveExecutionQualityMetric
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_resilience_event import LiveResilienceEvent
from app.services.live.audit_compliance import (
    export_live_compliance_bundle,
    read_live_compliance_evidence,
    record_live_audit_evidence,
)
from app.services.live.contracts import LiveAuditEvidenceRequest, LiveComplianceExportRequest


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(
        self,
        *,
        execution_events: list[LiveExecutionEvent],
        approval_events: list[LiveApprovalEvent],
        resilience_events: list[LiveResilienceEvent],
        reconciliation_events: list[LiveReconciliationEvent],
        accounting_records: list[LiveAccountingRecord],
        quality_metrics: list[LiveExecutionQualityMetric],
    ) -> None:
        self.execution_events = execution_events
        self.approval_events = approval_events
        self.resilience_events = resilience_events
        self.reconciliation_events = reconciliation_events
        self.accounting_records = accounting_records
        self.quality_metrics = quality_metrics
        self.audit_records: list[LiveAuditEvidenceRecord] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_audit_evidence_records" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.audit_records:
                if item.idempotency_key == key:
                    return item
            return None

        if "FROM live_execution_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.execution_events:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        if "FROM live_approval_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.approval_events:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        if "FROM live_resilience_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.resilience_events:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        if "FROM live_reconciliation_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.reconciliation_events:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        if "FROM live_accounting_records" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.accounting_records:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        if "FROM live_execution_quality_metrics" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.quality_metrics:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        return None

    async def scalars(self, statement: Any) -> _Rows:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_audit_evidence_records" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            rows = [item for item in self.audit_records if item.live_trading_profile_id == profile_id]
            return _Rows(rows)

        return _Rows([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveAuditEvidenceRecord):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.audit_records.append(obj)

    async def flush(self) -> None:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_graph() -> tuple[
    uuid.UUID,
    LiveExecutionEvent,
    LiveApprovalEvent,
    LiveResilienceEvent,
    LiveReconciliationEvent,
    LiveAccountingRecord,
    LiveExecutionQualityMetric,
]:
    profile_id = uuid.uuid4()
    now = _now()

    execution = LiveExecutionEvent(
        id=uuid.uuid4(),
        idempotency_key="exec-key",
        event_hash="exec-hash",
        live_trading_profile_id=profile_id,
        sequence_number=1,
        event_type="execution_intent_created",
        provider_name="paper-sim",
        risk_decision_id=uuid.uuid4(),
        approval_event_id=uuid.uuid4(),
        audit_correlation_id="corr-1",
        operating_mode="live",
        paper_default_mode=True,
        risk_authority_model="risk_engine_final",
        event_payload={"symbol": "AAPL", "side": "buy"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )

    approval = LiveApprovalEvent(
        id=uuid.uuid4(),
        idempotency_key="appr-key",
        event_hash="appr-hash",
        live_trading_profile_id=profile_id,
        sequence_number=1,
        event_type="approval_granted",
        checkpoint_type="first_live_enablement",
        approval_state="approved",
        approver_id="operator",
        approver_role="risk_owner",
        rationale="ok",
        approval_scope={"scope": ["enable"]},
        expires_at=now,
        renewal_condition="renew",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )

    resilience = LiveResilienceEvent(
        id=uuid.uuid4(),
        idempotency_key="res-key",
        event_hash="res-hash",
        live_trading_profile_id=profile_id,
        sequence_number=1,
        event_type="outage_detected",
        provider_name="paper-sim",
        reason_code="provider_timeout",
        submission_blocked=True,
        kill_switch_engaged=False,
        outage_detected=True,
        ambiguity_detected=False,
        reapproval_required=True,
        approval_event_id=None,
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )

    reconciliation = LiveReconciliationEvent(
        id=uuid.uuid4(),
        idempotency_key="rec-key",
        event_hash="rec-hash",
        live_trading_profile_id=profile_id,
        source_execution_event_id=execution.id,
        source_execution_event_type="execution_intent_created",
        sequence_number=1,
        event_type="fill_reconciled",
        reconciliation_status="filled",
        provider_name="paper-sim",
        provider_order_id="order-1",
        provider_fill_id="fill-1",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )

    accounting = LiveAccountingRecord(
        id=uuid.uuid4(),
        idempotency_key="acct-key",
        live_trading_profile_id=profile_id,
        reconciliation_event_id=reconciliation.id,
        source_execution_event_id=execution.id,
        source_execution_event_type="execution_intent_created",
        record_type="fill_accounting",
        provider_order_id="order-1",
        provider_fill_id="fill-1",
        symbol="AAPL",
        side="buy",
        filled_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        gross_notional=Decimal("100"),
        fee_amount=Decimal("1"),
        fee_currency="USD",
        net_cash_impact=Decimal("-101"),
        provenance={"source": "test"},
        recorded_at=now,
        created_at=now,
    )

    quality = LiveExecutionQualityMetric(
        id=uuid.uuid4(),
        idempotency_key="qual-key",
        live_trading_profile_id=profile_id,
        source_execution_event_id=execution.id,
        source_reconciliation_event_id=reconciliation.id,
        source_accounting_record_id=accounting.id,
        provider_name="paper-sim",
        symbol="AAPL",
        side="buy",
        expected_price=Decimal("99"),
        expected_price_state="available",
        actual_fill_price=Decimal("100"),
        actual_price_state="available",
        slippage_abs=Decimal("1"),
        slippage_bps=Decimal("101.01"),
        slippage_state="available",
        market_context={"regime": "trend"},
        telemetry_context={"source": "test"},
        provenance={"source": "test"},
        recorded_at=now,
        created_at=now,
    )

    return profile_id, execution, approval, resilience, reconciliation, accounting, quality


@pytest.mark.asyncio
async def test_record_live_audit_evidence_is_append_only_and_attributable() -> None:
    profile_id, execution, approval, resilience, reconciliation, accounting, quality = _source_graph()
    session = _FakeSession(
        execution_events=[execution],
        approval_events=[approval],
        resilience_events=[resilience],
        reconciliation_events=[reconciliation],
        accounting_records=[accounting],
        quality_metrics=[quality],
    )

    result = await record_live_audit_evidence(
        db=session,
        request=LiveAuditEvidenceRequest(
            live_trading_profile_id=profile_id,
            event_type="order_lifecycle_evidence",
            attributable_actor_id="operator-1",
            attributable_actor_role="risk_owner",
            action_name="record_order_lifecycle",
            action_source="live_service",
            action_summary="Captured lifecycle evidence",
            evidence_payload={"status": "filled"},
            provenance_metadata={"ticket": "LIVE-99-A"},
            live_execution_event_id=execution.id,
            live_approval_event_id=approval.id,
            live_resilience_event_id=resilience.id,
            live_reconciliation_event_id=reconciliation.id,
            live_accounting_record_id=accounting.id,
            live_execution_quality_metric_id=quality.id,
            idempotency_key="audit-key-1",
        ),
    )

    assert result.live_trading_profile_id == profile_id
    assert result.event_type == "order_lifecycle_evidence"
    assert len(session.audit_records) == 1
    assert session.audit_records[0].provenance_hash


@pytest.mark.asyncio
async def test_record_live_audit_evidence_rejects_invalid_linkage() -> None:
    profile_id, execution, approval, resilience, reconciliation, accounting, quality = _source_graph()
    session = _FakeSession(
        execution_events=[execution],
        approval_events=[approval],
        resilience_events=[resilience],
        reconciliation_events=[reconciliation],
        accounting_records=[accounting],
        quality_metrics=[quality],
    )

    with pytest.raises(ValueError, match="live_execution_event_id linkage is invalid"):
        await record_live_audit_evidence(
            db=session,
            request=LiveAuditEvidenceRequest(
                live_trading_profile_id=profile_id,
                event_type="operator_action_evidence",
                attributable_actor_id="operator-2",
                attributable_actor_role="ops",
                action_name="manual_intervention",
                action_source="live_service",
                action_summary="Attempted bad linkage",
                evidence_payload={"x": 1},
                provenance_metadata={"ticket": "LIVE-99-B"},
                live_execution_event_id=uuid.uuid4(),
                idempotency_key="audit-key-2",
            ),
        )


@pytest.mark.asyncio
async def test_read_and_export_compliance_surfaces_are_non_mutating() -> None:
    profile_id, execution, approval, resilience, reconciliation, accounting, quality = _source_graph()
    session = _FakeSession(
        execution_events=[execution],
        approval_events=[approval],
        resilience_events=[resilience],
        reconciliation_events=[reconciliation],
        accounting_records=[accounting],
        quality_metrics=[quality],
    )

    await record_live_audit_evidence(
        db=session,
        request=LiveAuditEvidenceRequest(
            live_trading_profile_id=profile_id,
            event_type="incident_recovery_evidence",
            attributable_actor_id="operator-3",
            attributable_actor_role="incident_commander",
            action_name="incident_review",
            action_source="live_service",
            action_summary="Incident and recovery evidence",
            evidence_payload={"incident": "outage"},
            provenance_metadata={"ticket": "LIVE-99-C"},
            live_resilience_event_id=resilience.id,
            live_execution_event_id=execution.id,
            idempotency_key="audit-key-3",
        ),
    )

    before = len(session.audit_records)
    read_model = await read_live_compliance_evidence(db=session, live_trading_profile_id=profile_id)
    export_bundle = await export_live_compliance_bundle(
        db=session,
        request=LiveComplianceExportRequest(
            live_trading_profile_id=profile_id,
            exported_by="auditor-1",
        ),
    )

    assert read_model.total_records == 1
    assert export_bundle.total_records == 1
    assert export_bundle.records[0].event_type == "incident_recovery_evidence"
    assert len(session.audit_records) == before
