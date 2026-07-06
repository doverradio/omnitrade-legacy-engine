from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaCycleProposal(Base):
    __tablename__ = "arena_cycle_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_cycle_proposals_idempotency_key"),
        CheckConstraint(
            "proposal_action IN ('buy','sell','wait')",
            name="ck_arena_cycle_proposals_action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tournament_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_action: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaCycleProposal, "before_update", propagate=True)
def _prevent_arena_cycle_proposal_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaCycleProposal,
) -> None:
    raise ValueError("arena_cycle_proposals is append-only and does not support updates")


@event.listens_for(ArenaCycleProposal, "before_delete", propagate=True)
def _prevent_arena_cycle_proposal_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaCycleProposal,
) -> None:
    raise ValueError("arena_cycle_proposals is append-only and does not support deletes")