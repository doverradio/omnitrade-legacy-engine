"""backfill account kill switch bootstrap rows

Revision ID: 20260708_0015
Revises: 20260708_0014
Create Date: 2026-07-08 00:30:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260708_0015"
down_revision: str | None = "20260708_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO risk_kill_switches (
                id,
                scope,
                paper_account_id,
                engaged,
                rearm_required,
                reason,
                changed_by,
                changed_at
            )
            SELECT
                gen_random_uuid(),
                'account',
                pa.id,
                false,
                false,
                'account_bootstrap_default',
                'system_bootstrap',
                now()
            FROM paper_accounts pa
            WHERE NOT EXISTS (
                SELECT 1
                FROM risk_kill_switches rks
                WHERE rks.scope = 'account'
                  AND rks.paper_account_id = pa.id
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM risk_kill_switches rks
            WHERE rks.scope = 'account'
              AND rks.engaged = false
              AND rks.rearm_required = false
              AND rks.changed_by = 'system_bootstrap'
              AND rks.reason = 'account_bootstrap_default'
            """
        )
    )
