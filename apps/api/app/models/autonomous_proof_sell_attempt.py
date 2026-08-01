from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutonomousProofSellAttempt(Base):
    """One durable, non-replaceable Autonomous Proof SELL worker attempt."""

    __tablename__ = "autonomous_proof_sell_attempts"
    __table_args__ = (
        UniqueConstraint("custody_id", name="uq_apsa_custody"),
        UniqueConstraint("campaign_id", "campaign_version", "runtime_campaign_id", name="uq_apsa_campaign_attempt"),
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_apsa_campaign_definition", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "stage IN ('SELECTED','EVALUATED','AUTHORIZED','PACKAGED','CLAIMED','ORDERED','RECONCILING','TERMINAL')",
            name="ck_apsa_stage",
        ),
        CheckConstraint("(stage = 'TERMINAL') = hard_stopped", name="ck_apsa_terminal_hard_stop"),
        CheckConstraint("retry_count >= 0", name="ck_apsa_retry_count"),
        Index("ix_apsa_due", "stage", "next_attempt_at"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    custody_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_custodies.custody_id", ondelete="RESTRICT"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capital_campaigns.uuid", ondelete="RESTRICT"), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SELECTED'"))
    authority_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_position_exit_authorities.authority_id", ondelete="RESTRICT"))
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_preview_packages.package_id", ondelete="RESTRICT"))
    activation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_proving_activations.activation_id", ondelete="RESTRICT"))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("autonomous_execution_claims.claim_id", ondelete="RESTRICT"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_crypto_orders.live_crypto_order_id", ondelete="RESTRICT"))
    reconciliation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("live_reconciliation_events.id", ondelete="RESTRICT"))
    blocker: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hard_stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    terminal_reason: Mapped[str | None] = mapped_column(Text)
    proof_sell_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
