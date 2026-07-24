from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousExecutionClaim(Base):
    __tablename__ = "autonomous_execution_claims"
    __table_args__ = (
        UniqueConstraint("package_id", name="uq_autonomous_execution_claim_package"),
        UniqueConstraint("activation_id", name="uq_autonomous_execution_claim_activation"),
        UniqueConstraint("campaign_id", "campaign_version", name="uq_autonomous_execution_claim_campaign_version"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_aec_campaign_definition", ondelete="RESTRICT",
        ),
        CheckConstraint("side = 'BUY'", name="ck_aec_buy_only"),
        CheckConstraint("attempt_count >= 1", name="ck_aec_attempt_count"),
        CheckConstraint(
            "claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','SAFETY_DISABLED',"
            "'RECONCILIATION_REQUIRED','BUY_RECONCILED','POSITION_OPENED','COMPLETED','BLOCKED',"
            "'FAILED_PRE_PROVIDER','RECOVERY_REQUIRED','CANCELLED')",
            name="ck_aec_status",
        ),
        Index("ix_aec_status_recovery", "claim_status", "recover_after"),
        Index("ix_aec_scope", "campaign_id", "campaign_version", "provider", "environment", "product"),
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
