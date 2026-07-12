from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousCapitalMandateEvaluation(Base):
    __tablename__ = "autonomous_capital_mandate_evaluations"
    __table_args__ = (
        CheckConstraint("proposed_action IN ('BUY','SELL','HOLD')", name="ck_ac_mandate_evaluations_action"),
        CheckConstraint("authorization_result IN ('AUTHORIZED','REJECTED')", name="ck_ac_mandate_evaluations_authorization_result"),
        CheckConstraint(
            "approval_result IN ('APPROVAL_REQUIRED_HUMAN','APPROVAL_SATISFIED_BY_ACTIVE_MANDATE')",
            name="ck_ac_mandate_evaluations_approval_result",
        ),
        CheckConstraint(
            "risk_verdict IN ('ACCEPTED','REJECTED','RESIZED','NOT_EVALUATED')",
            name="ck_ac_mandate_evaluations_risk_verdict",
        ),
        UniqueConstraint("idempotency_key", name="uq_ac_mandate_evaluations_idempotency"),
        Index("ix_ac_mandate_evaluations_mandate", "mandate_id"),
        Index("ix_ac_mandate_evaluations_decision", "decision_id"),
        Index("ix_ac_mandate_evaluations_created_at", "created_at"),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("autonomous_capital_mandates.mandate_id", ondelete="CASCADE"),
        nullable=False,
    )
    mandate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("autonomous_capital_mandate_versions.mandate_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    mandate_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.decision_id", ondelete="SET NULL"),
        nullable=True,
    )
    autonomy_level: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_action: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_result: Mapped[str] = mapped_column(Text, nullable=False)
    approval_result: Mapped[str] = mapped_column(Text, nullable=False)
    risk_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    risk_evaluated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    checks_passed: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    checks_failed: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    deterministic_explanation: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    human_approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active_mandate_exemption_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    software_build_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


@event.listens_for(AutonomousCapitalMandateEvaluation, "before_update", propagate=True)
def _prevent_mandate_evaluation_update(_mapper: Any, _connection: Any, _target: AutonomousCapitalMandateEvaluation) -> None:
    raise ValueError("mandate evaluations are append-only")


@event.listens_for(AutonomousCapitalMandateEvaluation, "before_delete", propagate=True)
def _prevent_mandate_evaluation_delete(_mapper: Any, _connection: Any, _target: AutonomousCapitalMandateEvaluation) -> None:
    raise ValueError("mandate evaluations are append-only")
