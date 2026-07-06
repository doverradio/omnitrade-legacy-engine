from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionCounterfactualResult(Base):
    __tablename__ = "decision_counterfactual_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_counterfactual_results_idempotency_key"),
        UniqueConstraint("decision_id", "horizon_minutes", name="uq_decision_counterfactual_results_decision_horizon"),
        CheckConstraint("horizon_label IN ('15m','1h','24h')", name="ck_decision_counterfactual_results_horizon_label"),
        CheckConstraint("horizon_minutes IN (15,60,1440)", name="ck_decision_counterfactual_results_horizon_minutes"),
        CheckConstraint("actual_action IN ('buy','sell','wait')", name="ck_decision_counterfactual_results_actual_action"),
        CheckConstraint(
            "best_action IS NULL OR best_action IN ('buy','sell','wait')",
            name="ck_decision_counterfactual_results_best_action",
        ),
        CheckConstraint(
            "evaluation_state IN ('resolved','unknown','unavailable')",
            name="ck_decision_counterfactual_results_evaluation_state",
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
    horizon_label: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asset_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    actual_action: Mapped[str] = mapped_column(Text, nullable=False)
    shadow_buy_return_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    shadow_sell_return_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    shadow_wait_return_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    best_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_action_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_state: Mapped[str] = mapped_column(Text, nullable=False)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson_tags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    decision_record = relationship("DecisionRecord", back_populates="counterfactual_results")


@event.listens_for(DecisionCounterfactualResult, "before_update", propagate=True)
def _prevent_decision_counterfactual_result_update(
    _mapper: Any,
    _connection: Any,
    _target: DecisionCounterfactualResult,
) -> None:
    raise ValueError("decision_counterfactual_results is append-only and does not support updates")


@event.listens_for(DecisionCounterfactualResult, "before_delete", propagate=True)
def _prevent_decision_counterfactual_result_delete(
    _mapper: Any,
    _connection: Any,
    _target: DecisionCounterfactualResult,
) -> None:
    raise ValueError("decision_counterfactual_results is append-only and does not support deletes")
