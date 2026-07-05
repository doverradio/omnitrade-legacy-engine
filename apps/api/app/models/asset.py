from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("asset_class IN ('crypto', 'stock')", name="ck_assets_asset_class"),
        UniqueConstraint("symbol", "exchange", name="uq_assets_symbol_exchange"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    asset_class: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    supports_fractional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    min_order_notional: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    qty_step_size: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    candles = relationship("Candle", back_populates="asset", cascade="all, delete-orphan")