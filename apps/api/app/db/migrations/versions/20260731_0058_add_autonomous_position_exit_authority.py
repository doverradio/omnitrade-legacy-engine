"""add durable autonomous position continuing exit authority

Revision ID: 20260731_0058
Revises: 20260731_0057
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0058"
down_revision: str | None = "20260731_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_apc_continuing_authority", "autonomous_position_custodies", type_="check")
    op.create_check_constraint(
        "ck_apc_continuing_authority", "autonomous_position_custodies",
        "continuing_exit_authority_state IN ('UNARMED','PENDING','ARMED','RESERVED','CONSUMED','EXPIRED','REVOKED','BLOCKED')",
    )
    op.create_table(
        "autonomous_position_exit_authorities",
        sa.Column("authority_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("authority_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("authority_state", sa.Text(), nullable=False),
        sa.Column("custody_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("live_trading_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False), sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("originating_buy_claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("originating_reconciliation_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provenance_classification", sa.Text(), nullable=False),
        sa.Column("proof_eligible", sa.Boolean(), nullable=False), sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("evaluation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_integrity_hash", sa.Text(), nullable=False),
        sa.Column("authoritative_quantity_at_issuance", sa.Numeric(), nullable=False),
        sa.Column("maximum_sell_quantity", sa.Numeric(), nullable=False),
        sa.Column("side", sa.Text(), server_default=sa.text("'SELL'"), nullable=False),
        sa.Column("exposure_effect", sa.Text(), server_default=sa.text("'REDUCE_ONLY'"), nullable=False),
        sa.Column("buy_forbidden", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("increased_exposure_forbidden", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("policy_evidence", postgresql.JSONB(), nullable=False), sa.Column("risk_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("blockers", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True)), sa.Column("reservation_expires_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)), sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("authority_state IN ('UNARMED','ARMED','RESERVED','CONSUMED','REVOKED','EXPIRED','BLOCKED')", name="ck_apea_state"),
        sa.CheckConstraint("side = 'SELL'", name="ck_apea_sell_only"),
        sa.CheckConstraint("exposure_effect = 'REDUCE_ONLY'", name="ck_apea_reduce_only"),
        sa.CheckConstraint("authoritative_quantity_at_issuance > 0", name="ck_apea_quantity_positive"),
        sa.CheckConstraint("maximum_sell_quantity > 0 AND maximum_sell_quantity <= authoritative_quantity_at_issuance", name="ck_apea_max_quantity"),
        sa.CheckConstraint("buy_forbidden = true AND increased_exposure_forbidden = true", name="ck_apea_exposure_forbidden"),
        sa.CheckConstraint("classification IN ('PROOF_ELIGIBLE_AUTONOMOUS','NONQUALIFYING_PROTECTIVE_EXIT')", name="ck_apea_classification"),
        sa.ForeignKeyConstraint(["custody_id"], ["autonomous_position_custodies.custody_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_trading_profile_id"], ["live_trading_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exchange_connection_id"], ["exchange_connections.exchange_connection_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["originating_buy_claim_id"], ["autonomous_execution_claims.claim_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["originating_reconciliation_event_id"], ["live_reconciliation_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("authority_id"),
        sa.UniqueConstraint("custody_id", "evaluation_integrity_hash", name="uq_apea_custody_evaluation"),
    )
    op.create_index("ix_apea_custody_state", "autonomous_position_exit_authorities", ["custody_id", "authority_state"])
    predicate = sa.text("authority_state IN ('ARMED','RESERVED')")
    op.create_index("uq_apea_active_custody", "autonomous_position_exit_authorities", ["custody_id"], unique=True, postgresql_where=predicate)
    op.create_index("uq_apea_active_position_scope", "autonomous_position_exit_authorities", ["live_trading_profile_id", "product"], unique=True, postgresql_where=predicate)


def downgrade() -> None:
    op.drop_index("uq_apea_active_position_scope", table_name="autonomous_position_exit_authorities")
    op.drop_index("uq_apea_active_custody", table_name="autonomous_position_exit_authorities")
    op.drop_index("ix_apea_custody_state", table_name="autonomous_position_exit_authorities")
    op.drop_table("autonomous_position_exit_authorities")
    op.drop_constraint("ck_apc_continuing_authority", "autonomous_position_custodies", type_="check")
    op.create_check_constraint(
        "ck_apc_continuing_authority", "autonomous_position_custodies",
        "continuing_exit_authority_state IN ('UNARMED','PENDING','ARMED','EXPIRED','REVOKED')",
    )
