"""add decision experiment recommendations

Revision ID: 20260706_0013
Revises: 20260706_0012
Create Date: 2026-07-06 10:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0013"
down_revision: str | None = "20260706_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_experiment_recommendations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("recommendation_engine_version", sa.Text(), nullable=False),
        sa.Column("recommendation_type", sa.Text(), nullable=False),
        sa.Column("recommendation_category", sa.Text(), nullable=False),
        sa.Column("confidence_level", sa.Text(), nullable=False),
        sa.Column("expected_impact_level", sa.Text(), nullable=False),
        sa.Column("required_human_review_level", sa.Text(), nullable=False),
        sa.Column("supporting_evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("originating_decision_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggested_experiment", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_state", sa.Text(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("advisory_only", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "recommendation_type IN ('strategy_parameter_investigation','hypothesis_test','experiment_run','risk_observation','recurring_decision_pattern')",
            name="ck_decision_experiment_recommendations_type",
        ),
        sa.CheckConstraint(
            "recommendation_category IN ('strategy','hypothesis','experiment','risk','pattern')",
            name="ck_decision_experiment_recommendations_category",
        ),
        sa.CheckConstraint(
            "confidence_level IN ('low','medium','high')",
            name="ck_decision_experiment_recommendations_confidence",
        ),
        sa.CheckConstraint(
            "expected_impact_level IN ('low','medium','high')",
            name="ck_decision_experiment_recommendations_impact",
        ),
        sa.CheckConstraint(
            "required_human_review_level IN ('standard','priority','required')",
            name="ck_decision_experiment_recommendations_review",
        ),
        sa.CheckConstraint(
            "evidence_state IN ('known','unknown','unavailable')",
            name="ck_decision_experiment_recommendations_evidence_state",
        ),
        sa.CheckConstraint(
            "advisory_only = true",
            name="ck_decision_experiment_recommendations_advisory_only",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_decision_experiment_recommendations_idempotency_key",
        ),
    )
    op.create_index(
        "idx_decision_experiment_recommendations_created",
        "decision_experiment_recommendations",
        ["created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_experiment_recommendations_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_experiment_recommendations is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_experiment_recommendations_mutation
        BEFORE UPDATE OR DELETE ON decision_experiment_recommendations
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_experiment_recommendations_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_decision_experiment_recommendations_mutation ON decision_experiment_recommendations"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_experiment_recommendations_mutation")

    op.drop_index(
        "idx_decision_experiment_recommendations_created",
        table_name="decision_experiment_recommendations",
    )
    op.drop_table("decision_experiment_recommendations")
