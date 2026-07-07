from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LiveTradingProfile(Base):
    __tablename__ = "live_trading_profiles"
    __table_args__ = (
        CheckConstraint(
            "operating_mode IN ('paper','live')",
            name="ck_live_trading_profiles_operating_mode",
        ),
        CheckConstraint(
            "lifecycle_state IN ('draft','pending_approval','approved','enabled','suspended')",
            name="ck_live_trading_profiles_lifecycle_state",
        ),
        CheckConstraint(
            "approval_state IN ('not_requested','pending','approved','rejected','revoked')",
            name="ck_live_trading_profiles_approval_state",
        ),
        CheckConstraint(
            "paper_default_mode = true",
            name="ck_live_trading_profiles_paper_default_mode_true",
        ),
        CheckConstraint(
            "risk_authority_model = 'risk_engine_final'",
            name="ck_live_trading_profiles_risk_authority_model",
        ),
        CheckConstraint(
            "autonomous_capital_allocation = false",
            name="ck_live_trading_profiles_no_autonomous_capital_allocation",
        ),
        CheckConstraint(
            "autonomous_strategy_evolution = false",
            name="ck_live_trading_profiles_no_autonomous_strategy_evolution",
        ),
        CheckConstraint(
            "automatic_promotion_enabled = false",
            name="ck_live_trading_profiles_no_automatic_promotion",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR live_opt_in = true)",
            name="ck_live_trading_profiles_live_requires_opt_in",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR approval_state = 'approved')",
            name="ck_live_trading_profiles_live_requires_approval",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR human_approval_recorded = true)",
            name="ck_live_trading_profiles_live_requires_human_approval_recorded",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR governance_approved = true)",
            name="ck_live_trading_profiles_live_requires_governance_approval",
        ),
        CheckConstraint(
            "(operating_mode = 'paper' OR lifecycle_state IN ('enabled','suspended'))",
            name="ck_live_trading_profiles_live_mode_lifecycle_boundary",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    paper_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operating_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'paper'"))
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    approval_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'not_requested'"))
    live_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    human_approval_recorded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    paper_default_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    governance_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    risk_authority_model: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'risk_engine_final'"),
    )
    autonomous_capital_allocation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    autonomous_strategy_evolution: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    automatic_promotion_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    provenance_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )
