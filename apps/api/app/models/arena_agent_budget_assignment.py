from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaAgentBudgetAssignment(Base):
    __tablename__ = "arena_agent_budget_assignments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_agent_budget_assignments_idempotency_key"),
        CheckConstraint("assigned_budget >= 0", name="ck_arena_agent_budget_assignments_non_negative"),
        CheckConstraint("paper_only = true", name="ck_arena_agent_budget_assignments_paper_only_true"),
        CheckConstraint(
            "live_capital_allocation = false",
            name="ck_arena_agent_budget_assignments_live_false",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    competition_budget_allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competition_budget_allocations.id", ondelete="CASCADE"),
        nullable=False,
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assigned_budget: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    paper_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    live_capital_allocation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaAgentBudgetAssignment, "before_update", propagate=True)
def _prevent_arena_agent_budget_assignment_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaAgentBudgetAssignment,
) -> None:
    raise ValueError("arena_agent_budget_assignments is append-only and does not support updates")


@event.listens_for(ArenaAgentBudgetAssignment, "before_delete", propagate=True)
def _prevent_arena_agent_budget_assignment_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaAgentBudgetAssignment,
) -> None:
    raise ValueError("arena_agent_budget_assignments is append-only and does not support deletes")