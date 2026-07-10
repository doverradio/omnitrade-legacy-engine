"""add system intelligence snapshots

Revision ID: 20260710_0023
Revises: 20260709_0022
Create Date: 2026-07-10 10:35:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260710_0023"
down_revision: str | None = "20260709_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_intelligence_snapshots",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("data_completeness", sa.Integer(), nullable=True),
        sa.Column("market_awareness_score", sa.Integer(), nullable=True),
        sa.Column("decision_quality_score", sa.Integer(), nullable=True),
        sa.Column("execution_reliability_score", sa.Integer(), nullable=True),
        sa.Column("risk_discipline_score", sa.Integer(), nullable=True),
        sa.Column("research_progress_score", sa.Integer(), nullable=True),
        sa.Column("adaptation_rate_score", sa.Integer(), nullable=True),
        sa.Column("operational_health_score", sa.Integer(), nullable=True),
        sa.Column("capital_efficiency_score", sa.Integer(), nullable=True),
        sa.Column("profit_performance_score", sa.Integer(), nullable=True),
        sa.Column("paper_net_profit", sa.Numeric(), nullable=True),
        sa.Column("live_net_profit", sa.Numeric(), nullable=True),
        sa.Column("combined_net_profit", sa.Numeric(), nullable=True),
        sa.Column("paper_equity", sa.Numeric(), nullable=True),
        sa.Column("live_equity", sa.Numeric(), nullable=True),
        sa.Column("combined_equity", sa.Numeric(), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(), nullable=True),
        sa.Column("fees", sa.Numeric(), nullable=True),
        sa.Column("drawdown_percent", sa.Numeric(), nullable=True),
        sa.Column("source_counts", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("explanations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("annotations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'v1'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("bucket_start", "bucket_end", "schema_version", name="uq_system_intelligence_snapshots_bucket_version"),
    )
    op.create_index("ix_system_intelligence_snapshots_captured_at", "system_intelligence_snapshots", ["captured_at"])
    op.create_index("ix_system_intelligence_snapshots_bucket_start", "system_intelligence_snapshots", ["bucket_start"])
    op.create_index("ix_system_intelligence_snapshots_overall_score", "system_intelligence_snapshots", ["overall_score"])
    op.create_index("ix_system_intelligence_snapshots_schema_version", "system_intelligence_snapshots", ["schema_version"])


def downgrade() -> None:
    op.drop_index("ix_system_intelligence_snapshots_schema_version", table_name="system_intelligence_snapshots")
    op.drop_index("ix_system_intelligence_snapshots_overall_score", table_name="system_intelligence_snapshots")
    op.drop_index("ix_system_intelligence_snapshots_bucket_start", table_name="system_intelligence_snapshots")
    op.drop_index("ix_system_intelligence_snapshots_captured_at", table_name="system_intelligence_snapshots")
    op.drop_table("system_intelligence_snapshots")
