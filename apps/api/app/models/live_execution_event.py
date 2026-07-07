from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveExecutionEvent(Base):
    __tablename__ = "live_execution_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_execution_events_idempotency_key"),
        UniqueConstraint("event_hash", name="uq_live_execution_events_event_hash"),
        UniqueConstraint("live_trading_profile_id", "sequence_number", name="uq_live_execution_events_sequence"),
        CheckConstraint(
            "event_type IN ('execution_intent_created','execution_intent_replayed','execution_blocked')",
            name="ck_live_execution_events_event_type",
        ),
        CheckConstraint(
            "operating_mode IN ('paper','live')",
            name="ck_live_execution_events_operating_mode",
        ),
        CheckConstraint(
            "paper_default_mode = true",
            name="ck_live_execution_events_paper_default_true",
        ),
        CheckConstraint(
            "risk_authority_model = 'risk_engine_final'",
            name="ck_live_execution_events_risk_authority_model",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="ck_live_execution_events_sequence_number",
        ),
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
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    risk_decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approval_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    audit_correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    operating_mode: Mapped[str] = mapped_column(Text, nullable=False)
    paper_default_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    risk_authority_model: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'risk_engine_final'"))
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveExecutionEvent, "before_update", propagate=True)
def _prevent_live_execution_event_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveExecutionEvent,
) -> None:
    raise ValueError("live_execution_events is append-only and does not support updates")


@event.listens_for(LiveExecutionEvent, "before_delete", propagate=True)
def _prevent_live_execution_event_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveExecutionEvent,
) -> None:
    raise ValueError("live_execution_events is append-only and does not support deletes")