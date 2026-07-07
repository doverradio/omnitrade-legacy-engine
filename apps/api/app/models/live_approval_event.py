from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveApprovalEvent(Base):
    __tablename__ = "live_approval_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_approval_events_idempotency_key"),
        UniqueConstraint("event_hash", name="uq_live_approval_events_event_hash"),
        UniqueConstraint("live_trading_profile_id", "sequence_number", name="uq_live_approval_events_sequence"),
        CheckConstraint(
            "event_type IN ('approval_granted','approval_revoked','approval_suspended','approval_renewed','checkpoint_evaluated')",
            name="ck_live_approval_events_event_type",
        ),
        CheckConstraint(
            "checkpoint_type IN ('first_live_enablement','material_control_change')",
            name="ck_live_approval_events_checkpoint_type",
        ),
        CheckConstraint(
            "approval_state IN ('approved','revoked','suspended','expired')",
            name="ck_live_approval_events_approval_state",
        ),
        CheckConstraint("sequence_number >= 1", name="ck_live_approval_events_sequence_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_trading_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_type: Mapped[str] = mapped_column(Text, nullable=False)
    approval_state: Mapped[str] = mapped_column(Text, nullable=False)
    approver_id: Mapped[str] = mapped_column(Text, nullable=False)
    approver_role: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    approval_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveApprovalEvent, "before_update", propagate=True)
def _prevent_live_approval_event_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveApprovalEvent,
) -> None:
    raise ValueError("live_approval_events is append-only and does not support updates")


@event.listens_for(LiveApprovalEvent, "before_delete", propagate=True)
def _prevent_live_approval_event_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveApprovalEvent,
) -> None:
    raise ValueError("live_approval_events is append-only and does not support deletes")