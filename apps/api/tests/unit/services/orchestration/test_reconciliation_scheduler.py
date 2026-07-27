from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration import reconciliation_scheduler as subject
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [LiveCryptoOrder.__table__]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _order(
    *, status: str, submitted_at: datetime | None, product_id: str = "BTC-USD", side: str = "BUY",
) -> LiveCryptoOrder:
    return LiveCryptoOrder(
        live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        exchange_connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
        product_id=product_id, side=side, order_type="market", requested_quote_size=Decimal("5"),
        client_order_id=str(uuid.uuid4()), status=status, submitted_at=submitted_at,
        audit_correlation_id=uuid.uuid4(),
    )


# --- discover_reconciliation_candidates: real sqlite, exercises the actual SQL --------

@pytest.mark.asyncio
async def test_no_unresolved_orders_returns_empty() -> None:
    async with _real_session() as session:
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == []


@pytest.mark.asyncio
async def test_submitted_unresolved_buy_order_is_discovered() -> None:
    now = datetime.now(timezone.utc)
    async with _real_session() as session:
        order = _order(status="SUBMISSION_PENDING", submitted_at=now)
        session.add(order)
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == [order.live_crypto_order_id]


@pytest.mark.asyncio
async def test_partially_filled_order_is_discovered() -> None:
    now = datetime.now(timezone.utc)
    async with _real_session() as session:
        order = _order(status="PARTIALLY_FILLED", submitted_at=now)
        session.add(order)
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == [order.live_crypto_order_id]


@pytest.mark.asyncio
async def test_unsubmitted_order_is_never_a_candidate() -> None:
    """A package that has only been prepared (dry-run order), never actually
    sent to the provider, has nothing to reconcile against yet."""
    async with _real_session() as session:
        order = _order(status="PENDING_CONFIRMATION", submitted_at=None)
        session.add(order)
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", sorted(subject._TERMINAL_ORDER_STATUSES))
async def test_terminal_order_statuses_are_never_candidates(terminal_status: str) -> None:
    now = datetime.now(timezone.utc)
    async with _real_session() as session:
        order = _order(status=terminal_status, submitted_at=now)
        session.add(order)
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == []


@pytest.mark.asyncio
async def test_ordinary_and_controlled_proof_orders_are_discovered_identically() -> None:
    """discover_reconciliation_candidates has no notion of Controlled Proof
    at all -- it only ever looks at LiveCryptoOrder's own submitted_at/
    status, so ordinary autonomous execution and Controlled Proof execution
    are reconciled through the exact same path, with no special-casing."""
    now = datetime.now(timezone.utc)
    async with _real_session() as session:
        buy_order = _order(status="SUBMISSION_PENDING", submitted_at=now, side="BUY")
        sell_order = _order(status="SUBMISSION_PENDING", submitted_at=now + timedelta(seconds=1), side="SELL")
        session.add_all([buy_order, sell_order])
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=10)
        assert candidates == [buy_order.live_crypto_order_id, sell_order.live_crypto_order_id]


@pytest.mark.asyncio
async def test_batch_limit_is_respected_oldest_first() -> None:
    now = datetime.now(timezone.utc)
    async with _real_session() as session:
        older = _order(status="SUBMISSION_PENDING", submitted_at=now - timedelta(minutes=5))
        newer = _order(status="SUBMISSION_PENDING", submitted_at=now)
        session.add_all([newer, older])
        await session.flush()
        candidates = await subject.discover_reconciliation_candidates(db=session, limit=1)
        assert candidates == [older.live_crypto_order_id]


# --- poll_unresolved_live_orders: mocked LiveCryptoOrderService.reconcile -------------

class _FakeReconcileService:
    def __init__(self, outcomes: dict[uuid.UUID, object]) -> None:
        self._outcomes = outcomes
        self.calls: list[uuid.UUID] = []

    async def reconcile(self, *, db, live_crypto_order_id, request):
        self.calls.append(live_crypto_order_id)
        outcome = self._outcomes[live_crypto_order_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(*, status: str, reconciliation_status: str = "open") -> SimpleNamespace:
    return SimpleNamespace(
        live_crypto_order=SimpleNamespace(status=status),
        reconciliation_status=reconciliation_status,
        provider_order_id="kraken-order-1",
        provider_fill_observed=status == "FILLED",
    )


def _enabled_settings(*, batch_limit: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        automatic_live_order_reconciliation_enabled=True,
        automatic_live_order_reconciliation_batch_limit=batch_limit,
    )


class _FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_disabled_setting_skips_discovery_and_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
        automatic_live_order_reconciliation_enabled=False, automatic_live_order_reconciliation_batch_limit=10,
    ))

    async def _unexpected_discover(*, db, limit):
        raise AssertionError("discovery must not run when the feature is disabled")

    monkeypatch.setattr(subject, "discover_reconciliation_candidates", _unexpected_discover)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(0, 0, 0, 0)


@pytest.mark.asyncio
async def test_no_candidates_is_a_clean_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)

    async def _discover(*, db, limit):
        return []

    monkeypatch.setattr(subject, "discover_reconciliation_candidates", _discover)
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: _FakeReconcileService({}))

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(0, 0, 0, 0)


@pytest.mark.asyncio
async def test_pending_order_remains_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([order_id]))
    fake_service = _FakeReconcileService({order_id: _response(status="ACKNOWLEDGED")})
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(1, 0, 1, 0)
    assert fake_service.calls == [order_id]


@pytest.mark.asyncio
async def test_partial_fill_remains_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([order_id]))
    fake_service = _FakeReconcileService({order_id: _response(status="PARTIALLY_FILLED", reconciliation_status="partially_filled")})
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(1, 0, 1, 0)


@pytest.mark.asyncio
async def test_completed_buy_counts_as_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([order_id]))
    fake_service = _FakeReconcileService({order_id: _response(status="FILLED", reconciliation_status="reconciled")})
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(1, 1, 0, 0)


@pytest.mark.asyncio
async def test_completed_sell_counts_as_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconciliation itself has no BUY/SELL branch -- the same function
    resolves either side identically, since LiveCryptoOrder.side is not
    consulted by discover_reconciliation_candidates or poll_unresolved_
    live_orders at all."""
    order_id = uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([order_id]))
    fake_service = _FakeReconcileService({order_id: _response(status="FILLED", reconciliation_status="reconciled")})
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome.reconciled == 1


@pytest.mark.asyncio
async def test_cancelled_order_counts_as_reconciled_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([order_id]))
    fake_service = _FakeReconcileService({order_id: _response(status="CANCELLED", reconciliation_status="reconciled")})
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome == subject.ReconciliationPollOutcome(1, 1, 0, 0)


@pytest.mark.asyncio
async def test_provider_failure_is_isolated_from_other_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    failing_id, succeeding_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([failing_id, succeeding_id]))
    fake_service = _FakeReconcileService({
        failing_id: RuntimeError("provider outage"),
        succeeding_id: _response(status="FILLED", reconciliation_status="reconciled"),
    })
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)
    db = _FakeDb()

    outcome = await subject.poll_unresolved_live_orders(db=db)

    assert outcome == subject.ReconciliationPollOutcome(2, 1, 0, 1)
    assert fake_service.calls == [failing_id, succeeding_id]
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_accounting_or_reconciliation_failure_is_also_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import InvalidRequestError

    failing_id, succeeding_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(subject, "get_settings", _enabled_settings)
    monkeypatch.setattr(subject, "discover_reconciliation_candidates", lambda *, db, limit: _async([failing_id, succeeding_id]))
    fake_service = _FakeReconcileService({
        failing_id: InvalidRequestError(message="balance mismatch", details={"blocker": "balance_mismatch"}),
        succeeding_id: _response(status="FILLED", reconciliation_status="reconciled"),
    })
    monkeypatch.setattr(subject, "LiveCryptoOrderService", lambda: fake_service)

    outcome = await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert outcome.failed == 1
    assert outcome.reconciled == 1


@pytest.mark.asyncio
async def test_batch_limit_setting_is_passed_through_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "get_settings", lambda: _enabled_settings(batch_limit=3))
    seen_limits = []

    async def _discover(*, db, limit):
        seen_limits.append(limit)
        return []

    monkeypatch.setattr(subject, "discover_reconciliation_candidates", _discover)

    await subject.poll_unresolved_live_orders(db=_FakeDb())

    assert seen_limits == [3]


@pytest.mark.asyncio
async def test_explicit_limit_overrides_configured_batch_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "get_settings", lambda: _enabled_settings(batch_limit=10))
    seen_limits = []

    async def _discover(*, db, limit):
        seen_limits.append(limit)
        return []

    monkeypatch.setattr(subject, "discover_reconciliation_candidates", _discover)

    await subject.poll_unresolved_live_orders(db=_FakeDb(), limit=1)

    assert seen_limits == [1]


async def _async(value):
    return value
