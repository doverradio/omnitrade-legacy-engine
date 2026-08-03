from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STAGE_PROPOSED = "PROPOSED"
STAGE_READY = "READY"
STAGE_REJECTED = "REJECTED"
STAGE_SUBMITTED = "SUBMITTED"
STAGE_OPEN = "OPEN"
STAGE_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STAGE_FILLED = "FILLED"
STAGE_EXPIRED = "EXPIRED"
STAGE_CANCEL_REQUESTED = "CANCEL_REQUESTED"
STAGE_CANCELLED = "CANCELLED"
STAGE_REPLACED = "REPLACED"
STAGE_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

TERMINAL_STAGES = frozenset({STAGE_REJECTED, STAGE_FILLED, STAGE_EXPIRED, STAGE_CANCELLED, STAGE_REPLACED})


class AutonomousLimitEntryAttempt(Base):
    """Durable, restart-safe state machine for one bounded BUY_LIMIT
    entry-intelligence proposal (docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md
    Phases 6-9). A row is the AUTHORITATIVE candidate/package representation
    for a limit entry -- it exists BEFORE any Risk evaluation or provider
    submission, and its stage is the single source of truth the supervisor
    worker (autonomous_limit_entry_worker.py) advances one step at a time.

    A replacement (Phase 9) never mutates a row in place -- it creates a NEW
    row with `replaces_attempt_id` pointing at the prior one, only after the
    prior row's provider cancellation is confirmed (stage CANCELLED). This
    keeps every priced quote and every provider interaction immutably
    auditable, and lets the partial unique index below allow at most one
    ACTIVE (non-terminal) attempt per campaign+instrument without blocking
    that replacement.
    """

    __tablename__ = "autonomous_limit_entry_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_alea_campaign_definition", ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_alea_idempotency_key"),
        CheckConstraint("side = 'BUY'", name="ck_alea_side_buy_only"),
        CheckConstraint(
            "stage IN ('PROPOSED','READY','REJECTED','SUBMITTED','OPEN','PARTIALLY_FILLED',"
            "'FILLED','EXPIRED','CANCEL_REQUESTED','CANCELLED','REPLACED','RECONCILIATION_REQUIRED')",
            name="ck_alea_stage",
        ),
        CheckConstraint("preferred_limit_price > 0", name="ck_alea_preferred_limit_price_positive"),
        CheckConstraint("maximum_profitable_entry_price > 0", name="ck_alea_max_profitable_entry_price_positive"),
        CheckConstraint("preferred_limit_price <= maximum_profitable_entry_price", name="ck_alea_never_chase_above_max"),
        CheckConstraint("requested_base_quantity > 0", name="ck_alea_requested_base_quantity_positive"),
        CheckConstraint("filled_base_quantity >= 0 AND filled_base_quantity <= requested_base_quantity", name="ck_alea_filled_within_requested"),
        CheckConstraint("replacement_count >= 0 AND replacement_count <= max_replacement_count", name="ck_alea_replacement_bounded"),
        CheckConstraint("retry_count >= 0", name="ck_alea_retry_count_non_negative"),
        Index("ix_alea_stage_next_attempt", "stage", "next_attempt_at"),
        Index("ix_alea_campaign_instrument", "campaign_id", "instrument"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'kraken_spot'"))
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'BUY'"))
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PROPOSED'"))
    preferred_limit_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    maximum_profitable_entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    invalidation_price: Mapped[Decimal | None] = mapped_column(Numeric)
    requested_base_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    filled_base_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    approved_notional: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    live_crypto_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"))
    replaces_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_limit_entry_attempts.attempt_id", ondelete="RESTRICT"))
    replacement_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_replacement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_repricing_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    last_repriced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text)
    evidence_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
