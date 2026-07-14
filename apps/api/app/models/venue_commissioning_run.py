from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VenueCommissioningRun(Base):
    __tablename__ = "venue_commissioning_runs"
    __table_args__ = (
        CheckConstraint("execution_purpose = 'VENUE_COMMISSIONING'", name="ck_vcr_exec_purpose"),
        CheckConstraint("commissioning_type = 'KRAKEN_FIRST_FLIGHT'", name="ck_vcr_comm_type"),
        CheckConstraint("provider = 'kraken_spot'", name="ck_vcr_provider"),
        CheckConstraint("environment = 'production'", name="ck_vcr_environment"),
        CheckConstraint("product_id = 'BTC-USD'", name="ck_vcr_product"),
        CheckConstraint("max_quote_notional = 5.00", name="ck_vcr_max_quote"),
        CheckConstraint("max_buys = 1", name="ck_vcr_max_buys"),
        CheckConstraint("max_sells = 1", name="ck_vcr_max_sells"),
        CheckConstraint("strategy_id IS NULL", name="ck_vcr_no_strategy_id"),
        CheckConstraint("strategy_signal IS NULL", name="ck_vcr_no_strategy_signal"),
        CheckConstraint("expected_profit = 'NOT_CLAIMED'", name="ck_vcr_no_profit_claim"),
        CheckConstraint("buy_requested_quote_usd > 0", name="ck_vcr_buy_quote_positive"),
        CheckConstraint("buy_requested_quote_usd <= 5.00", name="ck_vcr_buy_quote_cap"),
        CheckConstraint(
            "status IN ('PREPARED','ACTIVE','BUY_SUBMISSION_PENDING','BUY_RECONCILIATION_REQUIRED','BUY_FILLED','HOLDING','SELL_DUE','SELL_SUBMISSION_PENDING','SELL_RECONCILIATION_REQUIRED','SELL_FILLED','RECONCILED','COMPLETED','ABORTED','MANUAL_REVIEW_REQUIRED','REVOKED','EXPIRED')",
            name="ck_vcr_status",
        ),
        Index(
            "uq_vcr_active_scope",
            "provider",
            "environment",
            "product_id",
            unique=True,
            postgresql_where=text(
                "status IN ('PREPARED','ACTIVE','BUY_SUBMISSION_PENDING','BUY_RECONCILIATION_REQUIRED','BUY_FILLED','HOLDING','SELL_DUE','SELL_SUBMISSION_PENDING','SELL_RECONCILIATION_REQUIRED','SELL_FILLED','RECONCILED')"
            ),
        ),
        Index("ix_vcr_status_created", "status", "created_at"),
        Index("ix_vcr_buy_client_order_id", "buy_client_order_id", unique=True),
        Index("ix_vcr_sell_client_order_id", "sell_client_order_id", unique=True),
    )

    commissioning_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    execution_purpose: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'VENUE_COMMISSIONING'"))
    commissioning_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'KRAKEN_FIRST_FLIGHT'"))
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'kraken_spot'"))
    environment: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'production'"))
    product_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'BTC-USD'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PREPARED'"))

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    strategy_signal: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_profit: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'NOT_CLAIMED'"))

    max_quote_notional: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("5.00"))
    max_buys: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    max_sells: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    hold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))

    buy_requested_quote_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("5.00"))
    buy_client_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    buy_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    buy_filled_quote_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    buy_filled_base_btc: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    buy_avg_price_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    buy_fee_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    hold_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hold_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sell_client_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sell_provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sell_idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sell_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sell_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sell_requested_base_btc: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sell_filled_base_btc: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sell_filled_quote_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sell_avg_price_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sell_fee_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    gross_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    total_fees_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    net_realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    dust_base_btc: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    ledger_matches_kraken: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    duplicate_orders_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    manual_intervention_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    activated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    state_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
