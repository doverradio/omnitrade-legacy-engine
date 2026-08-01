from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousPositionCustody(Base):
    """Durable ordinary-production obligation to supervise owned inventory.

    Quantities cached here are observations only. LiveAccountingRecord remains
    the authority and is re-aggregated whenever custody is inspected. Because
    accounting ownership is aggregate by profile and product rather than lot,
    the unique nonterminal scope deliberately does not include campaign IDs.
    """

    __tablename__ = "autonomous_position_custodies"
    __table_args__ = (
        UniqueConstraint("buy_claim_id", name="uq_apc_buy_claim"),
        UniqueConstraint("buy_package_id", name="uq_apc_buy_package"),
        UniqueConstraint("buy_live_order_id", name="uq_apc_buy_order"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_apc_campaign_definition", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','CLOSED','RECOVERED','BLOCKED')",
            name="ck_apc_state",
        ),
        CheckConstraint("autonomous_origin = true", name="ck_apc_autonomous_origin"),
        CheckConstraint("original_acquired_quantity > 0", name="ck_apc_original_quantity_positive"),
        CheckConstraint("observed_remaining_quantity >= 0", name="ck_apc_remaining_quantity_nonnegative"),
        CheckConstraint(
            "continuing_exit_authority_state IN ('UNARMED','PENDING','ARMED','RESERVED','CONSUMED','EXPIRED','REVOKED','BLOCKED')",
            name="ck_apc_continuing_authority",
        ),
        CheckConstraint(
            "(proof_eligible = true AND disqualification_reason IS NULL AND disqualified_at IS NULL) "
            "OR (proof_eligible = false AND disqualification_reason IS NOT NULL AND disqualified_at IS NOT NULL)",
            name="ck_apc_proof_disqualification",
        ),
        Index("ix_apc_state_next_evaluation", "custody_state", "next_exit_evaluation_at"),
        Index("ix_apc_scope", "live_trading_profile_id", "provider", "environment", "product"),
        Index(
            "uq_apc_nonterminal_position_scope",
            "live_trading_profile_id", "product",
            unique=True,
            postgresql_where=text("custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','BLOCKED')"),
            sqlite_where=text("custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','BLOCKED')"),
        ),
    )

    custody_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    custody_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    originating_autonomous_cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_cycle_runs.cycle_id", ondelete="RESTRICT"), nullable=False)
    originating_campaign_cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_cycle_runs.cycle_id", ondelete="RESTRICT"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(nullable=False)
    runtime_campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capital_campaigns.uuid", ondelete="RESTRICT"), nullable=False)
    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandates.mandate_id", ondelete="RESTRICT"), nullable=False)
    mandate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandate_versions.mandate_version_id", ondelete="RESTRICT"), nullable=False)
    decision_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("decision_records.decision_id", ondelete="RESTRICT"), nullable=False)
    buy_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"), nullable=False)
    buy_activation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_proving_activations.activation_id", ondelete="RESTRICT"), nullable=False)
    buy_claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"), nullable=False)
    buy_live_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"), nullable=False)
    buy_reconciliation_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_reconciliation_events.id", ondelete="RESTRICT"), nullable=False)
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exchange_connections.exchange_connection_id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    original_acquired_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    observed_remaining_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quantity_authority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'live_accounting_records'"))
    autonomous_origin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    provenance_classification: Mapped[str] = mapped_column(Text, nullable=False)
    proof_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    disqualification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    disqualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_exit_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_exit_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_sell_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("decision_records.decision_id", ondelete="RESTRICT"), nullable=True)
    active_sell_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"), nullable=True)
    active_sell_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"), nullable=True)
    active_sell_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"), nullable=True)
    continuing_exit_authority_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UNARMED'"))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
