"""add autonomous cycle run persistence

Revision ID: 20260712_0031
Revises: 20260712_0030
Create Date: 2026-07-12 16:20:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0031"
down_revision: str | None = "20260712_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_cycle_runs",
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'NOT_STARTED'")),
        sa.Column("evaluation_stage", sa.Text(), nullable=True),
        sa.Column("termination_stage", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("deterministic_explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cycle_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposed_action", sa.Text(), nullable=True),
        sa.Column("mandate_verdict", sa.Text(), nullable=True),
        sa.Column("risk_verdict", sa.Text(), nullable=True),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mandate_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("software_build_version", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mandate_version_id"], ["autonomous_capital_mandate_versions.mandate_version_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decision_record_id"], ["decision_records.decision_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["preview_id"], ["crypto_order_previews.crypto_order_preview_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mandate_evaluation_id"], ["autonomous_capital_mandate_evaluations.evaluation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("cycle_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_autonomous_cycle_runs_idempotency_key"),
    )
    op.create_index("ix_autonomous_cycle_runs_mandate_created", "autonomous_cycle_runs", ["mandate_id", "started_at"])
    op.create_index("ix_autonomous_cycle_runs_state", "autonomous_cycle_runs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_autonomous_cycle_runs_state", table_name="autonomous_cycle_runs")
    op.drop_index("ix_autonomous_cycle_runs_mandate_created", table_name="autonomous_cycle_runs")
    op.drop_table("autonomous_cycle_runs")
