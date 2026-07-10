"""add crypto order previews table

Revision ID: 20260709_0020
Revises: 20260709_0019
Create Date: 2026-07-09 22:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260709_0020"
down_revision: str | None = "20260709_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crypto_order_previews",
        sa.Column("crypto_order_preview_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("preview_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("refreshed_from_preview_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("quote_size", sa.Numeric(), nullable=True),
        sa.Column("base_size", sa.Numeric(), nullable=True),
        sa.Column("requested_amount", sa.Numeric(), nullable=False),
        sa.Column("requested_amount_currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("readiness_verdict", sa.Text(), nullable=True),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_name", sa.Text(), nullable=True),
        sa.Column("preview_id", sa.Text(), nullable=True),
        sa.Column("estimated_average_price", sa.Numeric(), nullable=True),
        sa.Column("estimated_total_value", sa.Numeric(), nullable=True),
        sa.Column("estimated_base_size", sa.Numeric(), nullable=True),
        sa.Column("estimated_quote_size", sa.Numeric(), nullable=True),
        sa.Column("estimated_fee", sa.Numeric(), nullable=True),
        sa.Column("estimated_fee_currency", sa.Text(), nullable=True),
        sa.Column("estimated_slippage", sa.Numeric(), nullable=True),
        sa.Column("estimated_commission_total", sa.Numeric(), nullable=True),
        sa.Column("best_bid", sa.Numeric(), nullable=True),
        sa.Column("best_ask", sa.Numeric(), nullable=True),
        sa.Column("available_balance_before", sa.Numeric(), nullable=True),
        sa.Column("estimated_balance_after", sa.Numeric(), nullable=True),
        sa.Column("risk_verdict", sa.Text(), nullable=True),
        sa.Column("risk_explanation", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("warning_messages", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("exchange_response_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by", sa.Text(), nullable=False),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("crypto_order_preview_id"),
        sa.UniqueConstraint("idempotency_key", "preview_version", name="uq_crypto_order_previews_idempotency_version"),
    )
    op.create_index("idx_crypto_order_previews_exchange_created", "crypto_order_previews", ["exchange_connection_id", "created_at"], unique=False)
    op.create_index("idx_crypto_order_previews_status", "crypto_order_previews", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_crypto_order_previews_status", table_name="crypto_order_previews")
    op.drop_index("idx_crypto_order_previews_exchange_created", table_name="crypto_order_previews")
    op.drop_table("crypto_order_previews")
