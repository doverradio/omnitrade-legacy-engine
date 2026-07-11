"""add capital campaign profit policies and cycles

Revision ID: 20260710_0026
Revises: 20260710_0025
Create Date: 2026-07-10 18:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260710_0026"
down_revision: str | None = "20260710_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capital_campaign_profit_policies",
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("policy_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("capital_campaign_id", sa.Integer(), nullable=False),
        sa.Column("policy_type", sa.Text(), nullable=False),
        sa.Column("profit_target_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("profit_target_percent", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("compound_percent", sa.Numeric(precision=10, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("withdraw_percent", sa.Numeric(precision=10, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("protected_principal_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("minimum_realized_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("maximum_campaign_capital", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("minimum_cash_reserve", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_reserve_percent", sa.Numeric(precision=10, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("tax_reserve_percent", sa.Numeric(precision=10, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("cooldown_hours", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("require_operator_approval", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "policy_type IN ('HOLD_PROFIT','FULL_COMPOUND','PARTIAL_COMPOUND','WITHDRAW_PROFIT','WITHDRAW_AND_COMPOUND','PROTECTED_PRINCIPAL','MANUAL_REVIEW')",
            name="ck_ccpp_policy_type",
        ),
        sa.CheckConstraint("compound_percent >= 0 AND compound_percent <= 100", name="ck_ccpp_compound_pct"),
        sa.CheckConstraint("withdraw_percent >= 0 AND withdraw_percent <= 100", name="ck_ccpp_withdraw_pct"),
        sa.CheckConstraint("compound_percent + withdraw_percent <= 100", name="ck_ccpp_pct_total"),
        sa.CheckConstraint("profit_target_amount IS NULL OR profit_target_amount > 0", name="ck_ccpp_target_amount"),
        sa.CheckConstraint("profit_target_percent IS NULL OR profit_target_percent > 0", name="ck_ccpp_target_percent"),
        sa.CheckConstraint("minimum_realized_profit >= 0", name="ck_ccpp_min_profit"),
        sa.CheckConstraint("minimum_cash_reserve >= 0", name="ck_ccpp_cash_reserve"),
        sa.CheckConstraint("fee_reserve_percent >= 0", name="ck_ccpp_fee_reserve"),
        sa.CheckConstraint("tax_reserve_percent >= 0", name="ck_ccpp_tax_reserve"),
        sa.CheckConstraint(
            "maximum_campaign_capital IS NULL OR protected_principal_amount IS NULL OR maximum_campaign_capital > protected_principal_amount",
            name="ck_ccpp_max_capital",
        ),
        sa.ForeignKeyConstraint(["capital_campaign_id"], ["capital_campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("policy_id"),
    )
    op.create_index("ix_ccpp_uuid", "capital_campaign_profit_policies", ["policy_uuid"], unique=True)
    op.create_index("ix_ccpp_campaign", "capital_campaign_profit_policies", ["capital_campaign_id"])
    op.create_index("ix_ccpp_active", "capital_campaign_profit_policies", ["is_active"])

    op.create_table(
        "capital_campaign_profit_cycles",
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("cycle_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("capital_campaign_id", sa.Integer(), nullable=False),
        sa.Column("profit_policy_id", sa.Integer(), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("opening_capital", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("opening_equity", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("realized_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("unrealized_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("fees", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("eligible_profit", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("compound_amount", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("withdrawal_amount", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("reserve_amount", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("closing_campaign_capital", sa.Numeric(precision=20, scale=8), server_default=sa.text("0"), nullable=False),
        sa.Column("target_reached", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("settlement_state", sa.Text(), server_default=sa.text("'SETTLEMENT_UNKNOWN'"), nullable=False),
        sa.Column("calculation_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("calculation_fingerprint", sa.Text(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('CALCULATING','BELOW_TARGET','TARGET_REACHED','REVIEW_REQUIRED','APPROVED','COMPOUNDING_RECOMMENDED','WITHDRAWAL_RECOMMENDED','COMPLETED','CANCELLED','ERROR')",
            name="ck_ccpc_status",
        ),
        sa.CheckConstraint("settlement_state IN ('SETTLED','SETTLEMENT_UNKNOWN')", name="ck_ccpc_settlement"),
        sa.ForeignKeyConstraint(["capital_campaign_id"], ["capital_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profit_policy_id"], ["capital_campaign_profit_policies.policy_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("cycle_id"),
        sa.UniqueConstraint("capital_campaign_id", "cycle_number", name="uq_ccpc_campaign_cycle"),
    )
    op.create_index("ix_ccpc_uuid", "capital_campaign_profit_cycles", ["cycle_uuid"], unique=True)
    op.create_index("ix_ccpc_campaign", "capital_campaign_profit_cycles", ["capital_campaign_id"])
    op.create_index("ix_ccpc_policy", "capital_campaign_profit_cycles", ["profit_policy_id"])
    op.create_index("ix_ccpc_status", "capital_campaign_profit_cycles", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ccpc_status", table_name="capital_campaign_profit_cycles")
    op.drop_index("ix_ccpc_policy", table_name="capital_campaign_profit_cycles")
    op.drop_index("ix_ccpc_campaign", table_name="capital_campaign_profit_cycles")
    op.drop_index("ix_ccpc_uuid", table_name="capital_campaign_profit_cycles")
    op.drop_table("capital_campaign_profit_cycles")

    op.drop_index("ix_ccpp_active", table_name="capital_campaign_profit_policies")
    op.drop_index("ix_ccpp_campaign", table_name="capital_campaign_profit_policies")
    op.drop_index("ix_ccpp_uuid", table_name="capital_campaign_profit_policies")
    op.drop_table("capital_campaign_profit_policies")
