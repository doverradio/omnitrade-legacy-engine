"""add autonomous capital mandate engine persistence

Revision ID: 20260712_0029
Revises: 20260710_0028
Create Date: 2026-07-12 10:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0029"
down_revision: str | None = "20260710_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_capital_mandates",
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_actor_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("autonomy_level", sa.Text(), nullable=False, server_default=sa.text("'LEVEL_1'")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("exchange_environment", sa.Text(), nullable=False),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("live_trading_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("capital_campaign_id", sa.Integer(), nullable=True),
        sa.Column("approval_mode_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('DRAFT','PENDING_AUTHORIZATION','AUTHORIZED','ACTIVE','PAUSED','EXIT_ONLY','EXPIRED','REVOKED','KILLED','COMPLETED')",
            name="ck_ac_mandates_status",
        ),
        sa.CheckConstraint("autonomy_level IN ('LEVEL_0','LEVEL_1','LEVEL_2','LEVEL_3')", name="ck_ac_mandates_autonomy_level"),
        sa.CheckConstraint("exchange_environment IN ('production','sandbox')", name="ck_ac_mandates_exchange_environment"),
        sa.CheckConstraint("approval_mode_default = true", name="ck_ac_mandates_human_approval_default"),
        sa.ForeignKeyConstraint(["exchange_connection_id"], ["exchange_connections.exchange_connection_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_trading_profile_id"], ["live_trading_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["capital_campaign_id"], ["capital_campaigns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("mandate_id"),
    )
    op.create_index("ix_ac_mandates_owner_actor", "autonomous_capital_mandates", ["owner_actor_id"])
    op.create_index("ix_ac_mandates_status", "autonomous_capital_mandates", ["status"])
    op.create_index("ix_ac_mandates_autonomy_level", "autonomous_capital_mandates", ["autonomy_level"])
    op.create_index("ix_ac_mandates_live_profile", "autonomous_capital_mandates", ["live_trading_profile_id"])
    op.create_index("ix_ac_mandates_campaign", "autonomous_capital_mandates", ["capital_campaign_id"])

    op.create_table(
        "autonomous_capital_mandate_versions",
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_hash", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.Text(), nullable=False),
        sa.Column("authorized_capital_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_order_notional_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_open_exposure_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_daily_deployed_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_daily_realized_loss_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_campaign_drawdown_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("position_limit", sa.Integer(), nullable=False),
        sa.Column("price_evidence_max_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_slippage_bps", sa.Numeric(10, 4), nullable=False),
        sa.Column("max_fee_bps", sa.Numeric(10, 4), nullable=False),
        sa.Column("allowed_products", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_order_sides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_strategy_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entry_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exit_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cooldown_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("operating_schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approval_policy", sa.Text(), nullable=False, server_default=sa.text("'HUMAN_REQUIRED'")),
        sa.Column("reconciliation_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kill_switch_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("owner_acknowledgements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("authorization_evidence_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_authorized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version_number >= 1", name="ck_ac_mandate_versions_version_number"),
        sa.CheckConstraint("authorized_capital_usd > 0", name="ck_ac_mandate_versions_authorized_capital"),
        sa.CheckConstraint("max_order_notional_usd > 0", name="ck_ac_mandate_versions_max_order_notional"),
        sa.CheckConstraint("max_open_exposure_usd > 0", name="ck_ac_mandate_versions_max_open_exposure"),
        sa.CheckConstraint("max_daily_deployed_usd > 0", name="ck_ac_mandate_versions_max_daily_deployed"),
        sa.CheckConstraint("max_daily_realized_loss_usd >= 0", name="ck_ac_mandate_versions_max_daily_loss"),
        sa.CheckConstraint("max_campaign_drawdown_usd >= 0", name="ck_ac_mandate_versions_max_drawdown"),
        sa.CheckConstraint("max_consecutive_losses >= 0", name="ck_ac_mandate_versions_max_consecutive_losses"),
        sa.CheckConstraint("position_limit >= 0", name="ck_ac_mandate_versions_position_limit"),
        sa.CheckConstraint("price_evidence_max_age_seconds > 0", name="ck_ac_mandate_versions_price_freshness"),
        sa.CheckConstraint("max_slippage_bps >= 0", name="ck_ac_mandate_versions_max_slippage"),
        sa.CheckConstraint("max_fee_bps >= 0", name="ck_ac_mandate_versions_max_fee"),
        sa.CheckConstraint("approval_policy IN ('HUMAN_REQUIRED','MANDATE_ALLOWED')", name="ck_ac_mandate_versions_approval_policy"),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mandate_version_id"),
        sa.UniqueConstraint("mandate_id", "version_number", name="uq_ac_mandate_versions_mandate_version_number"),
        sa.UniqueConstraint("version_hash", name="uq_ac_mandate_versions_hash"),
    )
    op.create_index("ix_ac_mandate_versions_mandate", "autonomous_capital_mandate_versions", ["mandate_id"])
    op.create_index("ix_ac_mandate_versions_active", "autonomous_capital_mandate_versions", ["is_active"])
    op.create_index("ix_ac_mandate_versions_authorized", "autonomous_capital_mandate_versions", ["is_authorized"])

    op.create_table(
        "autonomous_capital_mandate_authorizations",
        sa.Column("mandate_authorization_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorization_state", sa.Text(), nullable=False),
        sa.Column("approval_result", sa.Text(), nullable=False, server_default=sa.text("'APPROVAL_REQUIRED_HUMAN'")),
        sa.Column("authorized_by_actor_id", sa.Text(), nullable=True),
        sa.Column("authorization_method", sa.Text(), nullable=False),
        sa.Column("owner_acknowledgements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("authorization_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deterministic_explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("authorization_state IN ('PENDING','AUTHORIZED','REJECTED','REVOKED')", name="ck_ac_mandate_authorizations_state"),
        sa.CheckConstraint("approval_result IN ('APPROVAL_REQUIRED_HUMAN','APPROVAL_SATISFIED_BY_ACTIVE_MANDATE')", name="ck_ac_mandate_authorizations_approval_result"),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mandate_version_id"], ["autonomous_capital_mandate_versions.mandate_version_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mandate_authorization_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ac_mandate_authorizations_idempotency"),
    )
    op.create_index("ix_ac_mandate_authorizations_mandate", "autonomous_capital_mandate_authorizations", ["mandate_id"])
    op.create_index("ix_ac_mandate_authorizations_version", "autonomous_capital_mandate_authorizations", ["mandate_version_id"])


def downgrade() -> None:
    op.drop_index("ix_ac_mandate_authorizations_version", table_name="autonomous_capital_mandate_authorizations")
    op.drop_index("ix_ac_mandate_authorizations_mandate", table_name="autonomous_capital_mandate_authorizations")
    op.drop_table("autonomous_capital_mandate_authorizations")

    op.drop_index("ix_ac_mandate_versions_authorized", table_name="autonomous_capital_mandate_versions")
    op.drop_index("ix_ac_mandate_versions_active", table_name="autonomous_capital_mandate_versions")
    op.drop_index("ix_ac_mandate_versions_mandate", table_name="autonomous_capital_mandate_versions")
    op.drop_table("autonomous_capital_mandate_versions")

    op.drop_index("ix_ac_mandates_campaign", table_name="autonomous_capital_mandates")
    op.drop_index("ix_ac_mandates_live_profile", table_name="autonomous_capital_mandates")
    op.drop_index("ix_ac_mandates_autonomy_level", table_name="autonomous_capital_mandates")
    op.drop_index("ix_ac_mandates_status", table_name="autonomous_capital_mandates")
    op.drop_index("ix_ac_mandates_owner_actor", table_name="autonomous_capital_mandates")
    op.drop_table("autonomous_capital_mandates")
