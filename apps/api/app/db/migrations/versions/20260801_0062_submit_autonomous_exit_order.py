"""allow coherent provider lifecycle for autonomous exit orders

Revision ID: 20260801_0062
Revises: 20260801_0061
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260801_0062"
down_revision: str | None = "20260801_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LIFECYCLE = """execution_claim_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY'
AND requested_base_quantity > 0 AND normalized_base_quantity > 0
AND normalized_base_quantity <= requested_base_quantity
AND normalized_base_quantity <= maximum_authorized_base_quantity
AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0
AND ((proof_eligible = true AND disqualification_reason IS NULL)
OR (proof_eligible = false AND disqualification_reason IS NOT NULL))
AND ((status = 'PENDING_CONFIRMATION' AND provider_order_id IS NULL
AND submitted_at IS NULL AND provider_submission_connected = false)
OR (status IN ('SUBMISSION_PENDING','REJECTED') AND provider_order_id IS NULL
AND submitted_at IS NOT NULL AND provider_submission_connected = true)
OR (status IN ('RECONCILIATION_REQUIRED','UNKNOWN')
AND submitted_at IS NOT NULL AND provider_submission_connected = true)
OR (status IN ('ACKNOWLEDGED','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELLED')
AND provider_order_id IS NOT NULL AND submitted_at IS NOT NULL
AND provider_submission_connected = true)))"""

_CONSTRUCTION_ONLY = """execution_claim_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY'
AND requested_base_quantity > 0 AND normalized_base_quantity > 0
AND normalized_base_quantity <= requested_base_quantity
AND normalized_base_quantity <= maximum_authorized_base_quantity
AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0
AND status = 'PENDING_CONFIRMATION' AND provider_order_id IS NULL
AND submitted_at IS NULL AND provider_submission_connected = false)"""


def upgrade() -> None:
    op.drop_constraint("ck_lco_reduce_only_constructed", "live_crypto_orders", type_="check")
    op.create_check_constraint("ck_lco_reduce_only_lifecycle", "live_crypto_orders", _LIFECYCLE)


def downgrade() -> None:
    op.drop_constraint("ck_lco_reduce_only_lifecycle", "live_crypto_orders", type_="check")
    op.create_check_constraint("ck_lco_reduce_only_constructed", "live_crypto_orders", _CONSTRUCTION_ONLY)
