from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaLeaderboardSnapshot(Base):
    __tablename__ = "arena_leaderboard_snapshots"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_leaderboard_snapshots_idempotency_key"),
        CheckConstraint(
            "snapshot_scope IN ('competition','tournament','cycle')",
            name="ck_arena_leaderboard_snapshots_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    ranking_hash: Mapped[str] = mapped_column(Text, nullable=False)
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tournament_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    snapshot_scope: Mapped[str] = mapped_column(Text, nullable=False)
    ranking_methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    filter_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ranking_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaLeaderboardSnapshot, "before_update", propagate=True)
def _prevent_arena_leaderboard_snapshot_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaLeaderboardSnapshot,
) -> None:
    raise ValueError("arena_leaderboard_snapshots is append-only and does not support updates")


@event.listens_for(ArenaLeaderboardSnapshot, "before_delete", propagate=True)
def _prevent_arena_leaderboard_snapshot_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaLeaderboardSnapshot,
) -> None:
    raise ValueError("arena_leaderboard_snapshots is append-only and does not support deletes")
