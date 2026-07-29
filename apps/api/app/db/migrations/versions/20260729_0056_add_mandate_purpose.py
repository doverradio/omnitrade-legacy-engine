"""add mandate purpose (PRODUCTION / CONTROLLED_PROOF) and per-purpose active-scope uniqueness

Revision ID: 20260729_0056
Revises: 20260728_0055

Backward compatibility: every existing row backfills to 'PRODUCTION' via the
column default, so ordinary production mandate resolution is unaffected --
this migration only ever narrows an ambiguous "latest active LEVEL_2 mandate
for scope" selection into a deterministic "at most one active mandate per
(scope, autonomy_level, purpose)" one.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0056"
down_revision: str | None = "20260728_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomous_capital_mandates",
        sa.Column("purpose", sa.Text(), server_default=sa.text("'PRODUCTION'"), nullable=False),
    )
    op.create_check_constraint(
        "ck_ac_mandates_purpose",
        "autonomous_capital_mandates",
        "purpose IN ('PRODUCTION','CONTROLLED_PROOF')",
    )
    # Replaces "latest active LEVEL_2 mandate for scope" ORDER BY/LIMIT 1
    # ambiguity with a real database-enforced invariant: at most one ACTIVE
    # mandate per (provider, environment, connection, profile, autonomy
    # level, purpose). Same partial-unique-index pattern as
    # uq_controlled_proof_runs_single_active / uq_aec_active_campaign_scope.
    op.create_index(
        "uq_ac_mandates_active_scope_purpose",
        "autonomous_capital_mandates",
        ["provider", "exchange_environment", "exchange_connection_id", "live_trading_profile_id", "autonomy_level", "purpose"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_ac_mandates_purpose", "autonomous_capital_mandates", ["purpose"])


def downgrade() -> None:
    op.drop_index("ix_ac_mandates_purpose", table_name="autonomous_capital_mandates")
    op.drop_index("uq_ac_mandates_active_scope_purpose", table_name="autonomous_capital_mandates")
    op.drop_constraint("ck_ac_mandates_purpose", "autonomous_capital_mandates", type_="check")
    op.drop_column("autonomous_capital_mandates", "purpose")
