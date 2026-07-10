"""allow live crypto dry run statuses

Revision ID: 20260709_0022
Revises: 20260709_0021
Create Date: 2026-07-09 23:40:00.000000

"""
from collections.abc import Sequence

from alembic import op


revision: str = "20260709_0022"
down_revision: str | None = "20260709_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_live_crypto_orders_status", "live_crypto_orders", type_="check")
    op.create_check_constraint(
        "ck_live_crypto_orders_status",
        "live_crypto_orders",
        "status IN ('DRY_RUN_READY','DRY_RUN_BLOCKED','PENDING_CONFIRMATION','CONFIRMATION_EXPIRED','VALIDATING','RISK_REJECTED','SUBMISSION_PENDING','SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','RECONCILIATION_REQUIRED','UNKNOWN')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_live_crypto_orders_status", "live_crypto_orders", type_="check")
    op.create_check_constraint(
        "ck_live_crypto_orders_status",
        "live_crypto_orders",
        "status IN ('PENDING_CONFIRMATION','CONFIRMATION_EXPIRED','VALIDATING','RISK_REJECTED','SUBMISSION_PENDING','SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','RECONCILIATION_REQUIRED','UNKNOWN')",
    )