"""add signals and model outputs tables

Revision ID: 20260706_0004a
Revises: 20260706_0004
Create Date: 2026-07-06 00:30:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0004a"
down_revision: str | None = "20260706_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("parameter_set_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("raw_strength", sa.Numeric(), nullable=True),
        sa.Column("ai_confidence", sa.Numeric(), nullable=True),
        sa.Column("regime_tag", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action IN ('buy','sell','hold')", name="ck_signals_action"),
        sa.CheckConstraint(
            "status IN ('generated','risk_approved','risk_rejected','executed','expired')",
            name="ck_signals_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "model_outputs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("related_signal_id", sa.UUID(), nullable=True),
        sa.Column("related_trade_id", sa.UUID(), nullable=True),
        sa.Column("input_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("model_outputs")
    op.drop_table("signals")
