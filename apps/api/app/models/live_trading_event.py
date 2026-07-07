from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveTradingEvent(Base):
    __tablename__ = "live_trading_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_trading_events_idempotency_key"),
        UniqueConstraint("event_hash", name="uq_live_trading_events_event_hash"),
        UniqueConstraint("live_trading_profile_id", "sequence_number", name="uq_live_trading_events_sequence"),
        CheckConstraint(
            "event_type IN ('registration_created','registration_replayed','readiness_state_changed','provenance_recorded')",
            name="ck_live_trading_events_event_type",
        ),
        CheckConstraint(
            "from_state IS NULL OR from_state IN ('draft','pending_approval','approved','enabled','suspended')",
            name="ck_live_trading_events_from_state",
        ),
        CheckConstraint(
            "to_state IN ('draft','pending_approval','approved','enabled','suspended')",
            name="ck_live_trading_events_to_state",
        ),
        CheckConstraint(
            "operating_mode IN ('paper','live')",
            name="ck_live_trading_events_operating_mode",
        ),
        CheckConstraint(
            "paper_default_mode = true",
            name="ck_live_trading_events_paper_default_true",
        ),
        CheckConstraint(
            "risk_authority_model = 'risk_engine_final'",
            name="ck_live_trading_events_risk_authority_model",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR live_opt_in = true)",
            name="ck_live_trading_events_live_requires_opt_in",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR governance_approved = true)",
            name="ck_live_trading_events_live_requires_governance_approval",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="ck_live_trading_events_sequence_number",
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
    from_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    operating_mode: Mapped[str] = mapped_column(Text, nullable=False)
    paper_default_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    live_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    governance_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    risk_authority_model: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'risk_engine_final'"),
    )
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveTradingEvent, "before_update", propagate=True)
def _prevent_live_trading_event_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveTradingEvent,
) -> None:
    raise ValueError("live_trading_events is append-only and does not support updates")


@event.listens_for(LiveTradingEvent, "before_delete", propagate=True)
def _prevent_live_trading_event_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveTradingEvent,
) -> None:
    raise ValueError("live_trading_events is append-only and does not support deletes")
