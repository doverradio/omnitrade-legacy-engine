from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CryptoOrderPreview(Base):
    __tablename__ = "crypto_order_previews"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "preview_version", name="uq_crypto_order_previews_idempotency_version"),
        Index("idx_crypto_order_previews_exchange_created", "exchange_connection_id", "created_at"),
        Index("idx_crypto_order_previews_status", "status"),
    )

    crypto_order_preview_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    preview_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    refreshed_from_preview_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    quote_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    base_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    requested_amount_currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    readiness_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    validation_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parameter_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parameter_sets.id", ondelete="RESTRICT"), nullable=True)
    strategy_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_average_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_total_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_base_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_quote_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_fee: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_fee_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_slippage: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_commission_total: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    available_balance_before: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    estimated_balance_after: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    risk_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning_messages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    exchange_response_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[str] = mapped_column(Text, nullable=False)
    audit_correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
