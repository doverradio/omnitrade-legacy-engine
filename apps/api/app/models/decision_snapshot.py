from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, event
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DecisionSnapshot(Base):
    __tablename__ = "decision_snapshots"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.decision_id", ondelete="CASCADE"),
        primary_key=True,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asset: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    ohlcv_context: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    indicators: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    market_regime: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    volatility: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spread_liquidity_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    strategy_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    current_position_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    open_trades: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    portfolio_exposure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parameter_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    ai_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    decision_engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    configuration_version: Mapped[str] = mapped_column(Text, nullable=False)

    decision_record = relationship("DecisionRecord", back_populates="decision_snapshot")


@event.listens_for(DecisionSnapshot, "before_update", propagate=True)
def _prevent_decision_snapshot_update(_mapper: Any, _connection: Any, _target: DecisionSnapshot) -> None:
    raise ValueError("decision_snapshots is immutable and does not support updates")


@event.listens_for(DecisionSnapshot, "before_delete", propagate=True)
def _prevent_decision_snapshot_delete(_mapper: Any, _connection: Any, _target: DecisionSnapshot) -> None:
    raise ValueError("decision_snapshots is immutable and does not support deletes")
