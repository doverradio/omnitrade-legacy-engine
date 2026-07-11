"""add live reconciliation and accounting schema foundation

Revision ID: 20260710_0027
Revises: 20260710_0026
Create Date: 2026-07-10 23:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260710_0027"
down_revision: str | None = "20260710_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LRE_TABLE = "live_reconciliation_events"
_LAR_TABLE = "live_accounting_records"
_LRE_STATUS_CONSTRAINT = "ck_live_reconciliation_events_reconciliation_status"
_LRE_STATUS_SQL = (
    "reconciliation_status IN ('open','partially_filled','filled','canceled','rejected',"
    "'reconciliation_required','unknown','conflict','balance_mismatch')"
)
_LRE_STATUS_SQL_DOWNGRADE = (
    "reconciliation_status IN ('open','partially_filled','filled','canceled','rejected')"
)


def upgrade() -> None:
    op.add_column(_LRE_TABLE, sa.Column("live_crypto_order_id", sa.UUID(), nullable=True))
    op.add_column(_LRE_TABLE, sa.Column("capital_campaign_id", sa.Integer(), nullable=True))
    op.add_column(_LRE_TABLE, sa.Column("provider_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(_LRE_TABLE, "provider_order_id", existing_type=sa.Text(), nullable=True)
    op.create_foreign_key("fk_lre_live_order", _LRE_TABLE, "live_crypto_orders", ["live_crypto_order_id"], ["live_crypto_order_id"], ondelete="SET NULL")
    op.create_foreign_key("fk_lre_campaign", _LRE_TABLE, "capital_campaigns", ["capital_campaign_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_lre_live_order", _LRE_TABLE, ["live_crypto_order_id"], unique=False)
    op.create_index("ix_lre_campaign", _LRE_TABLE, ["capital_campaign_id"], unique=False)
    op.drop_constraint(_LRE_STATUS_CONSTRAINT, _LRE_TABLE, type_="check")
    op.create_check_constraint(_LRE_STATUS_CONSTRAINT, _LRE_TABLE, _LRE_STATUS_SQL)

    op.add_column(_LAR_TABLE, sa.Column("live_crypto_order_id", sa.UUID(), nullable=True))
    op.add_column(_LAR_TABLE, sa.Column("capital_campaign_id", sa.Integer(), nullable=True))
    op.add_column(_LAR_TABLE, sa.Column("provider_fill_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_lar_live_order", _LAR_TABLE, "live_crypto_orders", ["live_crypto_order_id"], ["live_crypto_order_id"], ondelete="SET NULL")
    op.create_foreign_key("fk_lar_campaign", _LAR_TABLE, "capital_campaigns", ["capital_campaign_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_lar_live_order", _LAR_TABLE, ["live_crypto_order_id"], unique=False)
    op.create_index("ix_lar_campaign", _LAR_TABLE, ["capital_campaign_id"], unique=False)
    op.create_unique_constraint("uq_lar_provider_fill_record", _LAR_TABLE, ["provider_order_id", "provider_fill_id", "record_type"])


def downgrade() -> None:
    bind = op.get_bind()
    null_provider_order_count = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {_LRE_TABLE} WHERE provider_order_id IS NULL")
    ).scalar_one()
    if int(null_provider_order_count or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade live reconciliation schema while live_reconciliation_events.provider_order_id contains NULL rows."
        )

    op.drop_constraint("uq_lar_provider_fill_record", _LAR_TABLE, type_="unique")
    op.drop_index("ix_lar_campaign", table_name=_LAR_TABLE)
    op.drop_index("ix_lar_live_order", table_name=_LAR_TABLE)
    op.drop_constraint("fk_lar_campaign", _LAR_TABLE, type_="foreignkey")
    op.drop_constraint("fk_lar_live_order", _LAR_TABLE, type_="foreignkey")
    op.drop_column(_LAR_TABLE, "provider_fill_timestamp")
    op.drop_column(_LAR_TABLE, "capital_campaign_id")
    op.drop_column(_LAR_TABLE, "live_crypto_order_id")

    op.drop_constraint(_LRE_STATUS_CONSTRAINT, _LRE_TABLE, type_="check")
    op.create_check_constraint(_LRE_STATUS_CONSTRAINT, _LRE_TABLE, _LRE_STATUS_SQL_DOWNGRADE)
    op.drop_index("ix_lre_campaign", table_name=_LRE_TABLE)
    op.drop_index("ix_lre_live_order", table_name=_LRE_TABLE)
    op.drop_constraint("fk_lre_campaign", _LRE_TABLE, type_="foreignkey")
    op.drop_constraint("fk_lre_live_order", _LRE_TABLE, type_="foreignkey")
    op.alter_column(_LRE_TABLE, "provider_order_id", existing_type=sa.Text(), nullable=False)
    op.drop_column(_LRE_TABLE, "provider_recorded_at")
    op.drop_column(_LRE_TABLE, "capital_campaign_id")
    op.drop_column(_LRE_TABLE, "live_crypto_order_id")