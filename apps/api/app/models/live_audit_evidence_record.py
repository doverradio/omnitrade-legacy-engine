from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveAuditEvidenceRecord(Base):
    __tablename__ = "live_audit_evidence_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_audit_evidence_records_idempotency_key"),
        CheckConstraint(
            "event_type IN ('order_lifecycle_evidence','operator_action_evidence','incident_recovery_evidence')",
            name="ck_live_audit_evidence_records_event_type",
        ),
        CheckConstraint("length(attributable_actor_id) > 0", name="ck_live_audit_evidence_records_actor_id_required"),
        CheckConstraint("length(attributable_actor_role) > 0", name="ck_live_audit_evidence_records_actor_role_required"),
        CheckConstraint("length(action_name) > 0", name="ck_live_audit_evidence_records_action_name_required"),
        CheckConstraint(
            "num_nonnulls(live_execution_event_id, live_approval_event_id, live_resilience_event_id, live_reconciliation_event_id, live_accounting_record_id, live_execution_quality_metric_id) >= 1",
            name="ck_live_audit_evidence_records_requires_linkage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_trading_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    attributable_actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    attributable_actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    action_name: Mapped[str] = mapped_column(Text, nullable=False)
    action_source: Mapped[str] = mapped_column(Text, nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)

    live_execution_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_execution_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    live_approval_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_approval_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    live_resilience_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_resilience_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    live_reconciliation_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_reconciliation_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    live_accounting_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_accounting_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    live_execution_quality_metric_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_execution_quality_metrics.id", ondelete="SET NULL"),
        nullable=True,
    )

    provenance_hash: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveAuditEvidenceRecord, "before_update", propagate=True)
def _prevent_live_audit_evidence_record_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveAuditEvidenceRecord,
) -> None:
    raise ValueError("live_audit_evidence_records is append-only and does not support updates")


@event.listens_for(LiveAuditEvidenceRecord, "before_delete", propagate=True)
def _prevent_live_audit_evidence_record_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveAuditEvidenceRecord,
) -> None:
    raise ValueError("live_audit_evidence_records is append-only and does not support deletes")
