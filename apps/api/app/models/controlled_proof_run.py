from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ControlledProofRun(Base):
    """One row per operator-requested controlled production proof.

    Persists only the operator's request scope, the coarse operational
    lifecycle (REQUESTED/CLAIMED/BLOCKED/EXPIRED/CANCELLED/FAILED) the worker
    and service actually gate behavior on, and reference columns pointing at
    the real downstream records (decision, mandate evaluation, package, BUY
    order, SELL order, position) as they become known. Fine-grained progress
    states (ENTRY_PROPOSED..PROFIT_CONFIRMED) are derived at read time from
    those referenced records -- never duplicated here as a second, possibly
    stale copy of truth -- though the last-observed derived value is written
    back to `status` opportunistically so the persisted row still reflects
    current progress between reads.
    """

    __tablename__ = "controlled_proof_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_controlled_proof_runs_idempotency_key"),
        CheckConstraint(
            "status IN ('REQUESTED','CLAIMED','ENTRY_PROPOSED','PACKAGE_CREATED','POSITION_OPEN',"
            "'WAITING_FOR_PROFITABLE_EXIT','EXITED','RECONCILED','PROFIT_CONFIRMED','BLOCKED','EXPIRED',"
            "'CANCELLED','FAILED')",
            name="ck_controlled_proof_runs_status",
        ),
        CheckConstraint(
            "terminal_verdict IS NULL OR terminal_verdict IN "
            "('LIFECYCLE_PROVEN_PROFIT','LIFECYCLE_PROVEN_LOSS','LIFECYCLE_PROVEN_FLAT','BLOCKED','FAILED')",
            name="ck_controlled_proof_runs_terminal_verdict",
        ),
        # Real, database-level "at most one active controlled proof"
        # invariant -- not just an application-level race. Same pattern as
        # VenueCommissioningRun.uq_vcr_active_scope: a unique partial index,
        # Postgres-only (SQLite test coverage for this specific race is a
        # real-Postgres-with-skip integration test, matching this
        # codebase's existing convention for the identical VenueCommissioningRun
        # invariant -- see tests/integration/test_venue_commissioning_concurrency.py).
        Index(
            "uq_controlled_proof_runs_single_active",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "status IN ('REQUESTED','CLAIMED','ENTRY_PROPOSED','PACKAGE_CREATED','POSITION_OPEN',"
                "'WAITING_FOR_PROFITABLE_EXIT')"
            ),
            # SQLite also supports partial indexes (3.8.0+) and SQLAlchemy
            # honors this dialect-specific kwarg identically to
            # postgresql_where above -- without it, the sqlite test double
            # silently enforces "at most one row in the whole table, ever",
            # which is far stricter than production and forces tests to
            # delete terminal rows just to insert a new active one.
            sqlite_where=text(
                "status IN ('REQUESTED','CLAIMED','ENTRY_PROPOSED','PACKAGE_CREATED','POSITION_OPEN',"
                "'WAITING_FOR_PROFITABLE_EXIT')"
            ),
        ),
    )

    proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'REQUESTED'"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    max_notional_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by_cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mandate_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mandate_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sell_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    buy_live_crypto_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sell_live_crypto_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    position_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    net_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    terminal_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
