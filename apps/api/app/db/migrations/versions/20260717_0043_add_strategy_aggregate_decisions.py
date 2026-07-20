"""add strategy aggregate decisions table

Revision ID: 20260717_0043
Revises: 20260716_0042
Create Date: 2026-07-17 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717_0043"
down_revision: str | None = "20260716_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_aggregate_decisions",
        sa.Column("aggregate_decision_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("roster_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candle_close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("strategy_contributions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("eligible_strategy_count", sa.Integer(), nullable=False),
        sa.Column("weighted_buy_score", sa.Numeric(), nullable=False),
        sa.Column("weighted_sell_score", sa.Numeric(), nullable=False),
        sa.Column("weighted_hold_score", sa.Numeric(), nullable=False),
        sa.Column("position_state", sa.Text(), nullable=False),
        sa.Column("thresholds_applied", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("final_action", sa.Text(), nullable=False),
        sa.Column("primary_strategy_identity", sa.Text(), nullable=True),
        sa.Column("primary_strategy_version", sa.Text(), nullable=True),
        sa.Column("dominant_contributor_identity", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("deterministic_explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["roster_run_id"], ["strategy_roster_runs.roster_run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decision_record_id"], ["decision_records.decision_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("aggregate_decision_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_strategy_aggregate_decisions_idempotency_key"),
        sa.UniqueConstraint(
            "roster_run_id",
            "asset_id",
            "candle_close_time",
            "campaign_id",
            "campaign_version",
            name="uq_strategy_aggregate_decisions_scope",
        ),
        sa.CheckConstraint("final_action IN ('BUY','SELL','HOLD')", name="ck_strategy_aggregate_decisions_action"),
        sa.CheckConstraint("position_state IN ('FLAT','OPEN','UNKNOWN')", name="ck_strategy_aggregate_decisions_position_state"),
        sa.CheckConstraint("eligible_strategy_count >= 0", name="ck_strategy_aggregate_decisions_eligible_count"),
    )
    op.create_index("ix_strategy_aggregate_decisions_run", "strategy_aggregate_decisions", ["roster_run_id"])
    op.create_index("ix_strategy_aggregate_decisions_campaign", "strategy_aggregate_decisions", ["campaign_id", "campaign_version"])
    op.create_index("ix_strategy_aggregate_decisions_candle", "strategy_aggregate_decisions", ["asset_id", "candle_close_time"])


def downgrade() -> None:
    op.drop_index("ix_strategy_aggregate_decisions_candle", table_name="strategy_aggregate_decisions")
    op.drop_index("ix_strategy_aggregate_decisions_campaign", table_name="strategy_aggregate_decisions")
    op.drop_index("ix_strategy_aggregate_decisions_run", table_name="strategy_aggregate_decisions")
    op.drop_table("strategy_aggregate_decisions")
