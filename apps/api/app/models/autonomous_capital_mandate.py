from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousCapitalMandate(Base):
    __tablename__ = "autonomous_capital_mandates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_AUTHORIZATION','AUTHORIZED','ACTIVE','PAUSED','EXIT_ONLY','EXPIRED','REVOKED','KILLED','COMPLETED')",
            name="ck_ac_mandates_status",
        ),
        CheckConstraint(
            "autonomy_level IN ('LEVEL_0','LEVEL_1','LEVEL_2','LEVEL_3')",
            name="ck_ac_mandates_autonomy_level",
        ),
        CheckConstraint(
            "exchange_environment IN ('production','sandbox')",
            name="ck_ac_mandates_exchange_environment",
        ),
        CheckConstraint(
            "approval_mode_default = true",
            name="ck_ac_mandates_human_approval_default",
        ),
        Index("ix_ac_mandates_owner_actor", "owner_actor_id"),
        Index("ix_ac_mandates_status", "status"),
        Index("ix_ac_mandates_autonomy_level", "autonomy_level"),
        Index("ix_ac_mandates_live_profile", "live_trading_profile_id"),
        Index("ix_ac_mandates_campaign", "capital_campaign_id"),
    )

    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    owner_actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    autonomy_level: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'LEVEL_1'"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_environment: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exchange_connections.exchange_connection_id", ondelete="RESTRICT"), nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    paper_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="SET NULL"), nullable=True)
    capital_campaign_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("capital_campaigns.id", ondelete="SET NULL"), nullable=True)
    approval_mode_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
