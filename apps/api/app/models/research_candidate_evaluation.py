from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchCandidateEvaluation(Base):
    __tablename__ = "research_candidate_evaluations"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_candidates.candidate_id", ondelete="CASCADE"),
        nullable=False,
    )
    laboratory_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_laboratory_runs.run_id", ondelete="SET NULL"),
        nullable=True,
    )
    replay_status: Mapped[str] = mapped_column(Text, nullable=False)
    decision_quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_coach_summary: Mapped[str] = mapped_column(Text, nullable=False)
    decision_intelligence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    tournament_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
