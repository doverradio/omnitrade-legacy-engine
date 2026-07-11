from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CapitalCampaignProfitPolicy(Base):
    __tablename__ = "capital_campaign_profit_policies"
    __table_args__ = (
        CheckConstraint(
            "policy_type IN ('HOLD_PROFIT','FULL_COMPOUND','PARTIAL_COMPOUND','WITHDRAW_PROFIT','WITHDRAW_AND_COMPOUND','PROTECTED_PRINCIPAL','MANUAL_REVIEW')",
            name="ck_capital_campaign_profit_policies_type",
        ),
        CheckConstraint("compound_percent >= 0 AND compound_percent <= 100", name="ck_capital_campaign_profit_policies_compound_percent"),
        CheckConstraint("withdraw_percent >= 0 AND withdraw_percent <= 100", name="ck_capital_campaign_profit_policies_withdraw_percent"),
        CheckConstraint("compound_percent + withdraw_percent <= 100", name="ck_capital_campaign_profit_policies_alloc_percent_sum"),
        CheckConstraint("profit_target_amount IS NULL OR profit_target_amount > 0", name="ck_capital_campaign_profit_policies_target_amount_positive"),
        CheckConstraint("profit_target_percent IS NULL OR profit_target_percent > 0", name="ck_capital_campaign_profit_policies_target_percent_positive"),
        CheckConstraint("minimum_realized_profit >= 0", name="ck_capital_campaign_profit_policies_min_realized_profit_nonnegative"),
        CheckConstraint("minimum_cash_reserve >= 0", name="ck_capital_campaign_profit_policies_min_cash_reserve_nonnegative"),
        CheckConstraint("fee_reserve_percent >= 0", name="ck_capital_campaign_profit_policies_fee_reserve_nonnegative"),
        CheckConstraint("tax_reserve_percent >= 0", name="ck_capital_campaign_profit_policies_tax_reserve_nonnegative"),
        CheckConstraint(
            "maximum_campaign_capital IS NULL OR protected_principal_amount IS NULL OR maximum_campaign_capital > protected_principal_amount",
            name="ck_capital_campaign_profit_policies_max_capital_gt_protected_principal",
        ),
        Index("ix_capital_campaign_profit_policies_uuid", "policy_uuid", unique=True),
        Index("ix_capital_campaign_profit_policies_campaign_id", "capital_campaign_id"),
        Index("ix_capital_campaign_profit_policies_active", "is_active"),
    )

    policy_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_uuid: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    capital_campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("capital_campaigns.id", ondelete="CASCADE"), nullable=False)
    policy_type: Mapped[str] = mapped_column(Text, nullable=False)

    profit_target_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    profit_target_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    compound_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0"))
    withdraw_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0"))

    protected_principal_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    minimum_realized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    maximum_campaign_capital: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    minimum_cash_reserve: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))

    fee_reserve_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0"))
    tax_reserve_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0"))
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    require_operator_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    campaign = relationship("CapitalCampaign")
