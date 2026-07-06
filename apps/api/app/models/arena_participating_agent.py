from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ArenaParticipatingAgent(Base):
    __tablename__ = "arena_participating_agents"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_participating_agents_idempotency_key"),
        UniqueConstraint("agent_identity", name="uq_arena_participating_agents_identity"),
        CheckConstraint(
            "agent_role IN ('participant','challenger','benchmark')",
            name="ck_arena_participating_agents_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_identity: Mapped[str] = mapped_column(Text, nullable=False)
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    agent_role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'participant'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    competition = relationship("ArenaCompetition", back_populates="participating_agents")


@event.listens_for(ArenaParticipatingAgent, "before_update", propagate=True)
def _prevent_arena_participating_agent_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaParticipatingAgent,
) -> None:
    raise ValueError("arena_participating_agents is append-only and does not support updates")


@event.listens_for(ArenaParticipatingAgent, "before_delete", propagate=True)
def _prevent_arena_participating_agent_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaParticipatingAgent,
) -> None:
    raise ValueError("arena_participating_agents is append-only and does not support deletes")