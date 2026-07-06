"""add decision record and decision snapshot tables

Revision ID: 20260706_0007
Revises: 20260706_0006
Create Date: 2026-07-06 04:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0007"
down_revision: str | None = "20260706_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_records",
        sa.Column("decision_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("market_regime", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("indicators", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signal_strength", sa.Numeric(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("supporting_strategies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("opposing_strategies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_adjustments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_risk", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expected_reward", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("position_size", sa.Numeric(), nullable=True),
        sa.Column("trade_accepted", sa.Boolean(), nullable=False),
        sa.Column("trade_rejected_reason", sa.Text(), nullable=True),
        sa.Column("execution_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exit_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pnl", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("post_trade_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("lessons_learned", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_reflection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("future_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence_calibration", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=True),
        sa.Column("human_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index("idx_decision_records_timestamp", "decision_records", ["timestamp"], unique=False)

    op.create_table(
        "decision_snapshots",
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("ohlcv_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("indicators", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("market_regime", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("volatility", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("spread_liquidity_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("strategy_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_position_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("open_trades", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portfolio_exposure", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameter_set_version", sa.Text(), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("ai_model_version", sa.Text(), nullable=False),
        sa.Column("decision_engine_version", sa.Text(), nullable=False),
        sa.Column("configuration_version", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("decision_id"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_records_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_records is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_records_mutation
        BEFORE UPDATE OR DELETE ON decision_records
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_records_mutation();
        """
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_snapshots_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_snapshots is immutable and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_snapshots_mutation
        BEFORE UPDATE OR DELETE ON decision_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_snapshots_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_decision_snapshots_mutation ON decision_snapshots")
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_snapshots_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_decision_records_mutation ON decision_records")
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_records_mutation")

    op.drop_table("decision_snapshots")
    op.drop_index("idx_decision_records_timestamp", table_name="decision_records")
    op.drop_table("decision_records")
