"""add current_regime_trend to strategy_roster_runs

Revision ID: 20260725_0049
Revises: 20260724_0048
Create Date: 2026-07-25 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260725_0049"
down_revision: str | None = "20260724_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("strategy_roster_runs", sa.Column("current_regime_trend", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_strategy_roster_runs_current_regime_trend",
        "strategy_roster_runs",
        "current_regime_trend IS NULL OR current_regime_trend IN ('TRENDING','RANGING')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_strategy_roster_runs_current_regime_trend", "strategy_roster_runs", type_="check")
    op.drop_column("strategy_roster_runs", "current_regime_trend")
