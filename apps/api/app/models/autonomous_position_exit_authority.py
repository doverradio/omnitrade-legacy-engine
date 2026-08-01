from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousPositionExitAuthority(Base):
    __tablename__ = "autonomous_position_exit_authorities"
    __table_args__ = (
        UniqueConstraint("custody_id", "evaluation_integrity_hash", name="uq_apea_custody_evaluation"),
        CheckConstraint("authority_state IN ('UNARMED','ARMED','RESERVED','CONSUMED','REVOKED','EXPIRED','BLOCKED')", name="ck_apea_state"),
        CheckConstraint("side = 'SELL'", name="ck_apea_sell_only"),
        CheckConstraint("exposure_effect = 'REDUCE_ONLY'", name="ck_apea_reduce_only"),
        CheckConstraint("authoritative_quantity_at_issuance > 0", name="ck_apea_quantity_positive"),
        CheckConstraint("maximum_sell_quantity > 0 AND maximum_sell_quantity <= authoritative_quantity_at_issuance", name="ck_apea_max_quantity"),
        CheckConstraint("buy_forbidden = true AND increased_exposure_forbidden = true", name="ck_apea_exposure_forbidden"),
        CheckConstraint("classification IN ('PROOF_ELIGIBLE_AUTONOMOUS','NONQUALIFYING_PROTECTIVE_EXIT')", name="ck_apea_classification"),
        Index("ix_apea_custody_state", "custody_id", "authority_state"),
        Index(
            "uq_apea_active_custody", "custody_id", unique=True,
            postgresql_where=text("authority_state IN ('ARMED','RESERVED')"),
            sqlite_where=text("authority_state IN ('ARMED','RESERVED')"),
        ),
        Index(
            "uq_apea_active_position_scope", "live_trading_profile_id", "product", unique=True,
            postgresql_where=text("authority_state IN ('ARMED','RESERVED')"),
            sqlite_where=text("authority_state IN ('ARMED','RESERVED')"),
        ),
    )

    authority_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    authority_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    authority_state: Mapped[str] = mapped_column(Text, nullable=False)
    custody_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_custodies.custody_id", ondelete="RESTRICT"), nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_trading_profiles.id", ondelete="RESTRICT"), nullable=False)
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exchange_connections.exchange_connection_id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    originating_buy_claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"), nullable=False)
    originating_reconciliation_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_reconciliation_events.id", ondelete="RESTRICT"), nullable=False)
    provenance_classification: Mapped[str] = mapped_column(Text, nullable=False)
    proof_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluation_integrity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    authoritative_quantity_at_issuance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    maximum_sell_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SELL'"))
    exposure_effect: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'REDUCE_ONLY'"))
    buy_forbidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    increased_exposure_forbidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    policy_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_records.decision_id", ondelete="RESTRICT"), unique=True
    )
    reserved_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"), unique=True
    )
    reserved_activation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_proving_activations.activation_id", ondelete="RESTRICT"), unique=True
    )
    reserved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"), unique=True
    )
    reserved_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"), unique=True
    )
    last_construction_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_construction_failure_code: Mapped[str | None] = mapped_column(Text)
    last_construction_exception_class: Mapped[str | None] = mapped_column(Text)
    last_construction_failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    last_activation_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activation_failure_code: Mapped[str | None] = mapped_column(Text)
    last_activation_exception_class: Mapped[str | None] = mapped_column(Text)
    last_activation_failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    last_order_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_order_failure_code: Mapped[str | None] = mapped_column(Text)
    last_order_exception_class: Mapped[str | None] = mapped_column(Text)
    last_order_failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
