"""add mandate-backed canonical package authority

Revision ID: 20260722_0045
Revises: 20260721_0044
Create Date: 2026-07-22 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260722_0045"
down_revision: str | None = "20260721_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("canonical_preview_packages", sa.Column("authorization_source", sa.Text(), nullable=True))
    op.add_column("canonical_preview_packages", sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("canonical_preview_packages", sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("canonical_preview_packages", sa.Column("mandate_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("canonical_preview_packages", sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("canonical_preview_packages", sa.Column("authority_audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_cpp_mandate", "canonical_preview_packages", "autonomous_capital_mandates", ["mandate_id"], ["mandate_id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_cpp_mandate_version", "canonical_preview_packages", "autonomous_capital_mandate_versions", ["mandate_version_id"], ["mandate_version_id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_cpp_mandate_evaluation", "canonical_preview_packages", "autonomous_capital_mandate_evaluations", ["mandate_evaluation_id"], ["evaluation_id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_cpp_authorization_source", "canonical_preview_packages", "authorization_source IS NULL OR authorization_source IN ('HUMAN','MANDATE')")
    op.create_check_constraint(
        "ck_cpp_authorization_evidence",
        "canonical_preview_packages",
        "(authorization_source IS NULL) OR "
        "(authorization_source = 'HUMAN' AND approval_event_id IS NOT NULL AND mandate_id IS NULL AND mandate_version_id IS NULL AND mandate_evaluation_id IS NULL) OR "
        "(authorization_source = 'MANDATE' AND approval_event_id IS NULL AND mandate_id IS NOT NULL AND mandate_version_id IS NOT NULL AND mandate_evaluation_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cpp_authorization_evidence", "canonical_preview_packages", type_="check")
    op.drop_constraint("ck_cpp_authorization_source", "canonical_preview_packages", type_="check")
    op.drop_constraint("fk_cpp_mandate_evaluation", "canonical_preview_packages", type_="foreignkey")
    op.drop_constraint("fk_cpp_mandate_version", "canonical_preview_packages", type_="foreignkey")
    op.drop_constraint("fk_cpp_mandate", "canonical_preview_packages", type_="foreignkey")
    op.drop_column("canonical_preview_packages", "authority_audit_correlation_id")
    op.drop_column("canonical_preview_packages", "authorization_expires_at")
    op.drop_column("canonical_preview_packages", "mandate_evaluation_id")
    op.drop_column("canonical_preview_packages", "mandate_version_id")
    op.drop_column("canonical_preview_packages", "mandate_id")
    op.drop_column("canonical_preview_packages", "authorization_source")
