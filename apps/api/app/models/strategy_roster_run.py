from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyRosterRun(Base):
    __tablename__ = "strategy_roster_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_strategy_roster_runs_idempotency_key"),
        UniqueConstraint("asset_id", "interval", "candle_close_time", "trigger", name="uq_strategy_roster_runs_candle_trigger"),
        CheckConstraint("execution_mode = 'SHADOW'", name="ck_strategy_roster_runs_exec_mode"),
        CheckConstraint("live_submission_allowed = false", name="ck_strategy_roster_runs_live_disabled"),
        CheckConstraint("strategies_requested_count >= 0", name="ck_strategy_roster_runs_req_count"),
        CheckConstraint("strategies_completed_count >= 0", name="ck_strategy_roster_runs_done_count"),
        CheckConstraint("strategies_failed_count >= 0", name="ck_strategy_roster_runs_fail_count"),
        Index("ix_strategy_roster_runs_candle", "asset_id", "interval", "candle_close_time"),
        Index("ix_strategy_roster_runs_created", "created_at"),
    )

    roster_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candle_close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SHADOW'"))
    live_submission_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    scheduled_cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_cycle_runs.cycle_id", ondelete="SET NULL"), nullable=True)
    strategies_requested: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    strategies_completed: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    strategies_failed: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    strategies_requested_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    strategies_completed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    strategies_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buy_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sell_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    hold_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
