from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveAccountingRecord(Base):
    __tablename__ = "live_accounting_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_accounting_records_idempotency_key"),
        CheckConstraint(
            "record_type IN ('fill_accounting','partial_fill_accounting','fee_attribution')",
            name="ck_live_accounting_records_record_type",
        ),
        CheckConstraint(
            "source_execution_event_type = 'execution_intent_created'",
            name="ck_live_accounting_records_source_execution_event_type",
        ),
        CheckConstraint(
            "side IN ('buy','sell')",
            name="ck_live_accounting_records_side",
        ),
        CheckConstraint("filled_quantity >= 0", name="ck_live_accounting_records_filled_quantity"),
        CheckConstraint("fill_price >= 0", name="ck_live_accounting_records_fill_price"),
        CheckConstraint("gross_notional >= 0", name="ck_live_accounting_records_gross_notional"),
        CheckConstraint("fee_amount >= 0", name="ck_live_accounting_records_fee_amount"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    live_trading_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_trading_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    reconciliation_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_reconciliation_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_execution_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_execution_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_execution_event_type: Mapped[str] = mapped_column(Text, nullable=False)
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_fill_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    gross_notional: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fee_currency: Mapped[str] = mapped_column(Text, nullable=False)
    net_cash_impact: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveAccountingRecord, "before_update", propagate=True)
def _prevent_live_accounting_record_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveAccountingRecord,
) -> None:
    raise ValueError("live_accounting_records is append-only and does not support updates")


@event.listens_for(LiveAccountingRecord, "before_delete", propagate=True)
def _prevent_live_accounting_record_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveAccountingRecord,
) -> None:
    raise ValueError("live_accounting_records is append-only and does not support deletes")
