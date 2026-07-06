"""add decision quality scores

Revision ID: 20260706_0011
Revises: 20260706_0010
Create Date: 2026-07-06 08:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0011"
down_revision: str | None = "20260706_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_quality_scores",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("scoring_model_version", sa.Text(), nullable=False),
        sa.Column("composite_score", sa.Numeric(), nullable=False),
        sa.Column("component_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weight_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_quality_scores_idempotency_key"),
    )
    op.create_index(
        "idx_decision_quality_scores_decision_created",
        "decision_quality_scores",
        ["decision_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_quality_scores_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_quality_scores is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_quality_scores_mutation
        BEFORE UPDATE OR DELETE ON decision_quality_scores
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_quality_scores_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_decision_quality_scores_mutation ON decision_quality_scores")
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_quality_scores_mutation")

    op.drop_index(
        "idx_decision_quality_scores_decision_created",
        table_name="decision_quality_scores",
    )
    op.drop_table("decision_quality_scores")
