from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ArenaTournament(Base):
    __tablename__ = "arena_tournaments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_tournaments_idempotency_key"),
        UniqueConstraint("tournament_identity", name="uq_arena_tournaments_identity"),
        CheckConstraint(
            "status IN ('planned','active','completed','archived')",
            name="ck_arena_tournaments_status",
        ),
        CheckConstraint("sequence_number >= 1", name="ck_arena_tournaments_sequence_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    tournament_identity: Mapped[str] = mapped_column(Text, nullable=False)
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'planned'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    competition = relationship("ArenaCompetition", back_populates="tournaments")
    cycles = relationship(
        "ArenaCycle",
        back_populates="tournament",
        cascade="all, delete-orphan",
    )


@event.listens_for(ArenaTournament, "before_update", propagate=True)
def _prevent_arena_tournament_update(_mapper: Any, _connection: Any, _target: ArenaTournament) -> None:
    raise ValueError("arena_tournaments is append-only and does not support updates")


@event.listens_for(ArenaTournament, "before_delete", propagate=True)
def _prevent_arena_tournament_delete(_mapper: Any, _connection: Any, _target: ArenaTournament) -> None:
    raise ValueError("arena_tournaments is append-only and does not support deletes")