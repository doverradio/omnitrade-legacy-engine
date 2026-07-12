from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousCapitalMandateAuthorization(Base):
    __tablename__ = "autonomous_capital_mandate_authorizations"
    __table_args__ = (
        CheckConstraint(
            "authorization_state IN ('PENDING','AUTHORIZED','REJECTED','REVOKED')",
            name="ck_ac_mandate_authorizations_state",
        ),
        CheckConstraint(
            "approval_result IN ('APPROVAL_REQUIRED_HUMAN','APPROVAL_SATISFIED_BY_ACTIVE_MANDATE')",
            name="ck_ac_mandate_authorizations_approval_result",
        ),
        UniqueConstraint("idempotency_key", name="uq_ac_mandate_authorizations_idempotency"),
        Index("ix_ac_mandate_authorizations_mandate", "mandate_id"),
        Index("ix_ac_mandate_authorizations_version", "mandate_version_id"),
    )

    mandate_authorization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandates.mandate_id", ondelete="CASCADE"), nullable=False)
    mandate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandate_versions.mandate_version_id", ondelete="CASCADE"), nullable=False)
    authorization_state: Mapped[str] = mapped_column(Text, nullable=False)
    approval_result: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'APPROVAL_REQUIRED_HUMAN'"))
    authorized_by_actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorization_method: Mapped[str] = mapped_column(Text, nullable=False)
    owner_acknowledgements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    authorization_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    deterministic_explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    audit_correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@event.listens_for(AutonomousCapitalMandateAuthorization, "before_update", propagate=True)
def _prevent_authorization_update(_mapper: Any, _connection: Any, _target: AutonomousCapitalMandateAuthorization) -> None:
    raise ValueError("mandate authorizations are append-only")


@event.listens_for(AutonomousCapitalMandateAuthorization, "before_delete", propagate=True)
def _prevent_authorization_delete(_mapper: Any, _connection: Any, _target: AutonomousCapitalMandateAuthorization) -> None:
    raise ValueError("mandate authorizations are append-only")
