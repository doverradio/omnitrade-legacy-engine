from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CapitalCampaignProfitCycle(Base):
    __tablename__ = "capital_campaign_profit_cycles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CALCULATING','BELOW_TARGET','TARGET_REACHED','REVIEW_REQUIRED','APPROVED','COMPOUNDING_RECOMMENDED','WITHDRAWAL_RECOMMENDED','COMPLETED','CANCELLED','ERROR')",
            name="ck_capital_campaign_profit_cycles_status",
        ),
        CheckConstraint("settlement_state IN ('SETTLED','SETTLEMENT_UNKNOWN')", name="ck_capital_campaign_profit_cycles_settlement_state"),
        Index("ix_capital_campaign_profit_cycles_uuid", "cycle_uuid", unique=True),
        Index("ix_capital_campaign_profit_cycles_campaign_id", "capital_campaign_id"),
        Index("ix_capital_campaign_profit_cycles_policy_id", "profit_policy_id"),
        Index("ix_capital_campaign_profit_cycles_status", "status"),
        UniqueConstraint("capital_campaign_id", "cycle_number", name="uq_capital_campaign_profit_cycles_campaign_cycle_number"),
    )

    cycle_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_uuid: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    capital_campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("capital_campaigns.id", ondelete="CASCADE"), nullable=False)
    profit_policy_id: Mapped[int] = mapped_column(Integer, ForeignKey("capital_campaign_profit_policies.policy_id", ondelete="CASCADE"), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)

    opening_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    opening_equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    unrealized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))

    eligible_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    compound_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    withdrawal_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    reserve_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    closing_campaign_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))

    target_reached: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SETTLEMENT_UNKNOWN'"))

    calculation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    calculation_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)

    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    campaign = relationship("CapitalCampaign")
    policy = relationship("CapitalCampaignProfitPolicy")
