from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemIntelligenceSnapshot(Base):
    __tablename__ = "system_intelligence_snapshots"
    __table_args__ = (
        UniqueConstraint("bucket_start", "bucket_end", "schema_version", name="uq_system_intelligence_snapshots_bucket_version"),
        Index("ix_system_intelligence_snapshots_captured_at", "captured_at"),
        Index("ix_system_intelligence_snapshots_bucket_start", "bucket_start"),
        Index("ix_system_intelligence_snapshots_overall_score", "overall_score"),
        Index("ix_system_intelligence_snapshots_schema_version", "schema_version"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall_score: Mapped[int | None] = mapped_column(nullable=True)
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_completeness: Mapped[int | None] = mapped_column(nullable=True)
    market_awareness_score: Mapped[int | None] = mapped_column(nullable=True)
    decision_quality_score: Mapped[int | None] = mapped_column(nullable=True)
    execution_reliability_score: Mapped[int | None] = mapped_column(nullable=True)
    risk_discipline_score: Mapped[int | None] = mapped_column(nullable=True)
    research_progress_score: Mapped[int | None] = mapped_column(nullable=True)
    adaptation_rate_score: Mapped[int | None] = mapped_column(nullable=True)
    operational_health_score: Mapped[int | None] = mapped_column(nullable=True)
    capital_efficiency_score: Mapped[int | None] = mapped_column(nullable=True)
    profit_performance_score: Mapped[int | None] = mapped_column(nullable=True)
    paper_net_profit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    live_net_profit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    combined_net_profit: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    paper_equity: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    live_equity: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    combined_equity: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    drawdown_percent: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    source_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    explanations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    annotations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
