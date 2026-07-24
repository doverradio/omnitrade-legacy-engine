"""add campaign-version autonomous execution one-shot

Revision ID: 20260724_0048
Revises: 20260724_0047
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260724_0048"
down_revision: str | None = "20260724_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_autonomous_execution_claim_campaign_version",
        "autonomous_execution_claims",
        ["campaign_id", "campaign_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_autonomous_execution_claim_campaign_version",
        "autonomous_execution_claims",
        type_="unique",
    )
