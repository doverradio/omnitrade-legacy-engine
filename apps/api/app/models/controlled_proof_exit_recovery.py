from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ControlledProofExitRecovery(Base):
    """One explicit, exit-only authority for a terminal Controlled Proof."""

    __tablename__ = "controlled_proof_exit_recoveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_controlled_proof_exit_recoveries_idempotency"),
        CheckConstraint(
            "status IN ('AUTHORIZED','IN_PROGRESS','COMPLETED','BLOCKED','EXPIRED')",
            name="ck_controlled_proof_exit_recoveries_status",
        ),
        Index(
            "uq_controlled_proof_exit_recoveries_active_proof",
            "proof_id", unique=True,
            postgresql_where=text("status IN ('AUTHORIZED','IN_PROGRESS')"),
            sqlite_where=text("status IN ('AUTHORIZED','IN_PROGRESS')"),
        ),
        Index("ix_controlled_proof_exit_recoveries_proof", "proof_id"),
    )

    recovery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controlled_proof_runs.proof_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'AUTHORIZED'"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_by: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
