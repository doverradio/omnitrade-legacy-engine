"""add decision counterfactual results

Revision ID: 20260706_0010
Revises: 20260706_0009
Create Date: 2026-07-06 07:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0010"
down_revision: str | None = "20260706_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_counterfactual_results",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("horizon_label", sa.Text(), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("decision_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_symbol", sa.Text(), nullable=False),
        sa.Column("actual_action", sa.Text(), nullable=False),
        sa.Column("shadow_buy_return_pct", sa.Numeric(), nullable=True),
        sa.Column("shadow_sell_return_pct", sa.Numeric(), nullable=True),
        sa.Column("shadow_wait_return_pct", sa.Numeric(), nullable=True),
        sa.Column("best_action", sa.Text(), nullable=True),
        sa.Column("actual_action_correct", sa.Boolean(), nullable=True),
        sa.Column("evaluation_state", sa.Text(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("lesson_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "horizon_label IN ('15m','1h','24h')",
            name="ck_decision_counterfactual_results_horizon_label",
        ),
        sa.CheckConstraint(
            "horizon_minutes IN (15,60,1440)",
            name="ck_decision_counterfactual_results_horizon_minutes",
        ),
        sa.CheckConstraint(
            "actual_action IN ('buy','sell','wait')",
            name="ck_decision_counterfactual_results_actual_action",
        ),
        sa.CheckConstraint(
            "best_action IS NULL OR best_action IN ('buy','sell','wait')",
            name="ck_decision_counterfactual_results_best_action",
        ),
        sa.CheckConstraint(
            "evaluation_state IN ('resolved','unknown','unavailable')",
            name="ck_decision_counterfactual_results_evaluation_state",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.decision_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_counterfactual_results_idempotency_key"),
        sa.UniqueConstraint(
            "decision_id",
            "horizon_minutes",
            name="uq_decision_counterfactual_results_decision_horizon",
        ),
    )
    op.create_index(
        "idx_decision_counterfactual_results_decision_horizon",
        "decision_counterfactual_results",
        ["decision_id", "horizon_minutes"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_decision_counterfactual_results_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_counterfactual_results is append-only and does not support update/delete';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_decision_counterfactual_results_mutation
        BEFORE UPDATE OR DELETE ON decision_counterfactual_results
        FOR EACH ROW EXECUTE FUNCTION prevent_decision_counterfactual_results_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_decision_counterfactual_results_mutation ON decision_counterfactual_results"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_decision_counterfactual_results_mutation")

    op.drop_index(
        "idx_decision_counterfactual_results_decision_horizon",
        table_name="decision_counterfactual_results",
    )
    op.drop_table("decision_counterfactual_results")
