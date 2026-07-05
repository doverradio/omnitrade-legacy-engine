from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("asset_id", "interval", "open_time", name="uq_candles_asset_interval_open_time"),
        Index("idx_candles_asset_interval_time", "asset_id", "interval", "open_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    asset = relationship("Asset", back_populates="candles")