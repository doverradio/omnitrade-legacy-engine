from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ValidationRunMetric(Base):
    __tablename__ = "validation_run_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_runs.validation_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_type: Mapped[str] = mapped_column(Text, nullable=False)
    candles: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    signals: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    trades: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    decision_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    paper_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    campaign_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    research_candidates: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    candidates_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    evolution_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    research_memory_growth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    alerts_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
