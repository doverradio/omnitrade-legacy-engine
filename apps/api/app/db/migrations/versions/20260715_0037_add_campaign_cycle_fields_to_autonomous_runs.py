"""add campaign cycle fields to autonomous cycle runs

Revision ID: 20260715_0037
Revises: 20260714_0036
Create Date: 2026-07-15 00:05:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0037"
down_revision: str | None = "20260714_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("autonomous_cycle_runs", sa.Column("cycle_kind", sa.Text(), nullable=False, server_default=sa.text("'autonomous'")))
    op.add_column("autonomous_cycle_runs", sa.Column("capital_campaign_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("autonomous_cycle_runs", sa.Column("capital_campaign_version", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_autonomous_cycle_runs_cycle_kind",
        "autonomous_cycle_runs",
        "cycle_kind IN ('autonomous','campaign')",
    )
    op.create_index("ix_autonomous_cycle_runs_campaign_created", "autonomous_cycle_runs", ["capital_campaign_id", "started_at"], unique=False)
    op.create_index("ix_autonomous_cycle_runs_kind_created", "autonomous_cycle_runs", ["cycle_kind", "started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_autonomous_cycle_runs_kind_created", table_name="autonomous_cycle_runs")
    op.drop_index("ix_autonomous_cycle_runs_campaign_created", table_name="autonomous_cycle_runs")
    op.drop_constraint("ck_autonomous_cycle_runs_cycle_kind", "autonomous_cycle_runs", type_="check")
    op.drop_column("autonomous_cycle_runs", "capital_campaign_version")
    op.drop_column("autonomous_cycle_runs", "capital_campaign_id")
    op.drop_column("autonomous_cycle_runs", "cycle_kind")