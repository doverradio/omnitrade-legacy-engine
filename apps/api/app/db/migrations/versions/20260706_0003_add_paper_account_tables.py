"""add paper account and trades tables

Revision ID: 20260706_0003
Revises: 20260705_0002
Create Date: 2026-07-06 00:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260706_0003"
down_revision: str | None = "20260705_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("starting_balance", sa.Numeric(), nullable=False),
        sa.Column("current_cash_balance", sa.Numeric(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("asset_class IN ('crypto', 'stock')", name="ck_paper_accounts_asset_class"),
        sa.CheckConstraint("starting_balance >= 25", name="ck_paper_accounts_starting_balance_min"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "trades",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("paper_account_id", sa.UUID(), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=True),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("fee", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_paper", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("execution_venue", sa.Text(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_trades_side"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_trades_account_time", "trades", ["paper_account_id", "executed_at"])


def downgrade() -> None:
    op.drop_index("idx_trades_account_time", table_name="trades")
    op.drop_table("trades")
    op.drop_table("paper_accounts")
