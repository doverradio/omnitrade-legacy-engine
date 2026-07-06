from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionExplainabilityRecord(Base):
    __tablename__ = "decision_explainability_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_explainability_records_idempotency_key"),
        CheckConstraint(
            "evidence_role IN ('supporting','opposing','confidence_factor','risk_adjustment')",
            name="ck_decision_explainability_records_role",
        ),
        CheckConstraint(
            "availability_state IN ('known','unknown','unavailable')",
            name="ck_decision_explainability_records_availability_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.decision_id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_role: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_name: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    availability_state: Mapped[str] = mapped_column(Text, nullable=False)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    decision_record = relationship("DecisionRecord", back_populates="explainability_records")


@event.listens_for(DecisionExplainabilityRecord, "before_update", propagate=True)
def _prevent_decision_explainability_record_update(
    _mapper: Any,
    _connection: Any,
    _target: DecisionExplainabilityRecord,
) -> None:
    raise ValueError("decision_explainability_records is append-only and does not support updates")


@event.listens_for(DecisionExplainabilityRecord, "before_delete", propagate=True)
def _prevent_decision_explainability_record_delete(
    _mapper: Any,
    _connection: Any,
    _target: DecisionExplainabilityRecord,
) -> None:
    raise ValueError("decision_explainability_records is append-only and does not support deletes")