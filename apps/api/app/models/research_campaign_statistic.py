from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchCampaignStatistic(Base):
    __tablename__ = "research_campaign_statistics"
    __table_args__ = (
        UniqueConstraint("campaign_id", name="uq_research_campaign_statistics_campaign_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    )
    laboratory_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    candidates_generated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    candidates_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    best_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_candidates.candidate_id", ondelete="SET NULL"),
        nullable=True,
    )
    best_quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_champion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
