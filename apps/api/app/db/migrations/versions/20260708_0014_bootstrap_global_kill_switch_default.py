"""bootstrap global kill switch default row

Revision ID: 20260708_0014
Revises: b643e28c53a4
Create Date: 2026-07-08 00:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260708_0014"
down_revision: str | None = "b643e28c53a4"
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
                'global',
                NULL,
                false,
                false,
                'bootstrap_default',
                'system_bootstrap',
                now()
            WHERE NOT EXISTS (
                SELECT 1
                FROM risk_kill_switches
                WHERE scope = 'global'
                  AND paper_account_id IS NULL
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM risk_kill_switches
            WHERE scope = 'global'
              AND paper_account_id IS NULL
              AND engaged = false
              AND rearm_required = false
              AND changed_by = 'system_bootstrap'
              AND reason = 'bootstrap_default'
            """
        )
    )
