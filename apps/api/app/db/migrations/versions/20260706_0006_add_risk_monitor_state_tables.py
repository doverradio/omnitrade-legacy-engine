"""add risk monitor state tables

Revision ID: 20260706_0006
Revises: 20260706_0005
Create Date: 2026-07-06 02:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260706_0006"
down_revision: str | None = "20260706_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_kill_switches",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("paper_account_id", sa.UUID(), nullable=True),
        sa.Column("engaged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rearm_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "risk_rule_configs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("paper_account_id", sa.UUID(), nullable=True),
        sa.Column("max_position_size_pct", sa.Numeric(), nullable=False),
        sa.Column("max_daily_loss_pct", sa.Numeric(), nullable=False),
        sa.Column("max_drawdown_pct", sa.Numeric(), nullable=False),
        sa.Column("default_stop_loss_pct", sa.Numeric(), nullable=False),
        sa.Column("cooldown_after_losses", sa.Integer(), nullable=False),
        sa.Column("cooldown_duration_hours", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("risk_rule_configs")
    op.drop_table("risk_kill_switches")