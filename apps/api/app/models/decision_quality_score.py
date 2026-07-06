from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionQualityScore(Base):
    __tablename__ = "decision_quality_scores"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_quality_scores_idempotency_key"),
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
    scoring_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    composite_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    component_scores: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    weight_profile: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    decision_record = relationship("DecisionRecord", back_populates="quality_scores")


@event.listens_for(DecisionQualityScore, "before_update", propagate=True)
def _prevent_decision_quality_score_update(
    _mapper: Any,
    _connection: Any,
    _target: DecisionQualityScore,
) -> None:
    raise ValueError("decision_quality_scores is append-only and does not support updates")


@event.listens_for(DecisionQualityScore, "before_delete", propagate=True)
def _prevent_decision_quality_score_delete(
    _mapper: Any,
    _connection: Any,
    _target: DecisionQualityScore,
) -> None:
    raise ValueError("decision_quality_scores is append-only and does not support deletes")
