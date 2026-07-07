from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaComparisonRecord(Base):
    __tablename__ = "arena_comparison_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_comparison_records_idempotency_key"),
        CheckConstraint(
            "comparison_scope IN ('competition','tournament','cycle')",
            name="ck_arena_comparison_records_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    comparison_hash: Mapped[str] = mapped_column(Text, nullable=False)
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tournament_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    comparison_scope: Mapped[str] = mapped_column(Text, nullable=False)
    compared_agent_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    comparison_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    comparison_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaComparisonRecord, "before_update", propagate=True)
def _prevent_arena_comparison_record_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaComparisonRecord,
) -> None:
    raise ValueError("arena_comparison_records is append-only and does not support updates")


@event.listens_for(ArenaComparisonRecord, "before_delete", propagate=True)
def _prevent_arena_comparison_record_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaComparisonRecord,
) -> None:
    raise ValueError("arena_comparison_records is append-only and does not support deletes")
