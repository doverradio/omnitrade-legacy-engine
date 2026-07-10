"""add exchange connection readiness fields

Revision ID: 20260709_0019
Revises: 20260709_0018
Create Date: 2026-07-09 21:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260709_0019"
down_revision: str | None = "20260709_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("exchange_connections", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("exchange_connections", sa.Column("last_readiness_verdict", sa.Text(), nullable=True))
    op.add_column(
        "exchange_connections",
        sa.Column(
            "last_readiness_report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("exchange_connections", "last_readiness_report")
    op.drop_column("exchange_connections", "last_readiness_verdict")
    op.drop_column("exchange_connections", "last_verified_at")
