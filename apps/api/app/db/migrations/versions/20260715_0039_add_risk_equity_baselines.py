"""add risk equity baselines

Revision ID: 20260715_0039
Revises: 20260715_0038
Create Date: 2026-07-15 18:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260715_0039"
down_revision: str | None = "20260715_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_equity_baselines",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("paper_account_id", sa.UUID(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("start_of_day_equity", sa.Numeric(), nullable=False),
        sa.Column("start_of_day_source", sa.Text(), nullable=False),
        sa.Column("start_of_day_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("high_water_mark_equity", sa.Numeric(), nullable=False),
        sa.Column("high_water_mark_source", sa.Text(), nullable=False),
        sa.Column("high_water_mark_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_equity", sa.Numeric(), nullable=False),
        sa.Column("last_cash_balance", sa.Numeric(), nullable=False),
        sa.Column("last_position_value", sa.Numeric(), nullable=False),
        sa.Column("last_price_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valuation_source", sa.Text(), nullable=False),
        sa.Column("valuation_state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_account_id", name="uq_risk_equity_baselines_account"),
        sa.CheckConstraint("start_of_day_equity >= 0", name="ck_risk_eq_base_sod_non_negative"),
        sa.CheckConstraint("high_water_mark_equity >= 0", name="ck_risk_eq_base_hwm_non_negative"),
        sa.CheckConstraint("last_equity >= 0", name="ck_risk_eq_base_last_equity_non_negative"),
        sa.CheckConstraint("last_cash_balance >= 0", name="ck_risk_eq_base_cash_non_negative"),
        sa.CheckConstraint("last_position_value >= 0", name="ck_risk_eq_base_pos_non_negative"),
        sa.CheckConstraint(
            "valuation_state IN ('ready','missing_price_evidence','stale_price_evidence','inconsistent_account_state')",
            name="ck_risk_eq_base_valuation_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("risk_equity_baselines")
