from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RiskRuleConfig(Base):
    __tablename__ = "risk_rule_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    paper_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    max_position_size_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_daily_loss_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    default_stop_loss_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cooldown_after_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    cooldown_duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )