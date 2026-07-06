from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ArenaCompetition(Base):
    __tablename__ = "arena_competitions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_competitions_idempotency_key"),
        UniqueConstraint("competition_identity", name="uq_arena_competitions_identity"),
        CheckConstraint(
            "status IN ('planned','active','completed','archived')",
            name="ck_arena_competitions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    competition_identity: Mapped[str] = mapped_column(Text, nullable=False)
    master_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    paper_portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'planned'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    tournaments = relationship(
        "ArenaTournament",
        back_populates="competition",
        cascade="all, delete-orphan",
    )
    participating_agents = relationship(
        "ArenaParticipatingAgent",
        back_populates="competition",
        cascade="all, delete-orphan",
    )


@event.listens_for(ArenaCompetition, "before_update", propagate=True)
def _prevent_arena_competition_update(_mapper: Any, _connection: Any, _target: ArenaCompetition) -> None:
    raise ValueError("arena_competitions is append-only and does not support updates")


@event.listens_for(ArenaCompetition, "before_delete", propagate=True)
def _prevent_arena_competition_delete(_mapper: Any, _connection: Any, _target: ArenaCompetition) -> None:
    raise ValueError("arena_competitions is append-only and does not support deletes")