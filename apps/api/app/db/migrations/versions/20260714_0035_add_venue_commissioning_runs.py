"""add venue commissioning runs table

Revision ID: 20260714_0035
Revises: 20260713_0034
Create Date: 2026-07-14 05:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260714_0035"
down_revision: str | None = "20260713_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "venue_commissioning_runs",
        sa.Column("commissioning_run_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("execution_purpose", sa.Text(), nullable=False, server_default=sa.text("'VENUE_COMMISSIONING'")),
        sa.Column("commissioning_type", sa.Text(), nullable=False, server_default=sa.text("'KRAKEN_FIRST_FLIGHT'")),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'kraken_spot'")),
        sa.Column("environment", sa.Text(), nullable=False, server_default=sa.text("'production'")),
        sa.Column("product_id", sa.Text(), nullable=False, server_default=sa.text("'BTC-USD'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PREPARED'")),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_signal", sa.Text(), nullable=True),
        sa.Column("expected_profit", sa.Text(), nullable=False, server_default=sa.text("'NOT_CLAIMED'")),
        sa.Column("max_quote_notional", sa.Numeric(), nullable=False, server_default=sa.text("5.00")),
        sa.Column("max_buys", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("max_sells", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("hold_minutes", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("buy_requested_quote_usd", sa.Numeric(), nullable=False, server_default=sa.text("5.00")),
        sa.Column("buy_client_order_id", sa.Text(), nullable=True),
        sa.Column("buy_provider_order_id", sa.Text(), nullable=True),
        sa.Column("buy_idempotency_key", sa.Text(), nullable=True),
        sa.Column("buy_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("buy_filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("buy_filled_quote_usd", sa.Numeric(), nullable=True),
        sa.Column("buy_filled_base_btc", sa.Numeric(), nullable=True),
        sa.Column("buy_avg_price_usd", sa.Numeric(), nullable=True),
        sa.Column("buy_fee_usd", sa.Numeric(), nullable=True),
        sa.Column("hold_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sell_client_order_id", sa.Text(), nullable=True),
        sa.Column("sell_provider_order_id", sa.Text(), nullable=True),
        sa.Column("sell_idempotency_key", sa.Text(), nullable=True),
        sa.Column("sell_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sell_filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sell_requested_base_btc", sa.Numeric(), nullable=True),
        sa.Column("sell_filled_base_btc", sa.Numeric(), nullable=True),
        sa.Column("sell_filled_quote_usd", sa.Numeric(), nullable=True),
        sa.Column("sell_avg_price_usd", sa.Numeric(), nullable=True),
        sa.Column("sell_fee_usd", sa.Numeric(), nullable=True),
        sa.Column("gross_pnl_usd", sa.Numeric(), nullable=True),
        sa.Column("total_fees_usd", sa.Numeric(), nullable=True),
        sa.Column("net_realized_pnl_usd", sa.Numeric(), nullable=True),
        sa.Column("dust_base_btc", sa.Numeric(), nullable=True),
        sa.Column("ledger_matches_kraken", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("duplicate_orders_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("manual_intervention_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("activated_by", sa.Text(), nullable=True),
        sa.Column("started_by", sa.Text(), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("state_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("commissioning_run_id"),
        sa.CheckConstraint("execution_purpose = 'VENUE_COMMISSIONING'", name="ck_vcr_exec_purpose"),
        sa.CheckConstraint("commissioning_type = 'KRAKEN_FIRST_FLIGHT'", name="ck_vcr_comm_type"),
        sa.CheckConstraint("provider = 'kraken_spot'", name="ck_vcr_provider"),
        sa.CheckConstraint("environment = 'production'", name="ck_vcr_environment"),
        sa.CheckConstraint("product_id = 'BTC-USD'", name="ck_vcr_product"),
        sa.CheckConstraint("max_quote_notional = 5.00", name="ck_vcr_max_quote"),
        sa.CheckConstraint("max_buys = 1", name="ck_vcr_max_buys"),
        sa.CheckConstraint("max_sells = 1", name="ck_vcr_max_sells"),
        sa.CheckConstraint("strategy_id IS NULL", name="ck_vcr_no_strategy_id"),
        sa.CheckConstraint("strategy_signal IS NULL", name="ck_vcr_no_strategy_signal"),
        sa.CheckConstraint("expected_profit = 'NOT_CLAIMED'", name="ck_vcr_no_profit_claim"),
        sa.CheckConstraint("buy_requested_quote_usd > 0", name="ck_vcr_buy_quote_positive"),
        sa.CheckConstraint("buy_requested_quote_usd <= 5.00", name="ck_vcr_buy_quote_cap"),
        sa.CheckConstraint(
            "status IN ('PREPARED','ACTIVE','BUY_SUBMISSION_PENDING','BUY_RECONCILIATION_REQUIRED','BUY_FILLED','HOLDING','SELL_DUE','SELL_SUBMISSION_PENDING','SELL_RECONCILIATION_REQUIRED','SELL_FILLED','RECONCILED','COMPLETED','ABORTED','MANUAL_REVIEW_REQUIRED','REVOKED','EXPIRED')",
            name="ck_vcr_status",
        ),
    )
    op.create_index(
        "uq_vcr_active_scope",
        "venue_commissioning_runs",
        ["provider", "environment", "product_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PREPARED','ACTIVE','BUY_SUBMISSION_PENDING','BUY_RECONCILIATION_REQUIRED','BUY_FILLED','HOLDING','SELL_DUE','SELL_SUBMISSION_PENDING','SELL_RECONCILIATION_REQUIRED','SELL_FILLED','RECONCILED')"
        ),
    )
    op.create_index("ix_vcr_status_created", "venue_commissioning_runs", ["status", "created_at"], unique=False)
    op.create_index("ix_vcr_buy_client_order_id", "venue_commissioning_runs", ["buy_client_order_id"], unique=True)
    op.create_index("ix_vcr_sell_client_order_id", "venue_commissioning_runs", ["sell_client_order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_vcr_sell_client_order_id", table_name="venue_commissioning_runs")
    op.drop_index("ix_vcr_buy_client_order_id", table_name="venue_commissioning_runs")
    op.drop_index("ix_vcr_status_created", table_name="venue_commissioning_runs")
    op.drop_index("uq_vcr_active_scope", table_name="venue_commissioning_runs")
    op.drop_table("venue_commissioning_runs")
