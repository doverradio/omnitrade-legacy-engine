from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveCryptoOrder(Base):
    __tablename__ = "live_crypto_orders"
    __table_args__ = (
        UniqueConstraint("crypto_order_preview_id", name="uq_live_crypto_orders_preview_id"),
        UniqueConstraint("client_order_id", name="uq_live_crypto_orders_client_order_id"),
        UniqueConstraint("provider_order_id", name="uq_live_crypto_orders_provider_order_id"),
        Index("idx_live_crypto_orders_exchange_created", "exchange_connection_id", "created_at"),
        Index("idx_live_crypto_orders_status", "status"),
        CheckConstraint("exposure_effect IS NULL OR exposure_effect = 'REDUCE_ONLY'", name="ck_lco_exposure_effect"),
        CheckConstraint(
            "execution_claim_id IS NULL OR (side = 'SELL' AND exposure_effect = 'REDUCE_ONLY' "
            "AND requested_base_quantity > 0 AND normalized_base_quantity > 0 "
            "AND normalized_base_quantity <= requested_base_quantity "
            "AND normalized_base_quantity <= maximum_authorized_base_quantity "
            "AND expected_quote_proceeds > 0 AND capital_deployment_amount = 0 "
            "AND ((proof_eligible = true AND disqualification_reason IS NULL) OR "
            "(proof_eligible = false AND disqualification_reason IS NOT NULL)) "
            "AND ((status = 'PENDING_CONFIRMATION' AND provider_order_id IS NULL "
            "AND submitted_at IS NULL AND provider_submission_connected = false) "
            "OR (status IN ('SUBMISSION_PENDING','REJECTED') AND provider_order_id IS NULL "
            "AND submitted_at IS NOT NULL AND provider_submission_connected = true) "
            "OR (status IN ('RECONCILIATION_REQUIRED','UNKNOWN') "
            "AND submitted_at IS NOT NULL AND provider_submission_connected = true) "
            "OR (status IN ('ACKNOWLEDGED','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELLED') "
            "AND provider_order_id IS NOT NULL AND submitted_at IS NOT NULL "
            "AND provider_submission_connected = true)))",
            name="ck_lco_reduce_only_lifecycle",
        ),
        Index(
            "uq_lco_active_sell_custody_scope", "custody_id", unique=True,
            postgresql_where=text("custody_id IS NOT NULL AND status IN ('PENDING_CONFIRMATION','VALIDATING','SUBMISSION_PENDING','ACKNOWLEDGED','SUBMITTED','PARTIALLY_FILLED','RECONCILIATION_REQUIRED','UNKNOWN')"),
            sqlite_where=text("custody_id IS NOT NULL AND status IN ('PENDING_CONFIRMATION','VALIDATING','SUBMISSION_PENDING','ACKNOWLEDGED','SUBMITTED','PARTIALLY_FILLED','RECONCILIATION_REQUIRED','UNKNOWN')"),
        ),
    )

    live_crypto_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    crypto_order_preview_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    requested_quote_size: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    validation_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_provider_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operator_confirmation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    execution_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"), unique=True)
    claim_version: Mapped[int | None] = mapped_column(Integer)
    custody_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_custodies.custody_id", ondelete="RESTRICT"))
    evaluation_integrity_hash: Mapped[str | None] = mapped_column(Text)
    exit_authority_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_exit_authorities.authority_id", ondelete="RESTRICT"))
    exit_authority_version: Mapped[int | None] = mapped_column(Integer)
    activation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_proving_activations.activation_id", ondelete="RESTRICT"))
    originating_buy_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"))
    originating_reconciliation_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_reconciliation_events.id", ondelete="RESTRICT"))
    exposure_effect: Mapped[str | None] = mapped_column(Text)
    requested_base_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    normalized_base_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    maximum_authorized_base_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    expected_quote_proceeds: Mapped[Decimal | None] = mapped_column(Numeric)
    capital_deployment_amount: Mapped[Decimal | None] = mapped_column(Numeric)
    proof_eligible: Mapped[bool | None] = mapped_column(Boolean)
    disqualification_reason: Mapped[str | None] = mapped_column(Text)
    construction_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_submission_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
