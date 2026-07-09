"""add validation runs tables

Revision ID: 20260709_0017
Revises: 20260709_0016
Create Date: 2026-07-09 14:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260709_0017"
down_revision: str | None = "20260709_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validation_runs",
        sa.Column("validation_run_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paper_capital", sa.Numeric(), nullable=False),
        sa.Column("enabled_strategies", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("enabled_research_agents", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("enabled_research_features", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("health_score", sa.Integer(), nullable=True),
        sa.Column("result_status", sa.Text(), server_default=sa.text("'INCOMPLETE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','RUNNING','COMPLETED','FAILED','CANCELLED')", name="ck_validation_runs_status"),
        sa.CheckConstraint("result_status IN ('PASS','CONDITIONAL_PASS','FAIL','INCOMPLETE')", name="ck_validation_runs_result_status"),
        sa.PrimaryKeyConstraint("validation_run_id"),
    )
    op.create_index("ix_validation_runs_created_at", "validation_runs", ["created_at"])
    op.create_index("ix_validation_runs_status", "validation_runs", ["status"])

    op.create_table(
        "validation_run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.validation_run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_run_events_run_id", "validation_run_events", ["validation_run_id"])
    op.create_index("ix_validation_run_events_created_at", "validation_run_events", ["created_at"])

    op.create_table(
        "validation_run_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_type", sa.Text(), nullable=False),
        sa.Column("candles", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("signals", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("trades", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("decision_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("paper_equity", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("campaign_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("research_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("candidates_evaluated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("evolution_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("research_memory_growth", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("alerts_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.validation_run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_run_metrics_run_id", "validation_run_metrics", ["validation_run_id"])
    op.create_index("ix_validation_run_metrics_captured_at", "validation_run_metrics", ["captured_at"])

    op.create_table(
        "validation_run_scorecards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.validation_run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("validation_run_id", "category", name="uq_validation_run_scorecards_run_category"),
    )
    op.create_index("ix_validation_run_scorecards_run_id", "validation_run_scorecards", ["validation_run_id"])


def downgrade() -> None:
    op.drop_index("ix_validation_run_scorecards_run_id", table_name="validation_run_scorecards")
    op.drop_table("validation_run_scorecards")

    op.drop_index("ix_validation_run_metrics_captured_at", table_name="validation_run_metrics")
    op.drop_index("ix_validation_run_metrics_run_id", table_name="validation_run_metrics")
    op.drop_table("validation_run_metrics")

    op.drop_index("ix_validation_run_events_created_at", table_name="validation_run_events")
    op.drop_index("ix_validation_run_events_run_id", table_name="validation_run_events")
    op.drop_table("validation_run_events")

    op.drop_index("ix_validation_runs_status", table_name="validation_runs")
    op.drop_index("ix_validation_runs_created_at", table_name="validation_runs")
    op.drop_table("validation_runs")
