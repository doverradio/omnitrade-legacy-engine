from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyAggregateDecision(Base):
    """Append-only audit record for one Strategy Decision Aggregator run.

    Persists enough evidence to reconstruct exactly why the aggregator produced
    a given BUY/SELL/HOLD conclusion from the strategy roster: every considered
    strategy's raw proposal, its eligibility result, its applied weight, the
    weighted scores, the position state used, the thresholds in effect, and the
    final action -- separate from (and paired with) the DecisionRecord created
    for the same aggregate decision.
    """

    __tablename__ = "strategy_aggregate_decisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_strategy_aggregate_decisions_idempotency_key"),
        UniqueConstraint(
            "roster_run_id",
            "asset_id",
            "candle_close_time",
            "campaign_id",
            "campaign_version",
            name="uq_strategy_aggregate_decisions_scope",
        ),
        CheckConstraint("final_action IN ('BUY','SELL','HOLD')", name="ck_strategy_aggregate_decisions_action"),
        CheckConstraint("position_state IN ('FLAT','OPEN','UNKNOWN')", name="ck_strategy_aggregate_decisions_position_state"),
        CheckConstraint("eligible_strategy_count >= 0", name="ck_strategy_aggregate_decisions_eligible_count"),
        Index("ix_strategy_aggregate_decisions_run", "roster_run_id"),
        Index("ix_strategy_aggregate_decisions_campaign", "campaign_id", "campaign_version"),
        Index("ix_strategy_aggregate_decisions_candle", "asset_id", "candle_close_time"),
    )

    aggregate_decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    roster_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_roster_runs.roster_run_id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    candle_close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_contributions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    eligible_strategy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    weighted_buy_score: Mapped[Any] = mapped_column(Numeric, nullable=False)
    weighted_sell_score: Mapped[Any] = mapped_column(Numeric, nullable=False)
    weighted_hold_score: Mapped[Any] = mapped_column(Numeric, nullable=False)
    position_state: Mapped[str] = mapped_column(Text, nullable=False)
    thresholds_applied: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    final_action: Mapped[str] = mapped_column(Text, nullable=False)
    # Always the canonical AGGREGATE_STRATEGY_IDENTITY/AGGREGATE_STRATEGY_VERSION
    # (see app.services.strategy_roster.decision_aggregator) -- never an
    # individual contributor's identity, so an ensemble decision can never
    # become accidentally bound to an arbitrary contributor across cycles.
    primary_strategy_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_strategy_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Informational only: the single highest-weighted eligible contributor
    # toward the final action this cycle. Never used for coherence/continuity.
    dominant_contributor_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    deterministic_explanation: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("decision_records.decision_id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


@event.listens_for(StrategyAggregateDecision, "before_update", propagate=True)
def _prevent_strategy_aggregate_decision_update(_mapper: Any, _connection: Any, _target: StrategyAggregateDecision) -> None:
    raise ValueError("strategy_aggregate_decisions is append-only and does not support updates")


@event.listens_for(StrategyAggregateDecision, "before_delete", propagate=True)
def _prevent_strategy_aggregate_decision_delete(_mapper: Any, _connection: Any, _target: StrategyAggregateDecision) -> None:
    raise ValueError("strategy_aggregate_decisions is append-only and does not support deletes")
