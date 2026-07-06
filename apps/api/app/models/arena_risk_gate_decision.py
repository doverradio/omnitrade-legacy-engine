from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArenaRiskGateDecision(Base):
    __tablename__ = "arena_risk_gate_decisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_arena_risk_gate_decisions_idempotency_key"),
        CheckConstraint(
            "decision_action IN ('approve','resize','reject')",
            name="ck_arena_risk_gate_decisions_action",
        ),
        CheckConstraint("approved_quantity >= 0", name="ck_arena_risk_gate_decisions_non_negative_qty"),
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
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("arena_cycle_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tournament_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_action: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    risk_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(ArenaRiskGateDecision, "before_update", propagate=True)
def _prevent_arena_risk_gate_decision_update(
    _mapper: Any,
    _connection: Any,
    _target: ArenaRiskGateDecision,
) -> None:
    raise ValueError("arena_risk_gate_decisions is append-only and does not support updates")


@event.listens_for(ArenaRiskGateDecision, "before_delete", propagate=True)
def _prevent_arena_risk_gate_decision_delete(
    _mapper: Any,
    _connection: Any,
    _target: ArenaRiskGateDecision,
) -> None:
    raise ValueError("arena_risk_gate_decisions is append-only and does not support deletes")