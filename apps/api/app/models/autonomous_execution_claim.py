from __future__ import annotations

import uuid
from datetime import datetime

from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousExecutionClaim(Base):
    __tablename__ = "autonomous_execution_claims"
    __table_args__ = (
        UniqueConstraint("package_id", name="uq_autonomous_execution_claim_package"),
        UniqueConstraint("activation_id", name="uq_autonomous_execution_claim_activation"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_aec_campaign_definition", ondelete="RESTRICT",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_aec_side"),
        CheckConstraint("exposure_effect IS NULL OR exposure_effect = 'REDUCE_ONLY'", name="ck_aec_exposure_effect"),
        CheckConstraint(
            "custody_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY' "
            "AND claimed_base_quantity > 0 AND maximum_authorized_base_quantity > 0 "
            "AND claimed_base_quantity <= maximum_authorized_base_quantity "
            "AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0 "
            "AND exit_authority_id IS NOT NULL AND evaluation_integrity_hash IS NOT NULL "
            "AND originating_buy_claim_id IS NOT NULL AND originating_reconciliation_event_id IS NOT NULL)",
            name="ck_aec_reduce_only_custody_claim",
        ),
        CheckConstraint(
            "custody_id IS NULL OR ((proof_eligible = true AND disqualification_reason IS NULL) OR "
            "(proof_eligible = false AND disqualification_reason IS NOT NULL))",
            name="ck_aec_proof_classification",
        ),
        CheckConstraint("attempt_count >= 1", name="ck_aec_attempt_count"),
        CheckConstraint(
            "claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','SAFETY_DISABLED',"
            "'RECONCILIATION_REQUIRED','BUY_RECONCILED','POSITION_OPENED','COMPLETED','BLOCKED',"
            "'FAILED_PRE_PROVIDER','RECOVERY_REQUIRED','CANCELLED')",
            name="ck_aec_status",
        ),
        Index("ix_aec_status_recovery", "claim_status", "recover_after"),
        Index("ix_aec_scope", "campaign_id", "campaign_version", "provider", "environment", "product"),
        Index(
            "uq_aec_active_sell_custody_scope", "profile_id", "product", unique=True,
            postgresql_where=text("custody_id IS NOT NULL AND claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED','RECOVERY_REQUIRED')"),
            sqlite_where=text("custody_id IS NOT NULL AND claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED','RECOVERY_REQUIRED')"),
        ),
        # Replaces the original uq_autonomous_execution_claim_campaign_version
        # (a plain, table-wide UNIQUE(campaign_id, campaign_version) added by
        # migration 20260724_0048 for a since-superseded "one shot per
        # campaign version, ever" model). That constraint made it impossible
        # for any second Controlled Proof sharing the same pinned campaign
        # version to ever claim again, even after the first claim's package
        # reached a fully resolved, provider-never-called terminal state --
        # the confirmed production root cause of claim_concurrency_conflict.
        # The correct invariant is narrower: at most one claim whose
        # provider-submission outcome is not yet fully resolved may exist
        # per (campaign_id, campaign_version) at a time -- see
        # _CLAIM_SCOPE_NONTERMINAL_STATES in autonomous_execution_claims.py,
        # which this WHERE clause must stay in sync with. Same partial-index
        # pattern as ControlledProofRun.uq_controlled_proof_runs_single_active.
        # BLOCKED is deliberately excluded from this WHERE clause (unlike
        # RECOVERY_REQUIRED): its name describes a permanent, non-recoverable
        # pre-provider stop -- the same shape as FAILED_PRE_PROVIDER/
        # SAFETY_DISABLED -- not an in-progress state, so it must not reserve
        # the campaign scope forever. See _CLAIM_SCOPE_NONTERMINAL_STATES /
        # _CLAIM_SCOPE_RELEASED_STATES in autonomous_execution_claims.py.
        Index(
            "uq_aec_active_campaign_scope",
            "campaign_id", "campaign_version",
            unique=True,
            postgresql_where=text(
                "claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED',"
                "'RECOVERY_REQUIRED')"
            ),
            sqlite_where=text(
                "claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED',"
                "'RECOVERY_REQUIRED')"
            ),
        ),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"), nullable=False)
    activation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_proving_activations.activation_id", ondelete="RESTRICT"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandates.mandate_id", ondelete="RESTRICT"), nullable=False)
    mandate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandate_versions.mandate_version_id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exchange_connections.exchange_connection_id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    claim_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    custody_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_custodies.custody_id", ondelete="RESTRICT"), unique=True)
    evaluation_integrity_hash: Mapped[str | None] = mapped_column(Text)
    exit_authority_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_exit_authorities.authority_id", ondelete="RESTRICT"), unique=True)
    exit_authority_version: Mapped[int | None] = mapped_column(Integer)
    originating_buy_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"))
    originating_reconciliation_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_reconciliation_events.id", ondelete="RESTRICT"))
    exposure_effect: Mapped[str | None] = mapped_column(Text)
    claimed_base_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    maximum_authorized_base_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_quote_proceeds: Mapped[Decimal | None] = mapped_column(Numeric)
    capital_deployment_amount: Mapped[Decimal | None] = mapped_column(Numeric)
    preview_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crypto_order_previews.crypto_order_preview_id", ondelete="RESTRICT"))
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("risk_events.id", ondelete="RESTRICT"))
    audit_correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proof_eligible: Mapped[bool | None] = mapped_column(Boolean)
    disqualification_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authority_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_submission_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    claim_status: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_owner: Mapped[str] = mapped_column(Text, nullable=False)
    recover_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="SET NULL"), nullable=True)
    reconciliation_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
