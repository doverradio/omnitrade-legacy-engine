"""add LIMIT order columns to live_crypto_orders and the autonomous limit-entry attempt state machine

Revision ID: 20260803_0065
Revises: 20260801_0064
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0065"
down_revision: str | None = "20260801_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGE_VALUES = (
    "PROPOSED", "READY", "REJECTED", "SUBMITTED", "OPEN", "PARTIALLY_FILLED",
    "FILLED", "EXPIRED", "CANCEL_REQUESTED", "CANCELLED", "REPLACED",
    "RECONCILIATION_REQUIRED",
)
_TERMINAL_STAGE_VALUES = ("REJECTED", "FILLED", "EXPIRED", "CANCELLED", "REPLACED")


def upgrade() -> None:
    # live_crypto_orders: order_type has been a free, unconstrained Text
    # column since 20260709_0021 (only ever set to "MARKET" until now).
    # limit_price/time_in_force are nullable and only ever populated for
    # order_type='LIMIT' -- MARKET rows are unaffected (both stay NULL,
    # matching every existing row).
    op.add_column("live_crypto_orders", sa.Column("limit_price", sa.Numeric(), nullable=True))
    op.add_column("live_crypto_orders", sa.Column("time_in_force", sa.Text(), nullable=True))
    # UPPER(order_type): order_type has been unconstrained free text since
    # 20260709_0021, and existing call sites/fixtures use both "MARKET" and
    # lowercase "market" -- match case-insensitively so this constraint
    # cannot reject a row that was always valid before LIMIT support existed.
    op.create_check_constraint(
        "ck_lco_limit_price_matches_order_type",
        "live_crypto_orders",
        "(UPPER(order_type) = 'MARKET' AND limit_price IS NULL) OR (UPPER(order_type) = 'LIMIT' AND limit_price > 0)",
    )

    op.create_table(
        "autonomous_limit_entry_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'kraken_spot'")),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False, server_default=sa.text("'BUY'")),
        sa.Column("stage", sa.Text(), nullable=False, server_default=sa.text("'PROPOSED'")),
        sa.Column("preferred_limit_price", sa.Numeric(), nullable=False),
        sa.Column("maximum_profitable_entry_price", sa.Numeric(), nullable=False),
        sa.Column("invalidation_price", sa.Numeric(), nullable=True),
        sa.Column("requested_base_quantity", sa.Numeric(), nullable=False),
        sa.Column("filled_base_quantity", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("approved_notional", sa.Numeric(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("live_crypto_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replaces_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replacement_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_replacement_count", sa.Integer(), nullable=False),
        sa.Column("min_repricing_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_repriced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("evidence_provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_alea_campaign_definition", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["live_crypto_order_id"], ["live_crypto_orders.live_crypto_order_id"], name="fk_alea_live_crypto_order", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["replaces_attempt_id"], ["autonomous_limit_entry_attempts.attempt_id"], name="fk_alea_replaces_attempt", ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_alea_idempotency_key"),
        sa.CheckConstraint("side = 'BUY'", name="ck_alea_side_buy_only"),
        sa.CheckConstraint(f"stage IN ({', '.join(repr(v) for v in _STAGE_VALUES)})", name="ck_alea_stage"),
        sa.CheckConstraint("preferred_limit_price > 0", name="ck_alea_preferred_limit_price_positive"),
        sa.CheckConstraint("maximum_profitable_entry_price > 0", name="ck_alea_max_profitable_entry_price_positive"),
        sa.CheckConstraint("preferred_limit_price <= maximum_profitable_entry_price", name="ck_alea_never_chase_above_max"),
        sa.CheckConstraint("requested_base_quantity > 0", name="ck_alea_requested_base_quantity_positive"),
        sa.CheckConstraint("filled_base_quantity >= 0 AND filled_base_quantity <= requested_base_quantity", name="ck_alea_filled_within_requested"),
        sa.CheckConstraint("replacement_count >= 0 AND replacement_count <= max_replacement_count", name="ck_alea_replacement_bounded"),
        sa.CheckConstraint("retry_count >= 0", name="ck_alea_retry_count_non_negative"),
    )
    op.create_index("ix_alea_stage_next_attempt", "autonomous_limit_entry_attempts", ["stage", "next_attempt_at"])
    op.create_index("ix_alea_campaign_instrument", "autonomous_limit_entry_attempts", ["campaign_id", "instrument"])
    # At most one non-terminal (still-active) attempt per campaign+instrument
    # at a time -- a replacement creates a NEW row (chained via
    # replaces_attempt_id) only after the prior one has reached a terminal
    # stage, so this never blocks a legitimate replacement, only a genuine
    # duplicate proposal for the same still-active scope.
    op.create_index(
        "uq_alea_active_campaign_instrument_scope",
        "autonomous_limit_entry_attempts",
        ["campaign_id", "instrument"],
        unique=True,
        postgresql_where=sa.text(f"stage NOT IN ({', '.join(repr(v) for v in _TERMINAL_STAGE_VALUES)})"),
    )


def downgrade() -> None:
    op.drop_index("uq_alea_active_campaign_instrument_scope", table_name="autonomous_limit_entry_attempts")
    op.drop_index("ix_alea_campaign_instrument", table_name="autonomous_limit_entry_attempts")
    op.drop_index("ix_alea_stage_next_attempt", table_name="autonomous_limit_entry_attempts")
    op.drop_table("autonomous_limit_entry_attempts")
    op.drop_constraint("ck_lco_limit_price_matches_order_type", "live_crypto_orders", type_="check")
    op.drop_column("live_crypto_orders", "time_in_force")
    op.drop_column("live_crypto_orders", "limit_price")
