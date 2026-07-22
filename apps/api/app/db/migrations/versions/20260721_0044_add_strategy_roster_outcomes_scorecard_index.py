"""add scorecard-lookup index to strategy_roster_proposal_outcomes

Revision ID: 20260721_0044
Revises: 20260717_0043
Create Date: 2026-07-21 00:00:00.000000

fetch_strategy_scorecards() (app/services/strategy_outcomes/service.py) filters
strategy_roster_proposal_outcomes on (provider, product_id, interval,
evaluation_state) and orders the result by (strategy_slug, evaluated_at,
outcome_id) -- none of which is covered by this table's existing indexes
(ix_roster_outcomes_strategy_horizon, ix_roster_outcomes_proposal,
ix_roster_outcomes_roster_run). Every call therefore forces a full
sequential scan plus an in-memory/disk sort, both of which get slower as the
table grows with every 15-minute candle across every strategy and horizon.
Confirmed production root cause of a fetch_strategy_scorecards() command
timeout (TimeoutError) after the table had accumulated several days of
continuous scoring. This index covers both the filter and the sort, letting
Postgres satisfy the query with a single ordered index scan.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "20260721_0044"
down_revision: str | None = "20260717_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_roster_outcomes_scorecard_lookup",
        "strategy_roster_proposal_outcomes",
        ["provider", "product_id", "interval", "evaluation_state", "strategy_slug", "evaluated_at", "outcome_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_roster_outcomes_scorecard_lookup", table_name="strategy_roster_proposal_outcomes")
