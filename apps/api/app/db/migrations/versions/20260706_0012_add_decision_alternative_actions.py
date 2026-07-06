"""add decision alternative actions

Revision ID: 20260706_0012
Revises: 20260706_0011
Create Date: 2026-07-06 09:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0012"
down_revision: str | None = "20260706_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_alternative_actions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("chosen_action", sa.Text(), nullable=False),
        sa.Column("alternative_action", sa.Text(), nullable=False),
        sa.Column("reference_horizon_minutes", sa.Integer(), nullable=True),
        sa.Column("comparison_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("availability_state", sa.Text(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "chosen_action IN ('buy','sell','wait')",
            name="ck_decision_alternative_actions_chosen_action",
        ),
        sa.CheckConstraint(
            "alternative_action IN ('buy','sell','wait')",
            name="ck_decision_alternative_actions_alternative_action",
        ),
        sa.CheckConstraint(
            "chosen_action <> alternative_action",
            name="ck_decision_alternative_actions_distinct_actions",
        ),
        sa.CheckConstraint(
            "availability_state IN ('known','unknown','unavailable')",
            name="ck_decision_alternative_actions_availability_state",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_alternative_actions_idempotency_key"),
    )
    op.create_index(
        "idx_decision_alternative_actions_decision_created",
        "decision_alternative_actions",
        ["decision_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_alternative_actions_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_alternative_actions is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_alternative_actions_mutation
        BEFORE UPDATE OR DELETE ON decision_alternative_actions
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_alternative_actions_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_decision_alternative_actions_mutation ON decision_alternative_actions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_alternative_actions_mutation")

    op.drop_index(
        "idx_decision_alternative_actions_decision_created",
        table_name="decision_alternative_actions",
    )
    op.drop_table("decision_alternative_actions")
