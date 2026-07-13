from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyRosterProposal(Base):
    __tablename__ = "strategy_roster_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_strategy_roster_props_idempotency_key"),
        UniqueConstraint(
            "asset_id",
            "interval",
            "candle_close_time",
            "strategy_identity",
            "parameter_set_identity",
            name="uq_strategy_roster_props_unique_proposal",
        ),
        CheckConstraint("action IN ('BUY','SELL','HOLD')", name="ck_strategy_roster_props_action"),
        CheckConstraint("evaluation_status IN ('EVALUATED','INSUFFICIENT_CONTEXT','FAILED')", name="ck_strategy_roster_props_eval_status"),
        CheckConstraint("execution_mode = 'SHADOW'", name="ck_strategy_roster_props_exec_mode"),
        CheckConstraint("live_submission_allowed = false", name="ck_strategy_roster_props_live_disabled"),
        CheckConstraint("minimum_history_required >= 0", name="ck_strategy_roster_props_min_history"),
        CheckConstraint("history_candle_count >= 0", name="ck_strategy_roster_props_hist_count"),
        Index("ix_strategy_roster_props_run", "roster_run_id"),
        Index("ix_strategy_roster_props_candle", "asset_id", "interval", "candle_close_time"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    roster_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_roster_runs.roster_run_id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candle_close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True)
    strategy_slug: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_identity: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parameter_sets.id", ondelete="SET NULL"), nullable=True)
    parameter_set_identity: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_cycle_runs.cycle_id", ondelete="SET NULL"), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_status: Mapped[str] = mapped_column(Text, nullable=False)
    strength: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    deterministic_explanation: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    indicator_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    market_window_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    minimum_history_required: Mapped[int] = mapped_column(Integer, nullable=False)
    history_candle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_incomplete_candle_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SHADOW'"))
    live_submission_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


@event.listens_for(StrategyRosterProposal, "before_update", propagate=True)
def _prevent_strategy_roster_proposal_update(_mapper: Any, _connection: Any, _target: StrategyRosterProposal) -> None:
    raise ValueError("strategy_roster_proposals is append-only and does not support updates")


@event.listens_for(StrategyRosterProposal, "before_delete", propagate=True)
def _prevent_strategy_roster_proposal_delete(_mapper: Any, _connection: Any, _target: StrategyRosterProposal) -> None:
    raise ValueError("strategy_roster_proposals is append-only and does not support deletes")
