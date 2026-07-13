"""add strategy roster shadow persistence tables

Revision ID: 20260713_0032
Revises: 20260712_0031
Create Date: 2026-07-13 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_0032"
down_revision: str | None = "20260712_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_roster_runs",
        sa.Column("roster_run_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("candle_open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.Text(), nullable=False, server_default=sa.text("'SHADOW'")),
        sa.Column("live_submission_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scheduled_cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategies_requested", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("strategies_completed", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("strategies_failed", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("strategies_requested_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("strategies_completed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("strategies_failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("buy_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sell_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("hold_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scheduled_cycle_id"], ["autonomous_cycle_runs.cycle_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("roster_run_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_strategy_roster_runs_idempotency_key"),
        sa.UniqueConstraint("asset_id", "interval", "candle_close_time", "trigger", name="uq_strategy_roster_runs_candle_trigger"),
        sa.CheckConstraint("execution_mode = 'SHADOW'", name="ck_strategy_roster_runs_exec_mode"),
        sa.CheckConstraint("live_submission_allowed = false", name="ck_strategy_roster_runs_live_disabled"),
        sa.CheckConstraint("strategies_requested_count >= 0", name="ck_strategy_roster_runs_req_count"),
        sa.CheckConstraint("strategies_completed_count >= 0", name="ck_strategy_roster_runs_done_count"),
        sa.CheckConstraint("strategies_failed_count >= 0", name="ck_strategy_roster_runs_fail_count"),
    )
    op.create_index("ix_strategy_roster_runs_candle", "strategy_roster_runs", ["asset_id", "interval", "candle_close_time"])
    op.create_index("ix_strategy_roster_runs_created", "strategy_roster_runs", ["created_at"])

    op.create_table(
        "strategy_roster_proposals",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("roster_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("candle_open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_slug", sa.Text(), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("strategy_identity", sa.Text(), nullable=False),
        sa.Column("parameter_set_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parameter_set_identity", sa.Text(), nullable=False),
        sa.Column("scheduled_cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("evaluation_status", sa.Text(), nullable=False),
        sa.Column("strength", sa.Numeric(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("deterministic_explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("indicator_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("market_window_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("minimum_history_required", sa.Integer(), nullable=False),
        sa.Column("history_candle_count", sa.Integer(), nullable=False),
        sa.Column("current_incomplete_candle_excluded", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("execution_mode", sa.Text(), nullable=False, server_default=sa.text("'SHADOW'")),
        sa.Column("live_submission_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["roster_run_id"], ["strategy_roster_runs.roster_run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parameter_set_id"], ["parameter_sets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scheduled_cycle_id"], ["autonomous_cycle_runs.cycle_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("proposal_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_strategy_roster_props_idempotency_key"),
        sa.UniqueConstraint(
            "asset_id",
            "interval",
            "candle_close_time",
            "strategy_identity",
            "parameter_set_identity",
            name="uq_strategy_roster_props_unique_proposal",
        ),
        sa.CheckConstraint("action IN ('BUY','SELL','HOLD')", name="ck_strategy_roster_props_action"),
        sa.CheckConstraint("evaluation_status IN ('EVALUATED','INSUFFICIENT_CONTEXT','FAILED')", name="ck_strategy_roster_props_eval_status"),
        sa.CheckConstraint("execution_mode = 'SHADOW'", name="ck_strategy_roster_props_exec_mode"),
        sa.CheckConstraint("live_submission_allowed = false", name="ck_strategy_roster_props_live_disabled"),
        sa.CheckConstraint("minimum_history_required >= 0", name="ck_strategy_roster_props_min_history"),
        sa.CheckConstraint("history_candle_count >= 0", name="ck_strategy_roster_props_hist_count"),
    )
    op.create_index("ix_strategy_roster_props_run", "strategy_roster_proposals", ["roster_run_id"])
    op.create_index("ix_strategy_roster_props_candle", "strategy_roster_proposals", ["asset_id", "interval", "candle_close_time"])


def downgrade() -> None:
    op.drop_index("ix_strategy_roster_props_candle", table_name="strategy_roster_proposals")
    op.drop_index("ix_strategy_roster_props_run", table_name="strategy_roster_proposals")
    op.drop_table("strategy_roster_proposals")

    op.drop_index("ix_strategy_roster_runs_created", table_name="strategy_roster_runs")
    op.drop_index("ix_strategy_roster_runs_candle", table_name="strategy_roster_runs")
    op.drop_table("strategy_roster_runs")
