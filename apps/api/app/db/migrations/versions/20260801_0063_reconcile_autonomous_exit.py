"""persist autonomous exit reconciliation and realized result

Revision ID: 20260801_0063
Revises: 20260801_0062
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0063"
down_revision: str | None = "20260801_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("exit_reconciliation_event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("exit_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("realized_gross_sell_proceeds", sa.Numeric()),
        sa.Column("realized_sell_fees", sa.Numeric()),
        sa.Column("realized_net_sell_proceeds", sa.Numeric()),
        sa.Column("allocated_buy_cost_basis", sa.Numeric()),
        sa.Column("allocated_buy_fees", sa.Numeric()),
        sa.Column("realized_net_profit", sa.Numeric()),
        sa.Column("realized_return", sa.Numeric()),
        sa.Column("realized_sold_quantity", sa.Numeric()),
        sa.Column("residual_dust_quantity", sa.Numeric()),
        sa.Column("autonomous_proof_sell_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    for column in columns:
        op.add_column("autonomous_position_custodies", column)
    op.create_foreign_key(
        "fk_apc_exit_reconciliation", "autonomous_position_custodies", "live_reconciliation_events",
        ["exit_reconciliation_event_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_apc_exit_reconciliation", "autonomous_position_custodies", ["exit_reconciliation_event_id"])
    op.create_check_constraint(
        "ck_apc_realized_exit_economics", "autonomous_position_custodies",
        "realized_sold_quantity IS NULL OR (realized_sold_quantity >= 0 AND residual_dust_quantity >= 0 "
        "AND realized_gross_sell_proceeds >= 0 AND realized_sell_fees >= 0 "
        "AND realized_net_sell_proceeds = realized_gross_sell_proceeds - realized_sell_fees "
        "AND allocated_buy_cost_basis >= 0 AND allocated_buy_fees >= 0)",
    )
    op.create_check_constraint(
        "ck_apc_proof_sell_verified", "autonomous_position_custodies",
        "autonomous_proof_sell_verified = false OR (custody_state = 'CLOSED' AND proof_eligible = true "
        "AND exit_reconciliation_event_id IS NOT NULL AND exit_reconciled_at IS NOT NULL "
        "AND realized_net_profit > 0 AND residual_dust_quantity = 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_apc_proof_sell_verified", "autonomous_position_custodies", type_="check")
    op.drop_constraint("ck_apc_realized_exit_economics", "autonomous_position_custodies", type_="check")
    op.drop_constraint("uq_apc_exit_reconciliation", "autonomous_position_custodies", type_="unique")
    op.drop_constraint("fk_apc_exit_reconciliation", "autonomous_position_custodies", type_="foreignkey")
    for name in (
        "autonomous_proof_sell_verified", "residual_dust_quantity", "realized_sold_quantity",
        "realized_return", "realized_net_profit", "allocated_buy_fees", "allocated_buy_cost_basis",
        "realized_net_sell_proceeds", "realized_sell_fees", "realized_gross_sell_proceeds",
        "exit_reconciled_at", "exit_reconciliation_event_id",
    ):
        op.drop_column("autonomous_position_custodies", name)
