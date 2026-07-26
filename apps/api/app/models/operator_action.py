from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Kept in sync with app.services.operator_actions.registry's registered
# action types -- a DB-level fail-closed backstop, not the primary guard
# (the registry lookup in the service is what actually rejects unknown
# action_type values before a row is ever created).
_ACTION_TYPES = ("RUN_CONTROLLED_PROOF",)
_STATUSES = (
    "REQUESTED", "ACCEPTED", "IN_PROGRESS", "SUCCEEDED",
    "BLOCKED", "FAILED", "CANCELLED", "EXPIRED",
)


class OperatorAction(Base):
    """Generic operator control-plane action envelope. Never contains
    execution/orchestration logic itself -- each action_type delegates to an
    existing domain service (see app.services.operator_actions.registry)
    and this row only tracks the request, its idempotency, and a projection
    of the delegated resource's real state."""

    __tablename__ = "operator_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_operator_actions_idempotency_key"),
        CheckConstraint(f"action_type IN ({', '.join(repr(v) for v in _ACTION_TYPES)})", name="ck_operator_actions_action_type"),
        CheckConstraint(f"status IN ({', '.join(repr(v) for v in _STATUSES)})", name="ck_operator_actions_status"),
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'REQUESTED'"))
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    linked_resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
