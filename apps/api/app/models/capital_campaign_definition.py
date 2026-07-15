from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CapitalCampaignDefinition(Base):
    __tablename__ = "capital_campaign_definitions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_ccd_campaign_version"),
        CheckConstraint(
            "status IN ('DRAFT','READY','ACTIVE','PAUSED','CAPITAL_EXHAUSTED','COMPLETED','CANCELED','MANUAL_REVIEW_REQUIRED')",
            name="ck_ccd_status",
        ),
        CheckConstraint(
            "aggression_mode IN ('CONSERVATIVE','BALANCED','AGGRESSIVE','MAXIMUM_GOVERNED')",
            name="ck_ccd_aggression_mode",
        ),
        CheckConstraint("capital_budget > 0", name="ck_ccd_capital_budget_positive"),
        CheckConstraint("remaining_unallocated_capital >= 0", name="ck_ccd_remaining_capital_non_negative"),
        CheckConstraint("maximum_open_positions >= 0", name="ck_ccd_max_open_positions_non_negative"),
        CheckConstraint("maximum_position_size >= 0", name="ck_ccd_max_position_size_non_negative"),
        CheckConstraint("minimum_position_size >= 0", name="ck_ccd_min_position_size_non_negative"),
        CheckConstraint("maximum_total_exposure >= 0", name="ck_ccd_max_total_exposure_non_negative"),
        CheckConstraint("maximum_position_size >= minimum_position_size", name="ck_ccd_position_size_bounds"),
        CheckConstraint("initial_capital >= 0", name="ck_ccd_initial_capital_non_negative"),
        CheckConstraint("allocated_capital >= 0", name="ck_ccd_allocated_capital_non_negative"),
        CheckConstraint("reserved_capital >= 0", name="ck_ccd_reserved_capital_non_negative"),
        CheckConstraint("deployed_capital >= 0", name="ck_ccd_deployed_capital_non_negative"),
        CheckConstraint("fees >= 0", name="ck_ccd_fees_non_negative"),
        CheckConstraint("maximum_drawdown >= 0", name="ck_ccd_max_drawdown_non_negative"),
        Index("ix_ccd_campaign_id", "campaign_id"),
        Index("ix_ccd_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_identity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))

    capital_budget: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    remaining_unallocated_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)

    allowed_asset_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    allowed_venues: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    allowed_instruments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    campaign_modes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    maximum_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_position_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    minimum_position_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    maximum_total_exposure: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    profitability_policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    profitability_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    risk_policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    risk_policy_version: Mapped[str] = mapped_column(Text, nullable=False)

    compounding_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    profit_distribution_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    aggression_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'BALANCED'"))

    initial_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    allocated_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    reserved_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    deployed_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    realized_gross_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    realized_net_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    distributable_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    compounded_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    withdrawn_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    current_campaign_equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    maximum_drawdown: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    available_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))

    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    metadata_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
