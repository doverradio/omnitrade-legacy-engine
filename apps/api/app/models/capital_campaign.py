from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CapitalCampaign(Base):
    __tablename__ = "capital_campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','READY','RUNNING','PAUSED','TARGET_REACHED','COMPLETED','ARCHIVED')",
            name="ck_capital_campaigns_status",
        ),
        CheckConstraint(
            "(definition_campaign_id IS NULL AND definition_version IS NULL) OR (definition_campaign_id IS NOT NULL AND definition_version IS NOT NULL)",
            name="ck_capital_campaigns_definition_pin_pair",
        ),
        CheckConstraint(
            "definition_campaign_id IS NULL OR uuid = definition_campaign_id",
            name="ck_capital_campaigns_definition_pin_identity",
        ),
        ForeignKeyConstraint(
            ["definition_campaign_id", "definition_version"],
            ["capital_campaign_definitions.campaign_id", "capital_campaign_definitions.version"],
            name="fk_capital_campaigns_definition_pin",
            ondelete="SET NULL",
        ),
        Index("ix_capital_campaigns_uuid", "uuid", unique=True),
        Index("ix_capital_campaigns_status", "status"),
        Index("ix_capital_campaigns_owner", "owner"),
        Index("ix_capital_campaigns_validation_run_id", "validation_run_id"),
        Index("ix_capital_campaigns_paper_account_id", "paper_account_id"),
        Index("ix_capital_campaigns_strategy_id", "strategy_id"),
        Index("ix_capital_campaigns_definition_pin", "definition_campaign_id", "definition_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    campaign_type: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_account_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    validation_run_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_runs.validation_run_id", ondelete="SET NULL"),
        nullable=True,
    )
    strategy_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
    )
    definition_campaign_id: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    definition_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starting_capital: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    current_equity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    unrealized_profit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    roi: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    paper_account = relationship("PaperAccount")
    validation_run = relationship("ValidationRun")
    strategy = relationship("Strategy")
