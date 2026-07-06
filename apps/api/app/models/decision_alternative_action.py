from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionAlternativeAction(Base):
    __tablename__ = "decision_alternative_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_alternative_actions_idempotency_key"),
        CheckConstraint("chosen_action IN ('buy','sell','wait')", name="ck_decision_alternative_actions_chosen_action"),
        CheckConstraint("alternative_action IN ('buy','sell','wait')", name="ck_decision_alternative_actions_alternative_action"),
        CheckConstraint("chosen_action <> alternative_action", name="ck_decision_alternative_actions_distinct_actions"),
        CheckConstraint(
            "availability_state IN ('known','unknown','unavailable')",
            name="ck_decision_alternative_actions_availability_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.decision_id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    chosen_action: Mapped[str] = mapped_column(Text, nullable=False)
    alternative_action: Mapped[str] = mapped_column(Text, nullable=False)
    reference_horizon_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comparison_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    availability_state: Mapped[str] = mapped_column(Text, nullable=False)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    decision_record = relationship("DecisionRecord", back_populates="alternative_actions")


@event.listens_for(DecisionAlternativeAction, "before_update", propagate=True)
def _prevent_decision_alternative_action_update(
    _mapper: Any,
    _connection: Any,
    _target: DecisionAlternativeAction,
) -> None:
    raise ValueError("decision_alternative_actions is append-only and does not support updates")


@event.listens_for(DecisionAlternativeAction, "before_delete", propagate=True)
def _prevent_decision_alternative_action_delete(
    _mapper: Any,
    _connection: Any,
    _target: DecisionAlternativeAction,
) -> None:
    raise ValueError("decision_alternative_actions is append-only and does not support deletes")
