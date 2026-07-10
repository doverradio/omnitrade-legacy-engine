"""add live crypto orders table

Revision ID: 20260709_0021
Revises: 20260709_0020
Create Date: 2026-07-09 22:50:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260709_0021"
down_revision: str | None = "20260709_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_crypto_orders",
        sa.Column("live_crypto_order_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("crypto_order_preview_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("requested_quote_size", sa.Numeric(), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_order_id", sa.Text(), nullable=True),
        sa.Column("provider_status", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("safe_provider_response", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_confirmation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("live_crypto_order_id"),
        sa.UniqueConstraint("crypto_order_preview_id", name="uq_live_crypto_orders_preview_id"),
        sa.UniqueConstraint("client_order_id", name="uq_live_crypto_orders_client_order_id"),
        sa.UniqueConstraint("provider_order_id", name="uq_live_crypto_orders_provider_order_id"),
    )
    op.create_index("idx_live_crypto_orders_exchange_created", "live_crypto_orders", ["exchange_connection_id", "created_at"], unique=False)
    op.create_index("idx_live_crypto_orders_status", "live_crypto_orders", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_live_crypto_orders_status", table_name="live_crypto_orders")
    op.drop_index("idx_live_crypto_orders_exchange_created", table_name="live_crypto_orders")
    op.drop_table("live_crypto_orders")
