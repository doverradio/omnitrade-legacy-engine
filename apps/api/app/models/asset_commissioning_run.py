from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetCommissioningRun(Base):
    """One row per commissioning attempt for one (provider, product_id, campaign_id).

    Stage-by-stage progress and evidence live in `stages` (keyed by stage name,
    each entry {"status": ..., "evidence": {...}, "completed_at": ...}) so a
    resumed run can inspect exactly which stages already succeeded without
    re-deriving that from scattered tables.
    """

    __tablename__ = "asset_commissioning_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_asset_commissioning_runs_idempotency_key"),
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','FAILED')", name="ck_asset_commissioning_runs_status"),
    )

    commissioning_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    activate: Mapped[bool] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'IN_PROGRESS'"))
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mandate_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
