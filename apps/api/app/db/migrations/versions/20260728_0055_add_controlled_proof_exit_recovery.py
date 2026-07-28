"""add controlled proof exit recovery authority

Revision ID: 20260728_0055
Revises: 20260727_0054
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0055"
down_revision: str | None = "20260727_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "controlled_proof_exit_recoveries",
        sa.Column("recovery_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("proof_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'AUTHORIZED'"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("authorized_by", sa.Text(), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('AUTHORIZED','IN_PROGRESS','COMPLETED','BLOCKED','EXPIRED')", name="ck_controlled_proof_exit_recoveries_status"),
        sa.ForeignKeyConstraint(["proof_id"], ["controlled_proof_runs.proof_id"]),
        sa.PrimaryKeyConstraint("recovery_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_controlled_proof_exit_recoveries_idempotency"),
    )
    op.create_index(
        "uq_controlled_proof_exit_recoveries_active_proof",
        "controlled_proof_exit_recoveries", ["proof_id"], unique=True,
        postgresql_where=sa.text("status IN ('AUTHORIZED','IN_PROGRESS')"),
    )
    op.create_index("ix_controlled_proof_exit_recoveries_proof", "controlled_proof_exit_recoveries", ["proof_id"])


def downgrade() -> None:
    op.drop_index("ix_controlled_proof_exit_recoveries_proof", table_name="controlled_proof_exit_recoveries")
    op.drop_index("uq_controlled_proof_exit_recoveries_active_proof", table_name="controlled_proof_exit_recoveries")
    op.drop_table("controlled_proof_exit_recoveries")
