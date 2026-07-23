"""add mandate-backed canonical proving activation authority

Revision ID: 20260722_0046
Revises: 20260722_0045
Create Date: 2026-07-22 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0046"
down_revision: str | None = "20260722_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("canonical_proving_activations", sa.Column("authority_source", sa.Text(), nullable=False, server_default="HUMAN"))
    op.add_column("canonical_proving_activations", sa.Column("mandate_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("canonical_proving_activations", sa.Column("authority_audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.alter_column("canonical_proving_activations", "approval_event_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_foreign_key(
        "fk_cpa_mandate_evaluation", "canonical_proving_activations", "autonomous_capital_mandate_evaluations",
        ["mandate_evaluation_id"], ["evaluation_id"], ondelete="RESTRICT",
    )
    op.create_check_constraint("ck_cpa_authority_source", "canonical_proving_activations", "authority_source IN ('HUMAN','MANDATE')")
    op.create_check_constraint(
        "ck_cpa_authority_evidence",
        "canonical_proving_activations",
        "(authority_source = 'HUMAN' AND approval_event_id IS NOT NULL AND mandate_evaluation_id IS NULL) OR "
        "(authority_source = 'MANDATE' AND approval_event_id IS NULL AND mandate_evaluation_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cpa_authority_evidence", "canonical_proving_activations", type_="check")
    op.drop_constraint("ck_cpa_authority_source", "canonical_proving_activations", type_="check")
    op.drop_constraint("fk_cpa_mandate_evaluation", "canonical_proving_activations", type_="foreignkey")
    op.alter_column("canonical_proving_activations", "approval_event_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_column("canonical_proving_activations", "authority_audit_correlation_id")
    op.drop_column("canonical_proving_activations", "mandate_evaluation_id")
    op.drop_column("canonical_proving_activations", "authority_source")
