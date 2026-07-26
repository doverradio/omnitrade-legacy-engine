"""add controlled_proof_runs table

Revision ID: 20260726_0051
Revises: 20260725_0050
Create Date: 2026-07-26 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0051"
down_revision: str | None = "20260725_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = (
    "REQUESTED", "CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN",
    "WAITING_FOR_PROFITABLE_EXIT", "EXITED", "RECONCILED", "PROFIT_CONFIRMED",
    "BLOCKED", "EXPIRED", "CANCELLED", "FAILED",
)
_ACTIVE_STATES = (
    "REQUESTED", "CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN",
    "WAITING_FOR_PROFITABLE_EXIT",
)


def upgrade() -> None:
    op.create_table(
        "controlled_proof_runs",
        sa.Column("proof_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'REQUESTED'")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("max_notional_usd", sa.Numeric(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by_cycle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mandate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mandate_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sell_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("buy_live_crypto_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sell_live_crypto_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position_id", sa.Text(), nullable=True),
        sa.Column("net_pnl_usd", sa.Numeric(), nullable=True),
        sa.Column(
            "terminal_verdict", sa.Text(), nullable=True,
        ),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Text(), nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("proof_id"),
        sa.CheckConstraint(f"status IN ({', '.join(repr(s) for s in _STATES)})", name="ck_controlled_proof_runs_status"),
        sa.CheckConstraint(
            "terminal_verdict IS NULL OR terminal_verdict IN "
            "('LIFECYCLE_PROVEN_PROFIT','LIFECYCLE_PROVEN_LOSS','LIFECYCLE_PROVEN_FLAT','BLOCKED','FAILED')",
            name="ck_controlled_proof_runs_terminal_verdict",
        ),
    )
    op.create_unique_constraint(
        "uq_controlled_proof_runs_idempotency_key", "controlled_proof_runs", ["idempotency_key"]
    )
    # At most one controlled proof may be in an active (non-terminal) state at
    # any time -- enforced at the database level, not just application code,
    # via a unique index on a constant expression filtered to active states.
    # This is the same class of guarantee the rest of this codebase relies on
    # for exactly-once/at-most-one invariants (e.g. uq_asset_commissioning_runs_idempotency_key)
    # rather than trusting a read-then-write race in Python.
    op.create_index(
        "uq_controlled_proof_runs_single_active",
        "controlled_proof_runs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(f"status IN ({', '.join(repr(s) for s in _ACTIVE_STATES)})"),
    )
    op.create_index(
        "ix_controlled_proof_runs_campaign_product",
        "controlled_proof_runs",
        ["campaign_id", "campaign_version", "product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_controlled_proof_runs_campaign_product", table_name="controlled_proof_runs")
    op.drop_index("uq_controlled_proof_runs_single_active", table_name="controlled_proof_runs")
    op.drop_constraint("uq_controlled_proof_runs_idempotency_key", "controlled_proof_runs", type_="unique")
    op.drop_table("controlled_proof_runs")
