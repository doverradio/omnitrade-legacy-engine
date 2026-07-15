"""add capital campaign definition foundation

Revision ID: 20260714_0036
Revises: 20260714_0035
Create Date: 2026-07-14 21:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260714_0036"
down_revision: str | None = "20260714_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capital_campaign_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_identity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("capital_budget", sa.Numeric(20, 8), nullable=False),
        sa.Column("remaining_unallocated_capital", sa.Numeric(20, 8), nullable=False),
        sa.Column("base_currency", sa.Text(), nullable=False),
        sa.Column("allowed_asset_classes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_venues", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_instruments", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("campaign_modes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("maximum_open_positions", sa.Integer(), nullable=False),
        sa.Column("maximum_position_size", sa.Numeric(20, 8), nullable=False),
        sa.Column("minimum_position_size", sa.Numeric(20, 8), nullable=False),
        sa.Column("maximum_total_exposure", sa.Numeric(20, 8), nullable=False),
        sa.Column("profitability_policy_id", sa.Text(), nullable=False),
        sa.Column("profitability_policy_version", sa.Text(), nullable=False),
        sa.Column("risk_policy_id", sa.Text(), nullable=False),
        sa.Column("risk_policy_version", sa.Text(), nullable=False),
        sa.Column("compounding_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("profit_distribution_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("aggression_mode", sa.Text(), nullable=False, server_default=sa.text("'BALANCED'")),
        sa.Column("initial_capital", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("allocated_capital", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_capital", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("deployed_capital", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("realized_gross_pnl", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("fees", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("realized_net_pnl", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("distributable_profit", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("compounded_profit", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("withdrawn_profit", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("current_campaign_equity", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("maximum_drawdown", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("available_capital", sa.Numeric(20, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("metadata_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('DRAFT','READY','ACTIVE','PAUSED','CAPITAL_EXHAUSTED','COMPLETED','CANCELED','MANUAL_REVIEW_REQUIRED')", name="ck_ccd_status"),
        sa.CheckConstraint("aggression_mode IN ('CONSERVATIVE','BALANCED','AGGRESSIVE','MAXIMUM_GOVERNED')", name="ck_ccd_aggression_mode"),
        sa.CheckConstraint("capital_budget > 0", name="ck_ccd_capital_budget_positive"),
        sa.CheckConstraint("remaining_unallocated_capital >= 0", name="ck_ccd_remaining_capital_non_negative"),
        sa.CheckConstraint("maximum_open_positions >= 0", name="ck_ccd_max_open_positions_non_negative"),
        sa.CheckConstraint("maximum_position_size >= 0", name="ck_ccd_max_position_size_non_negative"),
        sa.CheckConstraint("minimum_position_size >= 0", name="ck_ccd_min_position_size_non_negative"),
        sa.CheckConstraint("maximum_total_exposure >= 0", name="ck_ccd_max_total_exposure_non_negative"),
        sa.CheckConstraint("maximum_position_size >= minimum_position_size", name="ck_ccd_position_size_bounds"),
        sa.CheckConstraint("initial_capital >= 0", name="ck_ccd_initial_capital_non_negative"),
        sa.CheckConstraint("allocated_capital >= 0", name="ck_ccd_allocated_capital_non_negative"),
        sa.CheckConstraint("reserved_capital >= 0", name="ck_ccd_reserved_capital_non_negative"),
        sa.CheckConstraint("deployed_capital >= 0", name="ck_ccd_deployed_capital_non_negative"),
        sa.CheckConstraint("fees >= 0", name="ck_ccd_fees_non_negative"),
        sa.CheckConstraint("maximum_drawdown >= 0", name="ck_ccd_max_drawdown_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "version", name="uq_ccd_campaign_version"),
    )
    op.create_index("ix_ccd_campaign_id", "capital_campaign_definitions", ["campaign_id"], unique=False)
    op.create_index("ix_ccd_status_created", "capital_campaign_definitions", ["status", "created_at"], unique=False)

    op.add_column("capital_campaigns", sa.Column("definition_campaign_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("capital_campaigns", sa.Column("definition_version", sa.Integer(), nullable=True))
    op.create_index(
        "ix_capital_campaigns_definition_pin",
        "capital_campaigns",
        ["definition_campaign_id", "definition_version"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_capital_campaigns_definition_pin_pair",
        "capital_campaigns",
        "(definition_campaign_id IS NULL AND definition_version IS NULL) OR (definition_campaign_id IS NOT NULL AND definition_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_capital_campaigns_definition_pin_identity",
        "capital_campaigns",
        "definition_campaign_id IS NULL OR uuid = definition_campaign_id",
    )
    op.create_foreign_key(
        "fk_capital_campaigns_definition_pin",
        "capital_campaigns",
        "capital_campaign_definitions",
        ["definition_campaign_id", "definition_version"],
        ["campaign_id", "version"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_capital_campaigns_definition_pin", "capital_campaigns", type_="foreignkey")
    op.drop_constraint("ck_capital_campaigns_definition_pin_identity", "capital_campaigns", type_="check")
    op.drop_constraint("ck_capital_campaigns_definition_pin_pair", "capital_campaigns", type_="check")
    op.drop_index("ix_capital_campaigns_definition_pin", table_name="capital_campaigns")
    op.drop_column("capital_campaigns", "definition_version")
    op.drop_column("capital_campaigns", "definition_campaign_id")

    op.drop_index("ix_ccd_status_created", table_name="capital_campaign_definitions")
    op.drop_index("ix_ccd_campaign_id", table_name="capital_campaign_definitions")
    op.drop_table("capital_campaign_definitions")
