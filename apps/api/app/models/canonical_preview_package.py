from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CanonicalPreviewPackage(Base):
    __tablename__ = "canonical_preview_packages"
    __table_args__ = (
        UniqueConstraint("package_id", name="uq_cpp_package_id"),
        UniqueConstraint("idempotency_key", name="uq_cpp_idempotency_key"),
        UniqueConstraint("crypto_order_preview_id", name="uq_cpp_preview_id"),
        UniqueConstraint("decision_record_id", name="uq_cpp_decision_id"),
        UniqueConstraint("risk_event_id", name="uq_cpp_risk_event_id"),
        UniqueConstraint("campaign_id", "campaign_version", "package_id", name="uq_cpp_campaign_owner"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_cpp_campaign_definition",
            ondelete="RESTRICT",
        ),
        CheckConstraint("environment IN ('production','sandbox')", name="ck_cpp_environment"),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_cpp_side"),
        CheckConstraint("proposed_order_amount > 0", name="ck_cpp_proposed_positive"),
        CheckConstraint("risk_approved_amount > 0", name="ck_cpp_approved_positive"),
        CheckConstraint("risk_approved_amount <= proposed_order_amount", name="ck_cpp_approved_lte_prop"),
        CheckConstraint("proposed_order_amount <= 5", name="ck_cpp_proposed_cap"),
        CheckConstraint("risk_approved_amount <= 5", name="ck_cpp_approved_cap"),
        CheckConstraint(
            "package_state IN ('CREATED','READY','AUTHORIZED','DRY_RUN_PASSED','ACTIVATED','EXPIRED','INVALIDATED','SUPERSEDED','COMPLETED','FAILED_CLOSED')",
            name="ck_cpp_package_state",
        ),
        Index("ix_cpp_campaign_version", "campaign_id", "campaign_version"),
        Index("ix_cpp_state", "package_state"),
        Index("ix_cpp_preview_expires", "preview_expires_at"),
        Index("ix_cpp_preview", "crypto_order_preview_id"),
        Index("ix_cpp_decision", "decision_record_id"),
        Index("ix_cpp_risk", "risk_event_id"),
        Index("ix_cpp_idempotency", "idempotency_key"),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(nullable=False)
    runtime_campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capital_campaigns.uuid", ondelete="RESTRICT"), nullable=False)
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_order_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    risk_approved_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parameter_sets.id", ondelete="RESTRICT"), nullable=False)
    parameter_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    decision_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("decision_records.decision_id", ondelete="RESTRICT"), nullable=False)
    risk_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("risk_events.id", ondelete="RESTRICT"), nullable=False)
    crypto_order_preview_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crypto_order_previews.crypto_order_preview_id", ondelete="RESTRICT"), nullable=False)
    market_evidence_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    market_evidence_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preview_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    package_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CREATED'"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    approval_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_approval_events.id", ondelete="SET NULL"), nullable=True)
    dry_run_live_crypto_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="SET NULL"), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
