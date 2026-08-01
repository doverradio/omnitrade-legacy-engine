"""add one-attempt autonomous proof sell worker state

Revision ID: 20260801_0064
Revises: 20260801_0063
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0064"
down_revision: str | None = "20260801_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_proof_sell_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("custody_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("runtime_campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False, server_default=sa.text("'SELECTED'")),
        sa.Column("authority_id", postgresql.UUID(as_uuid=True)),
        sa.Column("package_id", postgresql.UUID(as_uuid=True)),
        sa.Column("activation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("order_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reconciliation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("blocker", sa.Text()), sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("hard_stopped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("terminal_reason", sa.Text()),
        sa.Column("proof_sell_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["custody_id"], ["autonomous_position_custodies.custody_id"], name="fk_apsa_custody", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_id", "campaign_version"], ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"], name="fk_apsa_campaign_definition", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_campaign_id"], ["capital_campaigns.uuid"], name="fk_apsa_runtime_campaign", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["authority_id"], ["autonomous_position_exit_authorities.authority_id"], name="fk_apsa_authority", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_id"], ["canonical_preview_packages.package_id"], name="fk_apsa_package", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["activation_id"], ["canonical_proving_activations.activation_id"], name="fk_apsa_activation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["claim_id"], ["autonomous_execution_claims.claim_id"], name="fk_apsa_claim", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["live_crypto_orders.live_crypto_order_id"], name="fk_apsa_order", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["live_reconciliation_events.id"], name="fk_apsa_reconciliation", ondelete="RESTRICT"),
        sa.UniqueConstraint("custody_id", name="uq_apsa_custody"),
        sa.UniqueConstraint("campaign_id", "campaign_version", "runtime_campaign_id", name="uq_apsa_campaign_attempt"),
        sa.CheckConstraint("stage IN ('SELECTED','EVALUATED','AUTHORIZED','PACKAGED','CLAIMED','ORDERED','RECONCILING','TERMINAL')", name="ck_apsa_stage"),
        sa.CheckConstraint("(stage = 'TERMINAL') = hard_stopped", name="ck_apsa_terminal_hard_stop"),
        sa.CheckConstraint("retry_count >= 0", name="ck_apsa_retry_count"),
    )
    op.create_index("ix_apsa_due", "autonomous_proof_sell_attempts", ["stage", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_apsa_due", table_name="autonomous_proof_sell_attempts")
    op.drop_table("autonomous_proof_sell_attempts")
