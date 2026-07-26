"""add asset_commissioning_runs table

Revision ID: 20260725_0050
Revises: 20260725_0049
Create Date: 2026-07-25 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260725_0050"
down_revision: str | None = "20260725_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_commissioning_runs",
        sa.Column("commissioning_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("activate", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'IN_PROGRESS'")),
        sa.Column("stages", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("commissioning_id"),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','FAILED')", name="ck_asset_commissioning_runs_status"),
    )
    op.create_unique_constraint(
        "uq_asset_commissioning_runs_idempotency_key", "asset_commissioning_runs", ["idempotency_key"]
    )
    op.create_index(
        "ix_asset_commissioning_runs_product_campaign",
        "asset_commissioning_runs",
        ["product_id", "campaign_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_commissioning_runs_product_campaign", table_name="asset_commissioning_runs")
    op.drop_constraint("uq_asset_commissioning_runs_idempotency_key", "asset_commissioning_runs", type_="unique")
    op.drop_table("asset_commissioning_runs")
