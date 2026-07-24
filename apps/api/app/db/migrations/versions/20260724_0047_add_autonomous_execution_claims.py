"""add durable autonomous execution claims

Revision ID: 20260724_0047
Revises: 20260722_0046
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0047"
down_revision: str | None = "20260722_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_execution_claims",
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False), sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False), sa.Column("side", sa.Text(), nullable=False),
        sa.Column("claim_status", sa.Text(), nullable=False), sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_owner", sa.Text(), nullable=False), sa.Column("recover_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("live_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reconciliation_state", sa.Text(), nullable=True), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["package_id"], ["canonical_preview_packages.package_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["activation_id"], ["canonical_proving_activations.activation_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_id", "campaign_version"], ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"], name="fk_aec_campaign_definition", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_version_id"], ["autonomous_capital_mandate_versions.mandate_version_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_id"], ["live_trading_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["connection_id"], ["exchange_connections.exchange_connection_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_order_id"], ["live_crypto_orders.live_crypto_order_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("package_id", name="uq_autonomous_execution_claim_package"),
        sa.UniqueConstraint("activation_id", name="uq_autonomous_execution_claim_activation"),
        sa.CheckConstraint("side = 'BUY'", name="ck_aec_buy_only"),
        sa.CheckConstraint("attempt_count >= 1", name="ck_aec_attempt_count"),
        sa.CheckConstraint("claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','SAFETY_DISABLED','RECONCILIATION_REQUIRED','BUY_RECONCILED','POSITION_OPENED','COMPLETED','BLOCKED','FAILED_PRE_PROVIDER','RECOVERY_REQUIRED','CANCELLED')", name="ck_aec_status"),
    )
    op.create_index("ix_aec_status_recovery", "autonomous_execution_claims", ["claim_status", "recover_after"])
    op.create_index("ix_aec_scope", "autonomous_execution_claims", ["campaign_id", "campaign_version", "provider", "environment", "product"])


def downgrade() -> None:
    op.drop_index("ix_aec_scope", table_name="autonomous_execution_claims")
    op.drop_index("ix_aec_status_recovery", table_name="autonomous_execution_claims")
    op.drop_table("autonomous_execution_claims")
