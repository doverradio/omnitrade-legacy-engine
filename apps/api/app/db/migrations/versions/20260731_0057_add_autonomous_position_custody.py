"""add durable ordinary-production autonomous position custody

Revision ID: 20260731_0057
Revises: 20260729_0056
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0057"
down_revision: str | None = "20260729_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_position_custodies",
        sa.Column("custody_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("custody_state", sa.Text(), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("originating_autonomous_cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("originating_campaign_cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("runtime_campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_activation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_live_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buy_reconciliation_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("live_trading_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("original_acquired_quantity", sa.Numeric(), nullable=False),
        sa.Column("observed_remaining_quantity", sa.Numeric(), nullable=False),
        sa.Column("quantity_authority", sa.Text(), server_default=sa.text("'live_accounting_records'"), nullable=False),
        sa.Column("autonomous_origin", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("provenance_classification", sa.Text(), nullable=False),
        sa.Column("proof_eligible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("disqualification_reason", sa.Text(), nullable=True),
        sa.Column("disqualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_exit_evaluation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_exit_evaluation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_sell_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_sell_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_sell_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_sell_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("continuing_exit_authority_state", sa.Text(), server_default=sa.text("'UNARMED'"), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','CLOSED','RECOVERED','BLOCKED')", name="ck_apc_state"),
        sa.CheckConstraint("autonomous_origin = true", name="ck_apc_autonomous_origin"),
        sa.CheckConstraint("original_acquired_quantity > 0", name="ck_apc_original_quantity_positive"),
        sa.CheckConstraint("observed_remaining_quantity >= 0", name="ck_apc_remaining_quantity_nonnegative"),
        sa.CheckConstraint("continuing_exit_authority_state IN ('UNARMED','PENDING','ARMED','EXPIRED','REVOKED')", name="ck_apc_continuing_authority"),
        sa.CheckConstraint(
            "(proof_eligible = true AND disqualification_reason IS NULL AND disqualified_at IS NULL) "
            "OR (proof_eligible = false AND disqualification_reason IS NOT NULL AND disqualified_at IS NOT NULL)",
            name="ck_apc_proof_disqualification",
        ),
        sa.ForeignKeyConstraint(["originating_autonomous_cycle_id"], ["autonomous_cycle_runs.cycle_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["originating_campaign_cycle_id"], ["autonomous_cycle_runs.cycle_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_id", "campaign_version"], ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"], name="fk_apc_campaign_definition", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_campaign_id"], ["capital_campaigns.uuid"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_id"], ["autonomous_capital_mandates.mandate_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_version_id"], ["autonomous_capital_mandate_versions.mandate_version_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_record_id"], ["decision_records.decision_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buy_package_id"], ["canonical_preview_packages.package_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buy_activation_id"], ["canonical_proving_activations.activation_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buy_claim_id"], ["autonomous_execution_claims.claim_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buy_live_order_id"], ["live_crypto_orders.live_crypto_order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buy_reconciliation_event_id"], ["live_reconciliation_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_trading_profile_id"], ["live_trading_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exchange_connection_id"], ["exchange_connections.exchange_connection_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["active_sell_decision_id"], ["decision_records.decision_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["active_sell_package_id"], ["canonical_preview_packages.package_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["active_sell_claim_id"], ["autonomous_execution_claims.claim_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["active_sell_order_id"], ["live_crypto_orders.live_crypto_order_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("custody_id"),
        sa.UniqueConstraint("buy_claim_id", name="uq_apc_buy_claim"),
        sa.UniqueConstraint("buy_package_id", name="uq_apc_buy_package"),
        sa.UniqueConstraint("buy_live_order_id", name="uq_apc_buy_order"),
    )
    op.create_index("ix_apc_state_next_evaluation", "autonomous_position_custodies", ["custody_state", "next_exit_evaluation_at"])
    op.create_index("ix_apc_scope", "autonomous_position_custodies", ["live_trading_profile_id", "provider", "environment", "product"])
    op.create_index(
        "uq_apc_nonterminal_position_scope", "autonomous_position_custodies",
        ["live_trading_profile_id", "product"],
        unique=True,
        postgresql_where=sa.text("custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','BLOCKED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_apc_nonterminal_position_scope", table_name="autonomous_position_custodies")
    op.drop_index("ix_apc_scope", table_name="autonomous_position_custodies")
    op.drop_index("ix_apc_state_next_evaluation", table_name="autonomous_position_custodies")
    op.drop_table("autonomous_position_custodies")
