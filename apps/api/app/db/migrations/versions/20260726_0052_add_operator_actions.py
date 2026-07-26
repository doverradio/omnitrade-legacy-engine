"""add operator_actions table

Revision ID: 20260726_0052
Revises: 20260726_0051
Create Date: 2026-07-26 01:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0052"
down_revision: str | None = "20260726_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION_TYPES = ("RUN_CONTROLLED_PROOF",)
_STATUSES = (
    "REQUESTED", "ACCEPTED", "IN_PROGRESS", "SUCCEEDED",
    "BLOCKED", "FAILED", "CANCELLED", "EXPIRED",
)


def upgrade() -> None:
    op.create_table(
        "operator_actions",
        sa.Column("action_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'REQUESTED'")),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("linked_resource_type", sa.Text(), nullable=True),
        sa.Column("linked_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("action_id"),
        sa.CheckConstraint(f"action_type IN ({', '.join(repr(v) for v in _ACTION_TYPES)})", name="ck_operator_actions_action_type"),
        sa.CheckConstraint(f"status IN ({', '.join(repr(v) for v in _STATUSES)})", name="ck_operator_actions_status"),
    )
    op.create_unique_constraint(
        "uq_operator_actions_idempotency_key", "operator_actions", ["idempotency_key"]
    )
    op.create_index("ix_operator_actions_action_type", "operator_actions", ["action_type"])
    op.create_index("ix_operator_actions_status", "operator_actions", ["status"])
    op.create_index("ix_operator_actions_requested_at", "operator_actions", ["requested_at"])


def downgrade() -> None:
    op.drop_index("ix_operator_actions_requested_at", table_name="operator_actions")
    op.drop_index("ix_operator_actions_status", table_name="operator_actions")
    op.drop_index("ix_operator_actions_action_type", table_name="operator_actions")
    op.drop_constraint("uq_operator_actions_idempotency_key", "operator_actions", type_="unique")
    op.drop_table("operator_actions")
