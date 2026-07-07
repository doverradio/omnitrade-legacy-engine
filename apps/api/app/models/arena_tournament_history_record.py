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


class ArenaTournamentHistoryRecord(Base):
    __tablename__ = "arena_tournament_history_records"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_arena_tournament_history_records_idempotency_key",
        ),
        UniqueConstraint(
            "tournament_id",
            "sequence_number",
            name="uq_arena_tournament_history_records_sequence",
        ),
        CheckConstraint(
            "event_type IN ('scheduled','activated','completed','archived','standings_recorded')",
            name="ck_arena_tournament_history_records_event_type",
        ),
        CheckConstraint(
            "lifecycle_state IN ('planned','active','completed','archived')",
            name="ck_arena_tournament_history_records_lifecycle_state",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="ck_arena_tournament_history_records_sequence_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False)
    tournament_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_tournaments.id", ondelete="CASCADE"),
        nullable=False,
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    replay_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tie_break_rules: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    ordering_rules: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaTournamentHistoryRecord, "before_update", propagate=True)
def _prevent_arena_tournament_history_record_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaTournamentHistoryRecord,
) -> None:
    raise ValueError("arena_tournament_history_records is append-only and does not support updates")


@event.listens_for(ArenaTournamentHistoryRecord, "before_delete", propagate=True)
def _prevent_arena_tournament_history_record_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaTournamentHistoryRecord,
) -> None:
    raise ValueError("arena_tournament_history_records is append-only and does not support deletes")
