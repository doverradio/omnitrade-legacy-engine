from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RiskEquityBaseline(Base):
    __tablename__ = "risk_equity_baselines"
    __table_args__ = (
        UniqueConstraint("paper_account_id", name="uq_risk_equity_baselines_account"),
        CheckConstraint("start_of_day_equity >= 0", name="ck_risk_eq_base_sod_non_negative"),
        CheckConstraint("high_water_mark_equity >= 0", name="ck_risk_eq_base_hwm_non_negative"),
        CheckConstraint("last_equity >= 0", name="ck_risk_eq_base_last_equity_non_negative"),
        CheckConstraint("last_cash_balance >= 0", name="ck_risk_eq_base_cash_non_negative"),
        CheckConstraint("last_position_value >= 0", name="ck_risk_eq_base_pos_non_negative"),
        CheckConstraint(
            "valuation_state IN ('ready','missing_price_evidence','stale_price_evidence','inconsistent_account_state')",
            name="ck_risk_eq_base_valuation_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id"), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)

    start_of_day_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    start_of_day_source: Mapped[str] = mapped_column(Text, nullable=False)
    start_of_day_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    high_water_mark_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high_water_mark_source: Mapped[str] = mapped_column(Text, nullable=False)
    high_water_mark_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    last_equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    last_cash_balance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    last_position_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    last_price_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valuation_source: Mapped[str] = mapped_column(Text, nullable=False)
    valuation_state: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
