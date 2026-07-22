from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyRosterProposalOutcome(Base):
    __tablename__ = "strategy_roster_proposal_outcomes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_roster_outcomes_idempotency_key"),
        UniqueConstraint("proposal_id", "horizon_minutes", name="uq_roster_outcomes_proposal_horizon"),
        CheckConstraint("horizon_label IN ('15m','1h','4h','24h')", name="ck_roster_outcomes_horizon_label"),
        CheckConstraint("horizon_minutes IN (15,60,240,1440)", name="ck_roster_outcomes_horizon_minutes"),
        CheckConstraint("action IN ('BUY','SELL','HOLD')", name="ck_roster_outcomes_action"),
        CheckConstraint("proposal_evaluation_status IN ('EVALUATED','INSUFFICIENT_CONTEXT','FAILED')", name="ck_roster_outcomes_eval_status"),
        CheckConstraint("evaluation_state IN ('RESOLVED','PROPOSAL_NOT_EVALUATED')", name="ck_roster_outcomes_state"),
        CheckConstraint("market_move IN ('UP','DOWN','SIDEWAYS')", name="ck_roster_outcomes_market_move"),
        CheckConstraint("regime_trend IN ('TRENDING','RANGING')", name="ck_roster_outcomes_regime_trend"),
        CheckConstraint("regime_volatility IN ('HIGH_VOLATILITY','LOW_VOLATILITY')", name="ck_roster_outcomes_regime_volatility"),
        CheckConstraint("regime_range IN ('EXPANSION','COMPRESSION')", name="ck_roster_outcomes_regime_range"),
        CheckConstraint("execution_mode = 'SHADOW'", name="ck_roster_outcomes_exec_mode"),
        CheckConstraint("live_submission_allowed = false", name="ck_roster_outcomes_live_disabled"),
        Index("ix_roster_outcomes_strategy_horizon", "strategy_slug", "horizon_minutes", "evaluated_at"),
        Index("ix_roster_outcomes_proposal", "proposal_id"),
        Index("ix_roster_outcomes_roster_run", "roster_run_id"),
        # Covers both the filter and the ORDER BY of
        # app.services.strategy_outcomes.service.fetch_strategy_scorecards --
        # without it, every scorecard fetch is a full sequential scan plus a
        # sort that gets slower as this table grows. See migration
        # 20260721_0044 for the confirmed production timeout this caused.
        Index(
            "ix_roster_outcomes_scorecard_lookup",
            "provider", "product_id", "interval", "evaluation_state", "strategy_slug", "evaluated_at", "outcome_id",
        ),
    )

    outcome_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_roster_proposals.proposal_id", ondelete="CASCADE"), nullable=False)
    roster_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_roster_runs.roster_run_id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_slug: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_identity: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_evaluation_status: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_label: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_candle_close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    market_return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    buy_raw_return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    buy_fee_adjusted_return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sell_raw_return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sell_fee_adjusted_return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    actual_raw_return_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    actual_fee_adjusted_return_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mae_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    actual_action_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    evaluation_state: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_move: Mapped[str] = mapped_column(Text, nullable=False)
    regime_trend: Mapped[str] = mapped_column(Text, nullable=False)
    regime_volatility: Mapped[str] = mapped_column(Text, nullable=False)
    regime_range: Mapped[str] = mapped_column(Text, nullable=False)
    fee_bps: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    hold_buy_threshold_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    hold_sell_threshold_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SHADOW'"))
    live_submission_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


@event.listens_for(StrategyRosterProposalOutcome, "before_update", propagate=True)
def _prevent_strategy_roster_proposal_outcome_update(
    _mapper: Any,
    _connection: Any,
    _target: StrategyRosterProposalOutcome,
) -> None:
    raise ValueError("strategy_roster_proposal_outcomes is append-only and does not support updates")


@event.listens_for(StrategyRosterProposalOutcome, "before_delete", propagate=True)
def _prevent_strategy_roster_proposal_outcome_delete(
    _mapper: Any,
    _connection: Any,
    _target: StrategyRosterProposalOutcome,
) -> None:
    raise ValueError("strategy_roster_proposal_outcomes is append-only and does not support deletes")
