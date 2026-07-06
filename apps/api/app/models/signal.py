from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("action IN ('buy','sell','hold')", name="ck_signals_action"),
        CheckConstraint(
            "status IN ('generated','risk_approved','risk_rejected','executed','expired')",
            name="ck_signals_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parameter_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    raw_strength: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    regime_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )