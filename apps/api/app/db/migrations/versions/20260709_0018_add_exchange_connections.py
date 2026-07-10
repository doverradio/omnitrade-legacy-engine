"""add exchange connections table

Revision ID: 20260709_0018
Revises: 20260709_0017
Create Date: 2026-07-09 18:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260709_0018"
down_revision: str | None = "20260709_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_connections",
        sa.Column("exchange_connection_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("connection_name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'disconnected'"), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("api_key_masked", sa.Text(), nullable=False),
        sa.Column("api_secret_masked", sa.Text(), nullable=False),
        sa.Column("passphrase_configured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("credentials_valid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("api_permissions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("account_status", sa.Text(), nullable=True),
        sa.Column("balances", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("total_equity_usd", sa.Text(), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_api_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider IN ('coinbase_advanced')", name="ck_exchange_connections_provider"),
        sa.CheckConstraint("environment IN ('sandbox', 'production')", name="ck_exchange_connections_environment"),
        sa.CheckConstraint("status IN ('connected', 'disconnected', 'error')", name="ck_exchange_connections_status"),
        sa.PrimaryKeyConstraint("exchange_connection_id"),
    )

    op.create_index(
        "ix_exchange_connections_provider_env",
        "exchange_connections",
        ["provider", "environment"],
    )


def downgrade() -> None:
    op.drop_index("ix_exchange_connections_provider_env", table_name="exchange_connections")
    op.drop_table("exchange_connections")
