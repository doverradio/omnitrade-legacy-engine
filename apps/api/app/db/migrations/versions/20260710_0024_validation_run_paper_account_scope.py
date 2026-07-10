"""add validation run paper-account scope mapping

Revision ID: 20260710_0024
Revises: 20260710_0023
Create Date: 2026-07-10 13:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260710_0024"
down_revision: str | None = "20260710_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validation_run_paper_accounts",
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.validation_run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("validation_run_id", "paper_account_id", name="pk_validation_run_paper_accounts"),
    )
    op.create_index(
        "ix_validation_run_paper_accounts_validation_run_id",
        "validation_run_paper_accounts",
        ["validation_run_id"],
    )
    op.create_index(
        "ix_validation_run_paper_accounts_paper_account_id",
        "validation_run_paper_accounts",
        ["paper_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_validation_run_paper_accounts_paper_account_id", table_name="validation_run_paper_accounts")
    op.drop_index("ix_validation_run_paper_accounts_validation_run_id", table_name="validation_run_paper_accounts")
    op.drop_table("validation_run_paper_accounts")
