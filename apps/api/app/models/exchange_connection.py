from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExchangeConnection(Base):
    __tablename__ = "exchange_connections"
    __table_args__ = (
        CheckConstraint("provider IN ('coinbase_advanced')", name="ck_exchange_connections_provider"),
        CheckConstraint("environment IN ('sandbox', 'production')", name="ck_exchange_connections_environment"),
        CheckConstraint("status IN ('connected', 'disconnected', 'error')", name="ck_exchange_connections_status"),
        Index("ix_exchange_connections_provider_env", "provider", "environment"),
    )

    exchange_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    connection_name: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'disconnected'"))

    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_masked: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_masked: Mapped[str] = mapped_column(Text, nullable=False)
    passphrase_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    credentials_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    api_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    account_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    balances: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    total_equity_usd: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_api_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_readiness_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_readiness_report: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), server_onupdate=text("now()")
    )
