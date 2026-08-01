"""construct a provider-disconnected autonomous exit order

Revision ID: 20260801_0061
Revises: 20260801_0060
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0061"
down_revision: str | None = "20260801_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("autonomous_execution_claims", sa.Column("provider_submission_connected", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("autonomous_position_exit_authorities", sa.Column("reserved_order_id", postgresql.UUID(as_uuid=True)))
    for name, type_ in (
        ("last_order_failure_at", sa.DateTime(timezone=True)), ("last_order_failure_code", sa.Text()),
        ("last_order_exception_class", sa.Text()), ("last_order_failure_retryable", sa.Boolean()),
    ):
        op.add_column("autonomous_position_exit_authorities", sa.Column(name, type_))

    columns = (
        sa.Column("execution_claim_id", postgresql.UUID(as_uuid=True)), sa.Column("claim_version", sa.Integer()),
        sa.Column("custody_id", postgresql.UUID(as_uuid=True)), sa.Column("evaluation_integrity_hash", sa.Text()),
        sa.Column("exit_authority_id", postgresql.UUID(as_uuid=True)), sa.Column("exit_authority_version", sa.Integer()),
        sa.Column("activation_id", postgresql.UUID(as_uuid=True)), sa.Column("originating_buy_claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("originating_reconciliation_event_id", postgresql.UUID(as_uuid=True)), sa.Column("exposure_effect", sa.Text()),
        sa.Column("requested_base_quantity", sa.Numeric()), sa.Column("normalized_base_quantity", sa.Numeric()),
        sa.Column("maximum_authorized_base_quantity", sa.Numeric()), sa.Column("expected_quote_proceeds", sa.Numeric()),
        sa.Column("capital_deployment_amount", sa.Numeric()), sa.Column("proof_eligible", sa.Boolean()),
        sa.Column("disqualification_reason", sa.Text()), sa.Column("construction_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_submission_connected", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    for column in columns:
        op.add_column("live_crypto_orders", column)
    for name, source, target, target_column in (
        ("fk_lco_execution_claim", "execution_claim_id", "autonomous_execution_claims", "claim_id"),
        ("fk_lco_custody", "custody_id", "autonomous_position_custodies", "custody_id"),
        ("fk_lco_exit_authority", "exit_authority_id", "autonomous_position_exit_authorities", "authority_id"),
        ("fk_lco_activation", "activation_id", "canonical_proving_activations", "activation_id"),
        ("fk_lco_originating_buy_claim", "originating_buy_claim_id", "autonomous_execution_claims", "claim_id"),
        ("fk_lco_originating_reconciliation", "originating_reconciliation_event_id", "live_reconciliation_events", "id"),
    ):
        op.create_foreign_key(name, "live_crypto_orders", target, [source], [target_column], ondelete="RESTRICT")
    op.create_unique_constraint("uq_lco_execution_claim", "live_crypto_orders", ["execution_claim_id"])
    op.create_check_constraint("ck_lco_exposure_effect", "live_crypto_orders", "exposure_effect IS NULL OR exposure_effect = 'REDUCE_ONLY'")
    op.create_check_constraint("ck_lco_reduce_only_constructed", "live_crypto_orders", "execution_claim_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY' AND requested_base_quantity > 0 AND normalized_base_quantity > 0 AND normalized_base_quantity <= requested_base_quantity AND normalized_base_quantity <= maximum_authorized_base_quantity AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0 AND status = 'PENDING_CONFIRMATION' AND provider_order_id IS NULL AND submitted_at IS NULL AND provider_submission_connected = false)")
    op.create_index("uq_lco_active_sell_custody_scope", "live_crypto_orders", ["custody_id"], unique=True, postgresql_where=sa.text("custody_id IS NOT NULL AND status IN ('PENDING_CONFIRMATION','VALIDATING','SUBMISSION_PENDING','ACKNOWLEDGED','SUBMITTED','PARTIALLY_FILLED','RECONCILIATION_REQUIRED','UNKNOWN')"))
    op.create_foreign_key("fk_apea_reserved_order", "autonomous_position_exit_authorities", "live_crypto_orders", ["reserved_order_id"], ["live_crypto_order_id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_apea_reserved_order", "autonomous_position_exit_authorities", ["reserved_order_id"])


def downgrade() -> None:
    op.drop_constraint("uq_apea_reserved_order", "autonomous_position_exit_authorities", type_="unique")
    op.drop_constraint("fk_apea_reserved_order", "autonomous_position_exit_authorities", type_="foreignkey")
    op.drop_column("autonomous_position_exit_authorities", "last_order_failure_retryable")
    op.drop_column("autonomous_position_exit_authorities", "last_order_exception_class")
    op.drop_column("autonomous_position_exit_authorities", "last_order_failure_code")
    op.drop_column("autonomous_position_exit_authorities", "last_order_failure_at")
    op.drop_column("autonomous_position_exit_authorities", "reserved_order_id")
    op.drop_index("uq_lco_active_sell_custody_scope", table_name="live_crypto_orders")
    op.drop_constraint("ck_lco_reduce_only_constructed", "live_crypto_orders", type_="check")
    op.drop_constraint("ck_lco_exposure_effect", "live_crypto_orders", type_="check")
    op.drop_constraint("uq_lco_execution_claim", "live_crypto_orders", type_="unique")
    for name in ("fk_lco_originating_reconciliation", "fk_lco_originating_buy_claim", "fk_lco_activation", "fk_lco_exit_authority", "fk_lco_custody", "fk_lco_execution_claim"):
        op.drop_constraint(name, "live_crypto_orders", type_="foreignkey")
    for name in ("provider_submission_connected", "construction_expires_at", "disqualification_reason", "proof_eligible", "capital_deployment_amount", "expected_quote_proceeds", "maximum_authorized_base_quantity", "normalized_base_quantity", "requested_base_quantity", "exposure_effect", "originating_reconciliation_event_id", "originating_buy_claim_id", "activation_id", "exit_authority_version", "exit_authority_id", "evaluation_integrity_hash", "custody_id", "claim_version", "execution_claim_id"):
        op.drop_column("live_crypto_orders", name)
    op.drop_column("autonomous_execution_claims", "provider_submission_connected")
