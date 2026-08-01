"""bind continuing exit authority to its canonical SELL paperwork

Revision ID: 20260801_0059
Revises: 20260731_0058
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0059"
down_revision: str | None = "20260731_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_cpp_proposed_cap", "canonical_preview_packages", type_="check")
    op.drop_constraint("ck_cpp_approved_cap", "canonical_preview_packages", type_="check")
    op.add_column("canonical_preview_packages", sa.Column("capital_deployment_amount", sa.Numeric()))
    op.add_column("canonical_preview_packages", sa.Column("proposed_base_quantity", sa.Numeric()))
    op.add_column("canonical_preview_packages", sa.Column("maximum_authorized_base_quantity", sa.Numeric()))
    op.add_column("canonical_preview_packages", sa.Column("expected_quote_proceeds", sa.Numeric()))
    op.execute("UPDATE canonical_preview_packages SET capital_deployment_amount = CASE WHEN side = 'BUY' THEN proposed_order_amount ELSE 0 END")
    op.execute("""UPDATE canonical_preview_packages AS package SET
        proposed_base_quantity = preview.base_size,
        maximum_authorized_base_quantity = preview.base_size,
        expected_quote_proceeds = COALESCE(preview.estimated_quote_size, preview.estimated_total_value, package.proposed_order_amount)
        FROM crypto_order_previews AS preview
        WHERE package.side = 'SELL' AND preview.crypto_order_preview_id = package.crypto_order_preview_id""")
    op.create_check_constraint("ck_cpp_proposed_cap", "canonical_preview_packages", "side = 'SELL' OR proposed_order_amount <= 5")
    op.create_check_constraint("ck_cpp_approved_cap", "canonical_preview_packages", "side = 'SELL' OR risk_approved_amount <= 5")
    op.create_check_constraint("ck_cpp_side_aware_capital", "canonical_preview_packages", "capital_deployment_amount IS NULL OR (side = 'BUY' AND capital_deployment_amount > 0 AND capital_deployment_amount <= 5) OR (side = 'SELL' AND capital_deployment_amount = 0)")
    op.create_check_constraint("ck_cpp_side_aware_quantity", "canonical_preview_packages", "(proposed_base_quantity IS NULL AND maximum_authorized_base_quantity IS NULL AND expected_quote_proceeds IS NULL) OR (side = 'BUY' AND proposed_base_quantity IS NULL AND maximum_authorized_base_quantity IS NULL) OR (side = 'SELL' AND proposed_base_quantity > 0 AND maximum_authorized_base_quantity > 0 AND proposed_base_quantity <= maximum_authorized_base_quantity AND expected_quote_proceeds > 0)")

    op.drop_constraint("ck_cpa_max_order_cap", "canonical_proving_activations", type_="check")
    op.drop_constraint("ck_cpa_deployed_positive", "canonical_proving_activations", type_="check")
    op.add_column("canonical_proving_activations", sa.Column("side", sa.Text(), server_default=sa.text("'BUY'"), nullable=False))
    op.add_column("canonical_proving_activations", sa.Column("maximum_authorized_base_quantity", sa.Numeric()))
    op.execute("""UPDATE canonical_proving_activations AS activation SET
        side = package.side,
        max_deployed_capital = CASE WHEN package.side = 'BUY' THEN activation.max_deployed_capital ELSE 0 END,
        maximum_authorized_base_quantity = package.maximum_authorized_base_quantity
        FROM canonical_preview_packages AS package WHERE package.package_id = activation.package_id""")
    op.create_check_constraint("ck_cpa_max_order_cap", "canonical_proving_activations", "side = 'SELL' OR max_order_amount <= 5")
    op.create_check_constraint("ck_cpa_deployed_positive", "canonical_proving_activations", "(side = 'BUY' AND max_deployed_capital > 0) OR (side = 'SELL' AND max_deployed_capital = 0 AND maximum_authorized_base_quantity > 0)")
    op.add_column("autonomous_position_exit_authorities", sa.Column("reserved_decision_id", postgresql.UUID(as_uuid=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("reserved_package_id", postgresql.UUID(as_uuid=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_construction_failure_at", sa.DateTime(timezone=True)))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_construction_failure_code", sa.Text()))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_construction_exception_class", sa.Text()))
    op.add_column("autonomous_position_exit_authorities", sa.Column("last_construction_failure_retryable", sa.Boolean()))
    op.create_foreign_key("fk_apea_reserved_decision", "autonomous_position_exit_authorities", "decision_records", ["reserved_decision_id"], ["decision_id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_apea_reserved_package", "autonomous_position_exit_authorities", "canonical_preview_packages", ["reserved_package_id"], ["package_id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_apea_reserved_decision", "autonomous_position_exit_authorities", ["reserved_decision_id"])
    op.create_unique_constraint("uq_apea_reserved_package", "autonomous_position_exit_authorities", ["reserved_package_id"])
    op.create_check_constraint(
        "ck_apea_reservation_binding", "autonomous_position_exit_authorities",
        "(reserved_decision_id IS NULL AND reserved_package_id IS NULL) OR "
        "(reserved_decision_id IS NOT NULL AND reserved_package_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_apea_reservation_binding", "autonomous_position_exit_authorities", type_="check")
    op.drop_constraint("uq_apea_reserved_package", "autonomous_position_exit_authorities", type_="unique")
    op.drop_constraint("uq_apea_reserved_decision", "autonomous_position_exit_authorities", type_="unique")
    op.drop_constraint("fk_apea_reserved_package", "autonomous_position_exit_authorities", type_="foreignkey")
    op.drop_constraint("fk_apea_reserved_decision", "autonomous_position_exit_authorities", type_="foreignkey")
    op.drop_column("autonomous_position_exit_authorities", "last_construction_failure_retryable")
    op.drop_column("autonomous_position_exit_authorities", "last_construction_exception_class")
    op.drop_column("autonomous_position_exit_authorities", "last_construction_failure_code")
    op.drop_column("autonomous_position_exit_authorities", "last_construction_failure_at")
    op.drop_column("autonomous_position_exit_authorities", "reserved_package_id")
    op.drop_column("autonomous_position_exit_authorities", "reserved_decision_id")
    op.drop_constraint("ck_cpa_deployed_positive", "canonical_proving_activations", type_="check")
    op.drop_constraint("ck_cpa_max_order_cap", "canonical_proving_activations", type_="check")
    op.drop_column("canonical_proving_activations", "maximum_authorized_base_quantity")
    op.drop_column("canonical_proving_activations", "side")
    op.create_check_constraint("ck_cpa_deployed_positive", "canonical_proving_activations", "max_deployed_capital > 0")
    op.create_check_constraint("ck_cpa_max_order_cap", "canonical_proving_activations", "max_order_amount <= 5")
    op.drop_constraint("ck_cpp_side_aware_quantity", "canonical_preview_packages", type_="check")
    op.drop_constraint("ck_cpp_side_aware_capital", "canonical_preview_packages", type_="check")
    op.drop_constraint("ck_cpp_approved_cap", "canonical_preview_packages", type_="check")
    op.drop_constraint("ck_cpp_proposed_cap", "canonical_preview_packages", type_="check")
    op.drop_column("canonical_preview_packages", "expected_quote_proceeds")
    op.drop_column("canonical_preview_packages", "maximum_authorized_base_quantity")
    op.drop_column("canonical_preview_packages", "proposed_base_quantity")
    op.drop_column("canonical_preview_packages", "capital_deployment_amount")
    op.create_check_constraint("ck_cpp_approved_cap", "canonical_preview_packages", "risk_approved_amount <= 5")
    op.create_check_constraint("ck_cpp_proposed_cap", "canonical_preview_packages", "proposed_order_amount <= 5")
