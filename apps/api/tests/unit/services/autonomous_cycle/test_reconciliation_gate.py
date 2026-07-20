from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import TextClause

from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.autonomous_cycle.orchestrator import _is_live_order_unresolved, _reconcile_state

# ---------------------------------------------------------------------------
# Phase 3, items 1-8: _is_live_order_unresolved is a pure function of a
# LiveCryptoOrder's fields -- no DB needed to test the classification rule.
# ---------------------------------------------------------------------------


def _order(**overrides: Any) -> LiveCryptoOrder:
    defaults: dict[str, Any] = dict(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(),
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_quote_size=5,
        client_order_id=f"client-{uuid.uuid4()}",
        status="ACKNOWLEDGED",
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        safe_provider_response={},
        audit_correlation_id=uuid.uuid4(),
    )
    defaults.update(overrides)
    return LiveCryptoOrder(**defaults)


@pytest.mark.parametrize("status", ["FILLED", "filled", "Filled"])
def test_filled_is_resolved_regardless_of_casing(status: str) -> None:
    order = _order(status=status, provider_order_id="OID-1", submitted_at=datetime.now(timezone.utc))
    assert _is_live_order_unresolved(order) is False


@pytest.mark.parametrize("status", ["CANCELLED", "FAILED", "REJECTED", "EXPIRED", "SETTLED",
                                     "cancelled", "failed", "rejected", "expired", "settled"])
def test_terminal_statuses_resolved_regardless_of_casing(status: str) -> None:
    order = _order(status=status, provider_order_id="OID-1", submitted_at=datetime.now(timezone.utc))
    assert _is_live_order_unresolved(order) is False


def test_dry_run_ready_never_submitted_does_not_block() -> None:
    order = _order(
        status="DRY_RUN_READY",
        provider_order_id=None,
        submitted_at=None,
        safe_provider_response={"dry_run": True, "submission_skipped": True},
    )
    assert _is_live_order_unresolved(order) is False


def test_dry_run_blocked_never_submitted_does_not_block() -> None:
    order = _order(
        status="DRY_RUN_BLOCKED",
        provider_order_id=None,
        submitted_at=None,
        safe_provider_response={"dry_run": True, "submission_skipped": True},
    )
    assert _is_live_order_unresolved(order) is False


def test_genuinely_pending_submitted_order_still_blocks() -> None:
    order = _order(status="SUBMISSION_PENDING", provider_order_id=None, submitted_at=None, safe_provider_response={})
    assert _is_live_order_unresolved(order) is True


@pytest.mark.parametrize(
    "overrides",
    [
        # DRY_RUN status but a provider_order_id is present -- an unexpected/
        # ambiguous state that must not be silently excluded.
        {"status": "DRY_RUN_READY", "provider_order_id": "OID-UNEXPECTED", "submitted_at": None, "safe_provider_response": {"submission_skipped": True}},
        # DRY_RUN status but submitted_at is populated -- ambiguous, fail closed.
        {"status": "DRY_RUN_BLOCKED", "provider_order_id": None, "submitted_at": datetime.now(timezone.utc), "safe_provider_response": {"submission_skipped": True}},
        # DRY_RUN status without the explicit submission_skipped marker -- fail closed.
        {"status": "DRY_RUN_READY", "provider_order_id": None, "submitted_at": None, "safe_provider_response": {}},
    ],
)
def test_ambiguous_dry_run_records_fail_closed(overrides: dict[str, Any]) -> None:
    order = _order(**overrides)
    assert _is_live_order_unresolved(order) is True


@pytest.mark.parametrize("status", ["UNKNOWN", "SOMETHING_NEW", "", "reconciliation_required", "RECONCILIATION_REQUIRED"])
def test_unknown_or_non_terminal_statuses_fail_closed(status: str) -> None:
    order = _order(status=status)
    assert _is_live_order_unresolved(order) is True


# ---------------------------------------------------------------------------
# Phase 3, items 9-10: real, in-memory SQLAlchemy session exercising the
# actual _reconcile_state() query against real LiveCryptoOrder rows, proving
# (a) no row is mutated and (b) the exact two-order production scenario from
# the diagnostic clears after the fix.
# ---------------------------------------------------------------------------


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))

    tables = [LiveCryptoOrder.__table__, CryptoOrderPreview.__table__]
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
                raw = default.arg.text.strip().split("::", 1)[0]
                if raw.endswith("()") and not raw.startswith("("):
                    raw = f"({raw})"
                column.server_default = DefaultClause(text(raw))

    try:
        async with engine.begin() as conn:
            await conn.run_sync(LiveCryptoOrder.metadata.create_all, tables=tables)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


class _FakeProvider:
    async def fetch_balances(self, *, credentials, environment):
        return SimpleNamespace(balances={"USD": 100})


@pytest.mark.asyncio
async def test_reconciliation_gate_does_not_mutate_any_order_or_history_row() -> None:
    async with _real_session() as session:
        connection_id = uuid.uuid4()
        filled_order = _order(
            exchange_connection_id=connection_id,
            status="FILLED",
            provider_order_id="OAXUZJ-7WRL5-NPFWYA",
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(filled_order)
        await session.flush()
        before_status, before_updated_at = filled_order.status, filled_order.updated_at

        mandate = SimpleNamespace(exchange_connection_id=connection_id, paper_account_id=None)
        result = await _reconcile_state(
            db=session, mandate=mandate, provider=_FakeProvider(), credentials={}, environment="production", product_id="BTC-USD"
        )

        assert result.unresolved_order_count == 0
        # Re-read from the session -- nothing about the row changed.
        assert filled_order.status == before_status
        assert filled_order.updated_at == before_updated_at


@pytest.mark.asyncio
async def test_exact_two_order_production_scenario_clears_after_fix() -> None:
    """Reproduces the exact diagnosed production state: one FILLED order
    (previously misclassified due to the case-mismatch defect) and one
    never-submitted DRY_RUN_READY artifact on the same exchange connection.
    Neither should block after the fix.
    """
    async with _real_session() as session:
        connection_id = uuid.uuid4()
        filled_order = _order(
            exchange_connection_id=connection_id,
            status="FILLED",
            provider_status="FILLED",
            provider_order_id="OAXUZJ-7WRL5-NPFWYA",
            submitted_at=datetime.now(timezone.utc),
        )
        dry_run_order = _order(
            exchange_connection_id=connection_id,
            status="DRY_RUN_READY",
            provider_order_id=None,
            submitted_at=None,
            safe_provider_response={"dry_run": True, "submission_skipped": True},
        )
        session.add_all([filled_order, dry_run_order])
        await session.flush()

        mandate = SimpleNamespace(exchange_connection_id=connection_id, paper_account_id=None)
        result = await _reconcile_state(
            db=session, mandate=mandate, provider=_FakeProvider(), credentials={}, environment="production", product_id="BTC-USD"
        )

        assert result.unresolved_order_count == 0
        assert "CHECK_FAILED:unresolved_live_order_exists" not in result.explanation


@pytest.mark.asyncio
async def test_genuinely_unresolved_order_still_blocks_end_to_end() -> None:
    async with _real_session() as session:
        connection_id = uuid.uuid4()
        pending_order = _order(
            exchange_connection_id=connection_id,
            status="RECONCILIATION_REQUIRED",
            provider_order_id="OID-STILL-OPEN",
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(pending_order)
        await session.flush()

        mandate = SimpleNamespace(exchange_connection_id=connection_id, paper_account_id=None)
        result = await _reconcile_state(
            db=session, mandate=mandate, provider=_FakeProvider(), credentials={}, environment="production", product_id="BTC-USD"
        )

        assert result.unresolved_order_count == 1
        assert "CHECK_FAILED:unresolved_live_order_exists" in result.explanation
