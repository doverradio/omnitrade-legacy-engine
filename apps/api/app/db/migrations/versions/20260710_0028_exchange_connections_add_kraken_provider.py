"""allow kraken provider in exchange connections

Revision ID: 20260710_0028
Revises: 20260710_0027
Create Date: 2026-07-10 23:55:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260710_0028"
down_revision: str | None = "20260710_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "exchange_connections"
_CONSTRAINT = "ck_exchange_connections_provider"
_UPGRADE_SQL = "provider IN ('coinbase_advanced','kraken_spot')"
_DOWNGRADE_SQL = "provider IN ('coinbase_advanced')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _UPGRADE_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    kraken_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_connections WHERE provider = 'kraken_spot'")
    ).scalar_one()
    if int(kraken_rows or 0) > 0:
        raise RuntimeError("Cannot downgrade while exchange_connections contains kraken_spot rows.")

    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _DOWNGRADE_SQL)
