from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveResilienceEvent(Base):
    __tablename__ = "live_resilience_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_resilience_events_idempotency_key"),
        UniqueConstraint("event_hash", name="uq_live_resilience_events_event_hash"),
        UniqueConstraint("live_trading_profile_id", "sequence_number", name="uq_live_resilience_events_sequence"),
        CheckConstraint(
            "event_type IN ('kill_switch_engaged','emergency_stop_engaged','outage_detected','recovery_requested','recovery_approved','recovery_rejected')",
            name="ck_live_resilience_events_event_type",
        ),
        CheckConstraint("sequence_number >= 1", name="ck_live_resilience_events_sequence_number"),
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
    provider_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    submission_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    kill_switch_engaged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    outage_detected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ambiguity_detected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reapproval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveResilienceEvent, "before_update", propagate=True)
def _prevent_live_resilience_event_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveResilienceEvent,
) -> None:
    raise ValueError("live_resilience_events is append-only and does not support updates")


@event.listens_for(LiveResilienceEvent, "before_delete", propagate=True)
def _prevent_live_resilience_event_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveResilienceEvent,
) -> None:
    raise ValueError("live_resilience_events is append-only and does not support deletes")
