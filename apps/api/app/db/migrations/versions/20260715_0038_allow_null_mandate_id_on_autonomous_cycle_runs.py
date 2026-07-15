"""allow null mandate id on autonomous cycle runs

Revision ID: 20260715_0038
Revises: 20260715_0037
Create Date: 2026-07-15 00:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0038"
down_revision: str | None = "20260715_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("autonomous_cycle_runs", "mandate_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("autonomous_cycle_runs", "mandate_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)