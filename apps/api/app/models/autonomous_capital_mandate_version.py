from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousCapitalMandateVersion(Base):
    __tablename__ = "autonomous_capital_mandate_versions"
    __table_args__ = (
        CheckConstraint("version_number >= 1", name="ck_ac_mandate_versions_version_number"),
        CheckConstraint("authorized_capital_usd > 0", name="ck_ac_mandate_versions_authorized_capital"),
        CheckConstraint("max_order_notional_usd > 0", name="ck_ac_mandate_versions_max_order_notional"),
        CheckConstraint("max_open_exposure_usd > 0", name="ck_ac_mandate_versions_max_open_exposure"),
        CheckConstraint("max_daily_deployed_usd > 0", name="ck_ac_mandate_versions_max_daily_deployed"),
        CheckConstraint("max_daily_realized_loss_usd >= 0", name="ck_ac_mandate_versions_max_daily_loss"),
        CheckConstraint("max_campaign_drawdown_usd >= 0", name="ck_ac_mandate_versions_max_drawdown"),
        CheckConstraint("max_consecutive_losses >= 0", name="ck_ac_mandate_versions_max_consecutive_losses"),
        CheckConstraint("position_limit >= 0", name="ck_ac_mandate_versions_position_limit"),
        CheckConstraint("price_evidence_max_age_seconds > 0", name="ck_ac_mandate_versions_price_freshness"),
        CheckConstraint("max_slippage_bps >= 0", name="ck_ac_mandate_versions_max_slippage"),
        CheckConstraint("max_fee_bps >= 0", name="ck_ac_mandate_versions_max_fee"),
        CheckConstraint("approval_policy IN ('HUMAN_REQUIRED','MANDATE_ALLOWED')", name="ck_ac_mandate_versions_approval_policy"),
        UniqueConstraint("mandate_id", "version_number", name="uq_ac_mandate_versions_mandate_version_number"),
        UniqueConstraint("version_hash", name="uq_ac_mandate_versions_hash"),
        Index("ix_ac_mandate_versions_mandate", "mandate_id"),
        Index("ix_ac_mandate_versions_active", "is_active"),
        Index("ix_ac_mandate_versions_authorized", "is_authorized"),
    )

    mandate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    mandate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_capital_mandates.mandate_id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_hash: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_capital_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_order_notional_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_open_exposure_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_daily_deployed_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_daily_realized_loss_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_campaign_drawdown_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    position_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    price_evidence_max_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_slippage_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    max_fee_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    allowed_products: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allowed_order_sides: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allowed_strategy_versions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    entry_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    exit_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cooldown_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operating_schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'HUMAN_REQUIRED'"))
    reconciliation_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kill_switch_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    owner_acknowledgements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    authorization_evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# is_active is deliberately excluded: it is lifecycle bookkeeping (which authorized
# version currently governs an ACTIVE mandate), not part of the authorized economic
# terms, and must remain settable by apply_mandate_lifecycle_action()/
# authorize_mandate_version() after a version has been authorized.
_MUTABLE_AFTER_AUTHORIZATION_COLUMNS = {"is_active"}


@event.listens_for(AutonomousCapitalMandateVersion, "before_update", propagate=True)
def _prevent_authorized_version_update(_mapper: Any, _connection: Any, target: AutonomousCapitalMandateVersion) -> None:
    state = inspect(target)
    authorized_history = state.attrs.is_authorized.history
    # Use the pre-flush (committed) value of is_authorized, not the pending one --
    # otherwise the very update that flips is_authorized False->True would trip
    # this guard on itself and authorization could never be persisted.
    was_already_authorized = bool(authorized_history.deleted[0]) if authorized_history.deleted else bool(target.is_authorized)
    if not was_already_authorized:
        return
    for attr in state.attrs:
        if attr.key in _MUTABLE_AFTER_AUTHORIZATION_COLUMNS:
            continue
        if attr.history.has_changes():
            raise ValueError("authorized mandate versions are immutable")


@event.listens_for(AutonomousCapitalMandateVersion, "before_delete", propagate=True)
def _prevent_version_delete(_mapper: Any, _connection: Any, target: AutonomousCapitalMandateVersion) -> None:
    if bool(target.is_authorized):
        raise ValueError("authorized mandate versions are immutable")
