from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Numeric, Text, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asset: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    market_regime: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    indicators: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    signal_strength: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    supporting_strategies: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    opposing_strategies: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    risk_adjustments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    expected_risk: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expected_reward: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    position_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    trade_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trade_rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    exit_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pnl: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    duration: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_trade_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    lessons_learned: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    ai_reflection: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    future_tags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    confidence_calibration: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision_snapshot = relationship(
        "DecisionSnapshot",
        uselist=False,
        back_populates="decision_record",
        cascade="all, delete-orphan",
    )


@event.listens_for(DecisionRecord, "before_update", propagate=True)
def _prevent_decision_record_update(_mapper: Any, _connection: Any, _target: DecisionRecord) -> None:
    raise ValueError("decision_records is append-only and does not support updates")


@event.listens_for(DecisionRecord, "before_delete", propagate=True)
def _prevent_decision_record_delete(_mapper: Any, _connection: Any, _target: DecisionRecord) -> None:
    raise ValueError("decision_records is append-only and does not support deletes")
