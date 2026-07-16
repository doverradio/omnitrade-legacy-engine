"""add authoritative canonical package and activation tables

Revision ID: 20260715_0040
Revises: 20260715_0039
Create Date: 2026-07-15 21:30:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0040"
down_revision: str | None = "20260715_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_preview_packages",
        sa.Column("package_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("runtime_campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("live_trading_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("proposed_order_amount", sa.Numeric(), nullable=False),
        sa.Column("risk_approved_amount", sa.Numeric(), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version", sa.Text(), nullable=False),
        sa.Column("parameter_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parameter_set_version", sa.Text(), nullable=False),
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crypto_order_preview_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_evidence_identity", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("market_evidence_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("package_state", sa.Text(), nullable=False, server_default=sa.text("'CREATED'")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("approval_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dry_run_live_crypto_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("package_id"),
        sa.UniqueConstraint("package_id", name="uq_cpp_package_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_cpp_idempotency_key"),
        sa.UniqueConstraint("crypto_order_preview_id", name="uq_cpp_preview_id"),
        sa.UniqueConstraint("decision_record_id", name="uq_cpp_decision_id"),
        sa.UniqueConstraint("risk_event_id", name="uq_cpp_risk_event_id"),
        sa.UniqueConstraint("campaign_id", "campaign_version", "package_id", name="uq_cpp_campaign_owner"),
        sa.ForeignKeyConstraint(["runtime_campaign_id"], ["capital_campaigns.uuid"], name="fk_cpp_runtime_campaign", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], name="fk_cpp_paper_account", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_trading_profile_id"], ["live_trading_profiles.id"], name="fk_cpp_live_profile", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], name="fk_cpp_strategy", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parameter_set_id"], ["parameter_sets.id"], name="fk_cpp_parameter_set", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_record_id"], ["decision_records.decision_id"], name="fk_cpp_decision", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], name="fk_cpp_risk_event", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["crypto_order_preview_id"], ["crypto_order_previews.crypto_order_preview_id"], name="fk_cpp_preview", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_event_id"], ["live_approval_events.id"], name="fk_cpp_approval_event", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dry_run_live_crypto_order_id"], ["live_crypto_orders.live_crypto_order_id"], name="fk_cpp_dry_run_order", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["campaign_id", "campaign_version"], ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"], name="fk_cpp_campaign_definition", ondelete="RESTRICT"),
        sa.CheckConstraint("environment IN ('production','sandbox')", name="ck_cpp_environment"),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_cpp_side"),
        sa.CheckConstraint("proposed_order_amount > 0", name="ck_cpp_proposed_positive"),
        sa.CheckConstraint("risk_approved_amount > 0", name="ck_cpp_approved_positive"),
        sa.CheckConstraint("risk_approved_amount <= proposed_order_amount", name="ck_cpp_approved_lte_prop"),
        sa.CheckConstraint("proposed_order_amount <= 5", name="ck_cpp_proposed_cap"),
        sa.CheckConstraint("risk_approved_amount <= 5", name="ck_cpp_approved_cap"),
        sa.CheckConstraint(
            "package_state IN ('CREATED','READY','AUTHORIZED','DRY_RUN_PASSED','ACTIVATED','EXPIRED','INVALIDATED','SUPERSEDED','COMPLETED','FAILED_CLOSED')",
            name="ck_cpp_package_state",
        ),
    )
    op.create_index("ix_cpp_campaign_version", "canonical_preview_packages", ["campaign_id", "campaign_version"], unique=False)
    op.create_index("ix_cpp_state", "canonical_preview_packages", ["package_state"], unique=False)
    op.create_index("ix_cpp_preview_expires", "canonical_preview_packages", ["preview_expires_at"], unique=False)
    op.create_index("ix_cpp_preview", "canonical_preview_packages", ["crypto_order_preview_id"], unique=False)
    op.create_index("ix_cpp_decision", "canonical_preview_packages", ["decision_record_id"], unique=False)
    op.create_index("ix_cpp_risk", "canonical_preview_packages", ["risk_event_id"], unique=False)
    op.create_index("ix_cpp_idempotency", "canonical_preview_packages", ["idempotency_key"], unique=False)

    op.create_table(
        "canonical_proving_activations",
        sa.Column("activation_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dry_run_live_crypto_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_version", sa.Integer(), nullable=False),
        sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("live_trading_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("max_order_amount", sa.Numeric(), nullable=False),
        sa.Column("max_deployed_capital", sa.Numeric(), nullable=False),
        sa.Column("no_leverage", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activation_state", sa.Text(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("activation_id"),
        sa.UniqueConstraint("activation_id", name="uq_cpa_activation_id"),
        sa.UniqueConstraint("package_id", name="uq_cpa_package_id"),
        sa.UniqueConstraint("dry_run_live_crypto_order_id", name="uq_cpa_dry_run_order"),
        sa.ForeignKeyConstraint(["package_id"], ["canonical_preview_packages.package_id"], name="fk_cpa_package", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_event_id"], ["live_approval_events.id"], name="fk_cpa_approval", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dry_run_live_crypto_order_id"], ["live_crypto_orders.live_crypto_order_id"], name="fk_cpa_dry_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_cpa_campaign_definition",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["paper_account_id"], ["paper_accounts.id"], name="fk_cpa_paper", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["live_trading_profile_id"], ["live_trading_profiles.id"], name="fk_cpa_profile", ondelete="RESTRICT"),
        sa.CheckConstraint("environment IN ('production','sandbox')", name="ck_cpa_environment"),
        sa.CheckConstraint("max_order_amount <= 5", name="ck_cpa_max_order_cap"),
        sa.CheckConstraint("max_deployed_capital <= 5", name="ck_cpa_max_deployed_cap"),
        sa.CheckConstraint("max_order_amount > 0", name="ck_cpa_max_order_positive"),
        sa.CheckConstraint("max_deployed_capital > 0", name="ck_cpa_deployed_positive"),
        sa.CheckConstraint("no_leverage = true", name="ck_cpa_no_leverage"),
        sa.CheckConstraint(
            "activation_state IN ('ACTIVE','PAUSED','REVOKED','EXPIRED','INVALIDATED','COMPLETED')",
            name="ck_cpa_state",
        ),
    )
    op.create_index("ix_cpa_state", "canonical_proving_activations", ["activation_state"], unique=False)
    op.create_index("ix_cpa_expires", "canonical_proving_activations", ["expires_at"], unique=False)
    op.create_index("ix_cpa_scope", "canonical_proving_activations", ["paper_account_id", "provider", "environment", "product"], unique=False)
    op.create_index(
        "uq_cpa_active_scope",
        "canonical_proving_activations",
        ["paper_account_id", "provider", "environment", "product"],
        unique=True,
        postgresql_where=sa.text("activation_state = 'ACTIVE'"),
    )

    op.drop_constraint("ck_live_approval_events_checkpoint_type", "live_approval_events", type_="check")
    op.create_check_constraint(
        "ck_live_approval_events_checkpoint_type",
        "live_approval_events",
        "checkpoint_type IN ('first_live_enablement','material_control_change','bounded_proving_entry')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_live_approval_events_checkpoint_type", "live_approval_events", type_="check")
    op.create_check_constraint(
        "ck_live_approval_events_checkpoint_type",
        "live_approval_events",
        "checkpoint_type IN ('first_live_enablement','material_control_change')",
    )

    op.drop_index("uq_cpa_active_scope", table_name="canonical_proving_activations")
    op.drop_index("ix_cpa_scope", table_name="canonical_proving_activations")
    op.drop_index("ix_cpa_expires", table_name="canonical_proving_activations")
    op.drop_index("ix_cpa_state", table_name="canonical_proving_activations")
    op.drop_table("canonical_proving_activations")

    op.drop_index("ix_cpp_idempotency", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_risk", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_decision", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_preview", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_preview_expires", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_state", table_name="canonical_preview_packages")
    op.drop_index("ix_cpp_campaign_version", table_name="canonical_preview_packages")
    op.drop_table("canonical_preview_packages")
