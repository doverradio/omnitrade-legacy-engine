from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveReconciliationEvent(Base):
    __tablename__ = "live_reconciliation_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_reconciliation_events_idempotency_key"),
        UniqueConstraint("event_hash", name="uq_live_reconciliation_events_event_hash"),
        UniqueConstraint("live_trading_profile_id", "sequence_number", name="uq_live_reconciliation_events_sequence"),
        Index("ix_lre_live_order", "live_crypto_order_id"),
        Index("ix_lre_campaign", "capital_campaign_id"),
        CheckConstraint(
            "event_type IN ('order_reconciled','fill_reconciled')",
            name="ck_live_reconciliation_events_event_type",
        ),
        CheckConstraint(
            "source_execution_event_type = 'execution_intent_created'",
            name="ck_live_reconciliation_events_source_execution_event_type",
        ),
        CheckConstraint(
            "reconciliation_status IN ('open','partially_filled','filled','canceled','rejected','reconciliation_required','unknown','conflict','balance_mismatch')",
            name="ck_live_reconciliation_events_reconciliation_status",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="ck_live_reconciliation_events_sequence_number",
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
    live_crypto_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="SET NULL"),
        nullable=True,
    )
    capital_campaign_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("capital_campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_execution_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_execution_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_execution_event_type: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_fill_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immutable_contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    provider_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveReconciliationEvent, "before_update", propagate=True)
def _prevent_live_reconciliation_event_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveReconciliationEvent,
) -> None:
    raise ValueError("live_reconciliation_events is append-only and does not support updates")


@event.listens_for(LiveReconciliationEvent, "before_delete", propagate=True)
def _prevent_live_reconciliation_event_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveReconciliationEvent,
) -> None:
    raise ValueError("live_reconciliation_events is append-only and does not support deletes")
