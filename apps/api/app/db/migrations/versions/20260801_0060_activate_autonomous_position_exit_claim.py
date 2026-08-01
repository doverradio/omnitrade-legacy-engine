"""activate one authority-bound autonomous position exit claim

Revision ID: 20260801_0060
Revises: 20260801_0059
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0060"
down_revision: str | None = "20260801_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("canonical_proving_activations", "dry_run_live_crypto_order_id", nullable=True)
    op.drop_constraint("ck_cpa_authority_source", "canonical_proving_activations", type_="check")
    op.drop_constraint("ck_cpa_authority_evidence", "canonical_proving_activations", type_="check")
    op.create_check_constraint("ck_cpa_authority_source", "canonical_proving_activations", "authority_source IN ('HUMAN','MANDATE','CONTINUING_EXIT')")
    op.create_check_constraint("ck_cpa_authority_evidence", "canonical_proving_activations", "(authority_source = 'HUMAN' AND approval_event_id IS NOT NULL AND mandate_evaluation_id IS NULL) OR (authority_source = 'MANDATE' AND approval_event_id IS NULL AND mandate_evaluation_id IS NOT NULL) OR (authority_source = 'CONTINUING_EXIT' AND approval_event_id IS NULL AND mandate_evaluation_id IS NULL AND side = 'SELL' AND max_deployed_capital = 0 AND maximum_authorized_base_quantity > 0)")

    op.add_column("autonomous_position_exit_authorities", sa.Column("reserved_activation_id", postgresql.UUID(as_uuid=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("reserved_claim_id", postgresql.UUID(as_uuid=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_activation_failure_at", sa.DateTime(timezone=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_activation_failure_code", sa.Text()))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_activation_exception_class", sa.Text()))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_activation_failure_retryable", sa.Boolean()))

    columns = (
        sa.Column("claim_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("custody_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evaluation_integrity_hash", sa.Text()),
        sa.Column("exit_authority_id", postgresql.UUID(as_uuid=True)),
        sa.Column("exit_authority_version", sa.Integer()),
        sa.Column("originating_buy_claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("originating_reconciliation_event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("exposure_effect", sa.Text()),
        sa.Column("claimed_base_quantity", sa.Numeric()),
        sa.Column("maximum_authorized_base_quantity", sa.Numeric()),
        sa.Column("expected_quote_proceeds", sa.Numeric()),
        sa.Column("capital_deployment_amount", sa.Numeric()),
        sa.Column("preview_id", postgresql.UUID(as_uuid=True)),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("proof_eligible", sa.Boolean()),
        sa.Column("disqualification_reason", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("authority_evidence", postgresql.JSONB()),
    )
    for column in columns:
        op.add_column("autonomous_execution_claims", column)
    for name, column in (
        ("uq_aec_idempotency_key", "idempotency_key"),
        ("uq_aec_custody_id", "custody_id"),
        ("uq_aec_exit_authority_id", "exit_authority_id"),
    ):
        op.create_unique_constraint(name, "autonomous_execution_claims", [column])
    for name, source, target, target_col in (
        ("fk_aec_custody", "custody_id", "autonomous_position_custodies", "custody_id"),
        ("fk_aec_exit_authority", "exit_authority_id", "autonomous_position_exit_authorities", "authority_id"),
        ("fk_aec_originating_buy_claim", "originating_buy_claim_id", "autonomous_execution_claims", "claim_id"),
        ("fk_aec_originating_reconciliation", "originating_reconciliation_event_id", "live_reconciliation_events", "id"),
        ("fk_aec_preview", "preview_id", "crypto_order_previews", "crypto_order_preview_id"),
        ("fk_aec_risk_event", "risk_event_id", "risk_events", "id"),
    ):
        op.create_foreign_key(name, "autonomous_execution_claims", target, [source], [target_col], ondelete="RESTRICT")
    op.create_check_constraint("ck_aec_exposure_effect", "autonomous_execution_claims", "exposure_effect IS NULL OR exposure_effect = 'REDUCE_ONLY'")
    op.create_check_constraint("ck_aec_reduce_only_custody_claim", "autonomous_execution_claims", "custody_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY' AND claimed_base_quantity > 0 AND maximum_authorized_base_quantity > 0 AND claimed_base_quantity <= maximum_authorized_base_quantity AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0 AND exit_authority_id IS NOT NULL AND evaluation_integrity_hash IS NOT NULL AND originating_buy_claim_id IS NOT NULL AND originating_reconciliation_event_id IS NOT NULL)")
    op.create_check_constraint("ck_aec_proof_classification", "autonomous_execution_claims", "custody_id IS NULL OR ((proof_eligible = true AND disqualification_reason IS NULL) OR (proof_eligible = false AND disqualification_reason IS NOT NULL))")
    op.create_index("uq_aec_active_sell_custody_scope", "autonomous_execution_claims", ["profile_id", "product"], unique=True, postgresql_where=sa.text("custody_id IS NOT NULL AND claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED','RECOVERY_REQUIRED')"))

    op.create_foreign_key("fk_apea_reserved_activation", "autonomous_position_exit_authorities", "canonical_proving_activations", ["reserved_activation_id"], ["activation_id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_apea_reserved_claim", "autonomous_position_exit_authorities", "autonomous_execution_claims", ["reserved_claim_id"], ["claim_id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_apea_reserved_activation", "autonomous_position_exit_authorities", ["reserved_activation_id"])
    op.create_unique_constraint("uq_apea_reserved_claim", "autonomous_position_exit_authorities", ["reserved_claim_id"])


def downgrade() -> None:
    op.drop_constraint("uq_apea_reserved_claim", "autonomous_position_exit_authorities", type_="unique")
    op.drop_constraint("uq_apea_reserved_activation", "autonomous_position_exit_authorities", type_="unique")
    op.drop_constraint("fk_apea_reserved_claim", "autonomous_position_exit_authorities", type_="foreignkey")
    op.drop_constraint("fk_apea_reserved_activation", "autonomous_position_exit_authorities", type_="foreignkey")
    op.drop_column("autonomous_position_exit_authorities", "reserved_claim_id")
    op.drop_column("autonomous_position_exit_authorities", "reserved_activation_id")
    op.drop_column("autonomous_position_exit_authorities", "last_activation_failure_retryable")
    op.drop_column("autonomous_position_exit_authorities", "last_activation_exception_class")
    op.drop_column("autonomous_position_exit_authorities", "last_activation_failure_code")
    op.drop_column("autonomous_position_exit_authorities", "last_activation_failure_at")
    op.drop_index("uq_aec_active_sell_custody_scope", table_name="autonomous_execution_claims")
    op.drop_constraint("ck_aec_proof_classification", "autonomous_execution_claims", type_="check")
    op.drop_constraint("ck_aec_reduce_only_custody_claim", "autonomous_execution_claims", type_="check")
    op.drop_constraint("ck_aec_exposure_effect", "autonomous_execution_claims", type_="check")
    for name in ("fk_aec_risk_event", "fk_aec_preview", "fk_aec_originating_reconciliation", "fk_aec_originating_buy_claim", "fk_aec_exit_authority", "fk_aec_custody"):
        op.drop_constraint(name, "autonomous_execution_claims", type_="foreignkey")
    for name in ("uq_aec_exit_authority_id", "uq_aec_custody_id", "uq_aec_idempotency_key"):
        op.drop_constraint(name, "autonomous_execution_claims", type_="unique")
    for name in ("authority_evidence", "expires_at", "disqualification_reason", "proof_eligible", "audit_correlation_id", "risk_event_id", "preview_id", "capital_deployment_amount", "expected_quote_proceeds", "maximum_authorized_base_quantity", "claimed_base_quantity", "exposure_effect", "originating_reconciliation_event_id", "originating_buy_claim_id", "exit_authority_version", "exit_authority_id", "evaluation_integrity_hash", "custody_id", "idempotency_key", "claim_version"):
        op.drop_column("autonomous_execution_claims", name)
    op.drop_constraint("ck_cpa_authority_evidence", "canonical_proving_activations", type_="check")
    op.drop_constraint("ck_cpa_authority_source", "canonical_proving_activations", type_="check")
    op.create_check_constraint("ck_cpa_authority_source", "canonical_proving_activations", "authority_source IN ('HUMAN','MANDATE')")
    op.create_check_constraint("ck_cpa_authority_evidence", "canonical_proving_activations", "(authority_source = 'HUMAN' AND approval_event_id IS NOT NULL AND mandate_evaluation_id IS NULL) OR (authority_source = 'MANDATE' AND approval_event_id IS NULL AND mandate_evaluation_id IS NOT NULL)")
    op.alter_column("canonical_proving_activations", "dry_run_live_crypto_order_id", nullable=False)
