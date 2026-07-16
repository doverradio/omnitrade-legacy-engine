from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CanonicalProvingActivation(Base):
    __tablename__ = "canonical_proving_activations"
    __table_args__ = (
        UniqueConstraint("activation_id", name="uq_cpa_activation_id"),
        UniqueConstraint("package_id", name="uq_cpa_package_id"),
        UniqueConstraint("dry_run_live_crypto_order_id", name="uq_cpa_dry_run_order"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_cpa_campaign_definition",
            ondelete="RESTRICT",
        ),
        CheckConstraint("environment IN ('production','sandbox')", name="ck_cpa_environment"),
        CheckConstraint("max_order_amount <= 5", name="ck_cpa_max_order_cap"),
        CheckConstraint("max_deployed_capital <= 5", name="ck_cpa_max_deployed_cap"),
        CheckConstraint("max_order_amount > 0", name="ck_cpa_max_order_positive"),
        CheckConstraint("max_deployed_capital > 0", name="ck_cpa_deployed_positive"),
        CheckConstraint("no_leverage = true", name="ck_cpa_no_leverage"),
        CheckConstraint(
            "activation_state IN ('ACTIVE','PAUSED','REVOKED','EXPIRED','INVALIDATED','COMPLETED')",
            name="ck_cpa_state",
        ),
        Index("ix_cpa_state", "activation_state"),
        Index("ix_cpa_expires", "expires_at"),
        Index("ix_cpa_scope", "paper_account_id", "provider", "environment", "product"),
    )

    activation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"), nullable=False)
    approval_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_approval_events.id", ondelete="RESTRICT"), nullable=False)
    dry_run_live_crypto_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capital_campaign_definitions.campaign_id", ondelete="RESTRICT"), nullable=False)
    campaign_version: Mapped[int] = mapped_column(nullable=False)
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    max_order_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_deployed_capital: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    no_leverage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activation_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
