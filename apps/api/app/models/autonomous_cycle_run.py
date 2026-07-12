from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousCycleRun(Base):
    __tablename__ = "autonomous_cycle_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_autonomous_cycle_runs_idempotency_key"),
        Index("ix_autonomous_cycle_runs_mandate_created", "mandate_id", "started_at"),
        Index("ix_autonomous_cycle_runs_state", "state"),
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mandate_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'NOT_STARTED'"))
    evaluation_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    termination_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deterministic_explanation: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cycle_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    mandate_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preview_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mandate_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    software_build_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
