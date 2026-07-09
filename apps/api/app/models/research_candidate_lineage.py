from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchCandidateLineage(Base):
    __tablename__ = "research_candidate_lineage"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_research_candidate_lineage_candidate_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_candidates.candidate_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_candidates.candidate_id", ondelete="CASCADE"),
        nullable=False,
    )
    mutation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameter_diff: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
