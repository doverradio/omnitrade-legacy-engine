from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionExperimentRecommendation(Base):
    __tablename__ = "decision_experiment_recommendations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_experiment_recommendations_idempotency_key"),
        CheckConstraint(
            "recommendation_type IN ('strategy_parameter_investigation','hypothesis_test','experiment_run','risk_observation','recurring_decision_pattern')",
            name="ck_decision_experiment_recommendations_type",
        ),
        CheckConstraint(
            "recommendation_category IN ('strategy','hypothesis','experiment','risk','pattern')",
            name="ck_decision_experiment_recommendations_category",
        ),
        CheckConstraint(
            "confidence_level IN ('low','medium','high')",
            name="ck_decision_experiment_recommendations_confidence",
        ),
        CheckConstraint(
            "expected_impact_level IN ('low','medium','high')",
            name="ck_decision_experiment_recommendations_impact",
        ),
        CheckConstraint(
            "required_human_review_level IN ('standard','priority','required')",
            name="ck_decision_experiment_recommendations_review",
        ),
        CheckConstraint(
            "evidence_state IN ('known','unknown','unavailable')",
            name="ck_decision_experiment_recommendations_evidence_state",
        ),
        CheckConstraint("advisory_only = true", name="ck_decision_experiment_recommendations_advisory_only"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_type: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_category: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_level: Mapped[str] = mapped_column(Text, nullable=False)
    expected_impact_level: Mapped[str] = mapped_column(Text, nullable=False)
    required_human_review_level: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    originating_decision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_experiment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_state: Mapped[str] = mapped_column(Text, nullable=False)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    advisory_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(DecisionExperimentRecommendation, "before_update", propagate=True)
def _prevent_decision_experiment_recommendation_update(
    _mapper: Any,
    _connection: Any,
    _target: DecisionExperimentRecommendation,
) -> None:
    raise ValueError("decision_experiment_recommendations is append-only and does not support updates")


@event.listens_for(DecisionExperimentRecommendation, "before_delete", propagate=True)
def _prevent_decision_experiment_recommendation_delete(
    _mapper: Any,
    _connection: Any,
    _target: DecisionExperimentRecommendation,
) -> None:
    raise ValueError("decision_experiment_recommendations is append-only and does not support deletes")
