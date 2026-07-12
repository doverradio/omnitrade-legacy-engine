"""add mandate lifecycle and evidence persistence

Revision ID: 20260712_0030
Revises: 20260712_0029
Create Date: 2026-07-12 12:20:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0030"
down_revision: str | None = "20260712_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomous_capital_mandate_authorizations",
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_table(
        "autonomous_capital_mandate_evaluations",
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_number", sa.Integer(), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("autonomy_level", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=False),
        sa.Column("authorization_result", sa.Text(), nullable=False),
        sa.Column("approval_result", sa.Text(), nullable=False),
        sa.Column("risk_verdict", sa.Text(), nullable=False),
        sa.Column("risk_evaluated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("checks_passed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checks_failed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deterministic_explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("human_approval_required", sa.Boolean(), nullable=False),
        sa.Column("active_mandate_exemption_eligible", sa.Boolean(), nullable=False),
        sa.Column("request_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("software_build_version", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("proposed_action IN ('BUY','SELL','HOLD')", name="ck_ac_mandate_evaluations_action"),
        sa.CheckConstraint("authorization_result IN ('AUTHORIZED','REJECTED')", name="ck_ac_mandate_evaluations_authorization_result"),
        sa.CheckConstraint(
            "approval_result IN ('APPROVAL_REQUIRED_HUMAN','APPROVAL_SATISFIED_BY_ACTIVE_MANDATE')",
            name="ck_ac_mandate_evaluations_approval_result",
        ),
        sa.CheckConstraint(
            "risk_verdict IN ('ACCEPTED','REJECTED','RESIZED','NOT_EVALUATED')",
            name="ck_ac_mandate_evaluations_risk_verdict",
        ),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mandate_version_id"], ["autonomous_capital_mandate_versions.mandate_version_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("evaluation_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ac_mandate_evaluations_idempotency"),
    )
    op.create_index("ix_ac_mandate_evaluations_mandate", "autonomous_capital_mandate_evaluations", ["mandate_id"])
    op.create_index("ix_ac_mandate_evaluations_decision", "autonomous_capital_mandate_evaluations", ["decision_id"])
    op.create_index("ix_ac_mandate_evaluations_created_at", "autonomous_capital_mandate_evaluations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ac_mandate_evaluations_created_at", table_name="autonomous_capital_mandate_evaluations")
    op.drop_index("ix_ac_mandate_evaluations_decision", table_name="autonomous_capital_mandate_evaluations")
    op.drop_index("ix_ac_mandate_evaluations_mandate", table_name="autonomous_capital_mandate_evaluations")
    op.drop_table("autonomous_capital_mandate_evaluations")
    op.drop_column("autonomous_capital_mandate_authorizations", "audit_correlation_id")
