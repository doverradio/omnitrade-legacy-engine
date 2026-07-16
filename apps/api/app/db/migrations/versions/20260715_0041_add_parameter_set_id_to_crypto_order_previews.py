"""add parameter set pinning to crypto order previews

Revision ID: 20260715_0041
Revises: 20260715_0040
Create Date: 2026-07-15 23:15:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0041"
down_revision: str | None = "20260715_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crypto_order_previews", sa.Column("parameter_set_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cop_parameter_set",
        "crypto_order_previews",
        "parameter_sets",
        ["parameter_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cop_parameter_set", "crypto_order_previews", type_="foreignkey")
    op.drop_column("crypto_order_previews", "parameter_set_id")
