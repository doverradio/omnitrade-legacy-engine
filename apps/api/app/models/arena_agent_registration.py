from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaAgentRegistration(Base):
    __tablename__ = "arena_agent_registrations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_agent_registrations_idempotency_key"),
        UniqueConstraint("registration_hash", name="uq_arena_agent_registrations_registration_hash"),
        UniqueConstraint("agent_id", "version_id", name="uq_arena_agent_registrations_agent_version"),
        CheckConstraint(
            "eligibility_status IN ('accepted','rejected')",
            name="ck_arena_agent_registrations_eligibility_status",
        ),
        CheckConstraint(
            "((eligibility_status = 'accepted' AND rejection_reason IS NULL) OR "
            "(eligibility_status = 'rejected' AND rejection_reason IS NOT NULL))",
            name="ck_arena_agent_registrations_rejection_reason",
        ),
        CheckConstraint("paper_only_eligible = true", name="ck_arena_agent_registrations_paper_only_true"),
        CheckConstraint("live_capital_eligible = false", name="ck_arena_agent_registrations_live_capital_false"),
        CheckConstraint("human_governed = true", name="ck_arena_agent_registrations_human_governed_true"),
        CheckConstraint(
            "autonomous_self_modifying = false",
            name="ck_arena_agent_registrations_non_autonomous",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    semantic_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    provenance_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    registration_source: Mapped[str] = mapped_column(Text, nullable=False)
    registration_hash: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    paper_only_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    live_capital_eligible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    human_governed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    autonomous_self_modifying: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    eligibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


@event.listens_for(ArenaAgentRegistration, "before_update", propagate=True)
def _prevent_arena_agent_registration_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaAgentRegistration,
) -> None:
    raise ValueError("arena_agent_registrations is append-only and does not support updates")


@event.listens_for(ArenaAgentRegistration, "before_delete", propagate=True)
def _prevent_arena_agent_registration_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaAgentRegistration,
) -> None:
    raise ValueError("arena_agent_registrations is append-only and does not support deletes")