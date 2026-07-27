"""Allow the canonical execution claim to represent BUY or SELL.

Revision ID: 20260727_0054
Revises: 20260727_0053
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_0054"
down_revision = "20260727_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_aec_buy_only", "autonomous_execution_claims", type_="check")
    op.create_check_constraint("ck_aec_side", "autonomous_execution_claims", "side IN ('BUY','SELL')")


def downgrade() -> None:
    op.drop_constraint("ck_aec_side", "autonomous_execution_claims", type_="check")
    op.create_check_constraint("ck_aec_buy_only", "autonomous_execution_claims", "side = 'BUY'")
