from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveExecutionQualityMetric(Base):
    __tablename__ = "live_execution_quality_metrics"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_live_execution_quality_metrics_idempotency_key"),
        CheckConstraint("side IN ('buy','sell')", name="ck_live_execution_quality_metrics_side"),
        CheckConstraint(
            "expected_price_state IN ('available','unknown','unavailable')",
            name="ck_live_execution_quality_metrics_expected_price_state",
        ),
        CheckConstraint(
            "actual_price_state IN ('available','unknown','unavailable')",
            name="ck_live_execution_quality_metrics_actual_price_state",
        ),
        CheckConstraint(
            "slippage_state IN ('available','unknown','unavailable')",
            name="ck_live_execution_quality_metrics_slippage_state",
        ),
        CheckConstraint("expected_price >= 0", name="ck_live_execution_quality_metrics_expected_price_non_negative"),
        CheckConstraint("actual_fill_price >= 0", name="ck_live_execution_quality_metrics_actual_price_non_negative"),
        CheckConstraint("slippage_abs >= 0", name="ck_live_execution_quality_metrics_slippage_abs_non_negative"),
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
    source_execution_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_execution_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_reconciliation_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_reconciliation_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_accounting_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_accounting_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    expected_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    expected_price_state: Mapped[str] = mapped_column(Text, nullable=False)
    actual_fill_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    actual_price_state: Mapped[str] = mapped_column(Text, nullable=False)
    slippage_abs: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    slippage_state: Mapped[str] = mapped_column(Text, nullable=False)
    market_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    telemetry_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


@event.listens_for(LiveExecutionQualityMetric, "before_update", propagate=True)
def _prevent_live_execution_quality_metric_update(
    _mapper: Any,
    _connection: Any,
    _target: LiveExecutionQualityMetric,
) -> None:
    raise ValueError("live_execution_quality_metrics is append-only and does not support updates")


@event.listens_for(LiveExecutionQualityMetric, "before_delete", propagate=True)
def _prevent_live_execution_quality_metric_delete(
    _mapper: Any,
    _connection: Any,
    _target: LiveExecutionQualityMetric,
) -> None:
    raise ValueError("live_execution_quality_metrics is append-only and does not support deletes")
