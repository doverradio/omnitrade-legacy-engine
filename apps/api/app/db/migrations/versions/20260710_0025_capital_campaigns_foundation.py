"""add capital campaigns foundation

Revision ID: 20260710_0025
Revises: 20260710_0024
Create Date: 2026-07-10 14:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260710_0025"
down_revision: str | None = "20260710_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capital_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("campaign_type", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("starting_capital", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("current_equity", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("realized_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("unrealized_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("fees", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("roi", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT','READY','RUNNING','PAUSED','TARGET_REACHED','COMPLETED','ARCHIVED')",
            name="ck_capital_campaigns_status",
        ),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.validation_run_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_capital_campaigns_uuid", "capital_campaigns", ["uuid"], unique=True)
    op.create_index("ix_capital_campaigns_status", "capital_campaigns", ["status"])
    op.create_index("ix_capital_campaigns_owner", "capital_campaigns", ["owner"])
    op.create_index("ix_capital_campaigns_validation_run_id", "capital_campaigns", ["validation_run_id"])
    op.create_index("ix_capital_campaigns_paper_account_id", "capital_campaigns", ["paper_account_id"])
    op.create_index("ix_capital_campaigns_strategy_id", "capital_campaigns", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_capital_campaigns_strategy_id", table_name="capital_campaigns")
    op.drop_index("ix_capital_campaigns_paper_account_id", table_name="capital_campaigns")
    op.drop_index("ix_capital_campaigns_validation_run_id", table_name="capital_campaigns")
    op.drop_index("ix_capital_campaigns_owner", table_name="capital_campaigns")
    op.drop_index("ix_capital_campaigns_status", table_name="capital_campaigns")
    op.drop_index("ix_capital_campaigns_uuid", table_name="capital_campaigns")
    op.drop_table("capital_campaigns")
