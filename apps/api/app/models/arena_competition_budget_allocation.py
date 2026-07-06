from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaCompetitionBudgetAllocation(Base):
    __tablename__ = "arena_competition_budget_allocations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_competition_budget_allocations_idempotency_key"),
        CheckConstraint("competition_budget >= 0", name="ck_arena_competition_budget_allocations_non_negative"),
        CheckConstraint("paper_only = true", name="ck_arena_competition_budget_allocations_paper_only_true"),
        CheckConstraint(
            "live_capital_allocation = false",
            name="ck_arena_competition_budget_allocations_live_false",
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
    paper_portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    master_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    competition_budget: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
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


@event.listens_for(ArenaCompetitionBudgetAllocation, "before_update", propagate=True)
def _prevent_arena_competition_budget_allocation_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaCompetitionBudgetAllocation,
) -> None:
    raise ValueError("arena_competition_budget_allocations is append-only and does not support updates")


@event.listens_for(ArenaCompetitionBudgetAllocation, "before_delete", propagate=True)
def _prevent_arena_competition_budget_allocation_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaCompetitionBudgetAllocation,
) -> None:
    raise ValueError("arena_competition_budget_allocations is append-only and does not support deletes")