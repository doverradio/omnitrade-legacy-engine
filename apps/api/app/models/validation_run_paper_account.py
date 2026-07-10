from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ValidationRunPaperAccount(Base):
    __tablename__ = "validation_run_paper_accounts"

    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_runs.validation_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    paper_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
