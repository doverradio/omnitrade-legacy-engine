"""add strategy roster proposal outcomes table

Revision ID: 20260713_0034
Revises: 20260713_0033
Create Date: 2026-07-13 23:55:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_0034"
down_revision: str | None = "20260713_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_roster_proposal_outcomes",
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roster_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("strategy_slug", sa.Text(), nullable=False),
        sa.Column("strategy_identity", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("proposal_evaluation_status", sa.Text(), nullable=False),
        sa.Column("horizon_label", sa.Text(), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("proposal_candle_close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(), nullable=False),
        sa.Column("exit_price", sa.Numeric(), nullable=False),
        sa.Column("market_return_pct", sa.Numeric(), nullable=False),
        sa.Column("buy_raw_return_pct", sa.Numeric(), nullable=False),
        sa.Column("buy_fee_adjusted_return_pct", sa.Numeric(), nullable=False),
        sa.Column("sell_raw_return_pct", sa.Numeric(), nullable=False),
        sa.Column("sell_fee_adjusted_return_pct", sa.Numeric(), nullable=False),
        sa.Column("actual_raw_return_pct", sa.Numeric(), nullable=True),
        sa.Column("actual_fee_adjusted_return_pct", sa.Numeric(), nullable=True),
        sa.Column("mfe_pct", sa.Numeric(), nullable=True),
        sa.Column("mae_pct", sa.Numeric(), nullable=True),
        sa.Column("actual_action_correct", sa.Boolean(), nullable=True),
        sa.Column("evaluation_completed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("evaluation_state", sa.Text(), nullable=False),
        sa.Column("evaluation_reason", sa.Text(), nullable=True),
        sa.Column("market_move", sa.Text(), nullable=False),
        sa.Column("regime_trend", sa.Text(), nullable=False),
        sa.Column("regime_volatility", sa.Text(), nullable=False),
        sa.Column("regime_range", sa.Text(), nullable=False),
        sa.Column("fee_bps", sa.Numeric(), nullable=False),
        sa.Column("hold_buy_threshold_pct", sa.Numeric(), nullable=False),
        sa.Column("hold_sell_threshold_pct", sa.Numeric(), nullable=False),
        sa.Column("execution_mode", sa.Text(), nullable=False, server_default=sa.text("'SHADOW'")),
        sa.Column("live_submission_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["proposal_id"], ["strategy_roster_proposals.proposal_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["roster_run_id"], ["strategy_roster_runs.roster_run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_roster_outcomes_idempotency_key"),
        sa.UniqueConstraint("proposal_id", "horizon_minutes", name="uq_roster_outcomes_proposal_horizon"),
        sa.CheckConstraint("horizon_label IN ('15m','1h','4h','24h')", name="ck_roster_outcomes_horizon_label"),
        sa.CheckConstraint("horizon_minutes IN (15,60,240,1440)", name="ck_roster_outcomes_horizon_minutes"),
        sa.CheckConstraint("action IN ('BUY','SELL','HOLD')", name="ck_roster_outcomes_action"),
        sa.CheckConstraint("proposal_evaluation_status IN ('EVALUATED','INSUFFICIENT_CONTEXT','FAILED')", name="ck_roster_outcomes_eval_status"),
        sa.CheckConstraint("evaluation_state IN ('RESOLVED','PROPOSAL_NOT_EVALUATED')", name="ck_roster_outcomes_state"),
        sa.CheckConstraint("market_move IN ('UP','DOWN','SIDEWAYS')", name="ck_roster_outcomes_market_move"),
        sa.CheckConstraint("regime_trend IN ('TRENDING','RANGING')", name="ck_roster_outcomes_regime_trend"),
        sa.CheckConstraint("regime_volatility IN ('HIGH_VOLATILITY','LOW_VOLATILITY')", name="ck_roster_outcomes_regime_volatility"),
        sa.CheckConstraint("regime_range IN ('EXPANSION','COMPRESSION')", name="ck_roster_outcomes_regime_range"),
        sa.CheckConstraint("execution_mode = 'SHADOW'", name="ck_roster_outcomes_exec_mode"),
        sa.CheckConstraint("live_submission_allowed = false", name="ck_roster_outcomes_live_disabled"),
    )
    op.create_index(
        "ix_roster_outcomes_strategy_horizon",
        "strategy_roster_proposal_outcomes",
        ["strategy_slug", "horizon_minutes", "evaluated_at"],
    )
    op.create_index("ix_roster_outcomes_proposal", "strategy_roster_proposal_outcomes", ["proposal_id"])
    op.create_index("ix_roster_outcomes_roster_run", "strategy_roster_proposal_outcomes", ["roster_run_id"])


def downgrade() -> None:
    op.drop_index("ix_roster_outcomes_roster_run", table_name="strategy_roster_proposal_outcomes")
    op.drop_index("ix_roster_outcomes_proposal", table_name="strategy_roster_proposal_outcomes")
    op.drop_index("ix_roster_outcomes_strategy_horizon", table_name="strategy_roster_proposal_outcomes")
    op.drop_table("strategy_roster_proposal_outcomes")
