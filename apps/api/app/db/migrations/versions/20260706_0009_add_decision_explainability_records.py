"""add decision explainability records

Revision ID: 20260706_0009
Revises: 20260706_0008
Create Date: 2026-07-06 06:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0009"
down_revision: str | None = "20260706_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_explainability_records",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("evidence_role", sa.Text(), nullable=False),
        sa.Column("evidence_name", sa.Text(), nullable=False),
        sa.Column("evidence_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("availability_state", sa.Text(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "evidence_role IN ('supporting','opposing','confidence_factor','risk_adjustment')",
            name="ck_decision_explainability_records_role",
        ),
        sa.CheckConstraint(
            "availability_state IN ('known','unknown','unavailable')",
            name="ck_decision_explainability_records_availability_state",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_explainability_records_idempotency_key"),
    )
    op.create_index(
        "idx_decision_explainability_records_decision_created",
        "decision_explainability_records",
        ["decision_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_explainability_records_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_explainability_records is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_explainability_records_mutation
        BEFORE UPDATE OR DELETE ON decision_explainability_records
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_explainability_records_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_decision_explainability_records_mutation ON decision_explainability_records"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_explainability_records_mutation")

    op.drop_index(
        "idx_decision_explainability_records_decision_created",
        table_name="decision_explainability_records",
    )
    op.drop_table("decision_explainability_records")
