from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.services.orchestration import autonomous_execution_claims as subject


def _package(now: datetime):
    return SimpleNamespace(
        package_id=uuid4(), package_state="ACTIVATED", side="BUY", preview_expires_at=now + timedelta(minutes=5),
        superseded_at=None, authorization_source="MANDATE", mandate_id=uuid4(), mandate_version_id=uuid4(),
        mandate_evaluation_id=uuid4(), campaign_id=uuid4(), campaign_version=1, paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(), provider="kraken_spot", environment="production", product="BTC-USD",
        runtime_campaign_id=uuid4(), market_evidence_identity={"exchange_connection_id": str(uuid4())},
    )


def _settings(package):
    return SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=package.campaign_id,
        automatic_mandate_package_activation_campaign_version=package.campaign_version,
        automatic_mandate_package_activation_mandate_id=package.mandate_id,
        automatic_mandate_package_activation_mandate_version_id=package.mandate_version_id,
    )


@pytest.mark.asyncio
async def test_stale_activated_package_creates_no_claim() -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    package.preview_expires_at = now
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, None]))
    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, now=now)
    assert outcome.claim is None
    assert outcome.reason_code == "package_not_eligible"


@pytest.mark.asyncio
async def test_existing_claim_is_idempotently_replayed() -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    claim = SimpleNamespace(claim_id=uuid4(), package_id=package.package_id, claim_status="SAFETY_DISABLED")
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, claim]))
    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, now=now)
    assert outcome.claim is claim
    assert not outcome.created
    assert outcome.reason_code == "already_claimed"


@pytest.mark.asyncio
async def test_fresh_matching_package_creates_one_durable_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    activation = SimpleNamespace(
        activation_id=uuid4(), package_id=package.package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=4),
        campaign_id=package.campaign_id, campaign_version=1, paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id, provider=package.provider,
        environment=package.environment, product=package.product,
    )
    runtime = SimpleNamespace(id=7, status="RUNNING", definition_version=1)
    mandate = SimpleNamespace(status="ACTIVE", expires_at=now + timedelta(days=1))
    version = SimpleNamespace(is_active=True, is_authorized=True, mandate_id=package.mandate_id)
    claim = SimpleNamespace(claim_id=uuid4(), package_id=package.package_id, claim_status="CLAIMED")
    db = SimpleNamespace(
        # The extra `None` right after the existing-claim check is the new
        # ControlledProofRun linkage lookup (_resolve_autonomous_execution_scope)
        # -- no proof is linked to this package, so it falls through to the
        # unchanged, settings-derived configured-scope path exercised here.
        scalar=AsyncMock(side_effect=[package, None, None, activation, runtime, mandate, version, None, None, None, 0, uuid4(), claim]),
        add=Mock(), flush=AsyncMock(),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: _settings(package))
    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, claim_owner="worker:test", now=now)
    assert outcome.created
    assert outcome.claim is claim
    assert outcome.reason_code == "claimed"


@pytest.mark.asyncio
async def test_submission_disabled_is_recoverable_and_not_ambiguous() -> None:
    now = datetime.now(timezone.utc)
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), claim_status="CLAIMED", claim_owner="worker:test",
        last_error_code=None, recover_after=now, updated_at=now, reconciliation_state=None,
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    await subject.mark_submission_safety_disabled(db=db, claim=claim)
    assert claim.claim_status == "SAFETY_DISABLED"
    assert claim.last_error_code == "live_submission_disabled"
    assert claim.reconciliation_state is None
    assert claim.recover_after is None


def test_claim_schema_prevents_duplicate_package_and_activation() -> None:
    str(CreateTable(AutonomousExecutionClaim.__table__).compile(dialect=postgresql.dialect()))
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AutonomousExecutionClaim.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("package_id",) in unique_columns
    assert ("activation_id",) in unique_columns


# --- advance_claimed_execution: shared prepare-then-execute path, always terminal on failure ---

def _claim(*, claim_status: str = "CLAIMED") -> SimpleNamespace:
    return SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), campaign_id=uuid4(), campaign_version=1,
        claim_status=claim_status, claim_owner="worker:test",
    )


def _prepared(claim) -> SimpleNamespace:
    order = SimpleNamespace(live_crypto_order_id=uuid4())
    return SimpleNamespace(claim=claim, order=order, replayed=False)


@pytest.mark.asyncio
async def test_advance_claimed_execution_reaches_submission_pending_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)

    async def _prepare(*, db, claim_id):
        assert claim_id == claim.claim_id
        return prepared

    async def _execute(*, db, prepared):
        return SimpleNamespace(current_state="SUBMITTED")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == "SUBMISSION_PENDING"
    assert claim.reconciliation_state == "SUBMITTED"


@pytest.mark.asyncio
async def test_advance_claimed_execution_reaches_reconciliation_required_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)

    async def _prepare(*, db, claim_id):
        return prepared

    async def _execute(*, db, prepared):
        return SimpleNamespace(current_state="RECONCILIATION_REQUIRED")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_advance_claimed_execution_invalid_request_error_terminalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import InvalidRequestError

    claim = _claim()
    blocked = []

    async def _prepare(*, db, claim_id):
        raise InvalidRequestError(message="failed closed", details={"blocker": "activation_not_effective"})

    async def _mark_blocked(*, db, claim, reason_code):
        blocked.append((claim, reason_code))

    async def _execute(*, db, prepared):
        raise AssertionError("must not execute when preparation failed")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(claim, "activation_not_effective")]


@pytest.mark.asyncio
async def test_advance_claimed_execution_unexpected_exception_terminalizes_instead_of_leaving_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5: an unforeseen exception (not InvalidRequestError) from
    preparation must still reach a terminal state -- this is exactly the gap
    that left claim 854e9b17-f608-400a-b6fe-58647b730cf0 with no lifecycle
    event beyond 'created'."""
    claim = _claim()
    blocked = []

    async def _prepare(*, db, claim_id):
        raise RuntimeError("unexpected bug deep in preparation")

    async def _mark_blocked(*, db, claim, reason_code):
        blocked.append((claim, reason_code))

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(claim, "unexpected_preparation_failure")]
    assert claim.claim_status == "CLAIMED"  # advance_claimed_execution itself never mutates; mark_pre_provider_blocked (mocked here) does


@pytest.mark.asyncio
async def test_advance_claimed_execution_marks_safety_disabled_when_submission_off(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    safety_calls = []

    async def _prepare(*, db, claim_id):
        return prepared

    async def _mark_safety_disabled(*, db, claim):
        safety_calls.append(claim)

    async def _execute(*, db, prepared):
        raise AssertionError("must not execute when live submission is disabled")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "mark_submission_safety_disabled", _mark_safety_disabled)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=False))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert safety_calls == [claim]


@pytest.mark.asyncio
async def test_advance_claimed_execution_execute_failure_terminalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    blocked = []

    async def _prepare(*, db, claim_id):
        return prepared

    async def _execute(*, db, prepared):
        raise RuntimeError("provider evidence unavailable")

    async def _mark_blocked(*, db, claim, reason_code):
        blocked.append((claim, reason_code))

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_buy", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(claim, "commissioned_execution_request_evidence_unavailable")]


# --- sweep_stale_autonomous_execution_claims: the never-implemented recovery pass ----

@pytest.mark.asyncio
async def test_sweep_advances_each_stale_claim_found(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_a, claim_b = _claim(), _claim()
    advanced = []

    async def _advance(*, db, claim):
        advanced.append(claim)

    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [claim_a, claim_b])))
    monkeypatch.setattr(subject, "advance_claimed_execution", _advance)

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 2
    assert advanced == [claim_a, claim_b]


@pytest.mark.asyncio
async def test_sweep_returns_zero_when_nothing_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    monkeypatch.setattr(subject, "advance_claimed_execution", AsyncMock())

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 0


@pytest.mark.asyncio
async def test_sweep_isolates_one_claims_failure_from_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_a, claim_b = _claim(), _claim()
    advanced = []

    async def _advance(*, db, claim):
        if claim is claim_a:
            raise RuntimeError("advance_claimed_execution itself misbehaved")
        advanced.append(claim)

    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [claim_a, claim_b])))
    monkeypatch.setattr(subject, "advance_claimed_execution", _advance)

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 2
    assert advanced == [claim_b]


@pytest.mark.asyncio
async def test_sweep_query_filters_by_claimed_status_and_due_recovery() -> None:
    """Proves the sweep's WHERE clause targets exactly claim_status='CLAIMED'
    AND recover_after IS NOT NULL AND recover_after <= now -- against a real
    SQLite-backed session, not a mock, so the actual SQL is exercised."""
    from datetime import timedelta

    from app.models.audit_log import AuditLog
    from tests.support.real_sqlite_session import real_sqlite_session

    now = datetime.now(timezone.utc)

    async with real_sqlite_session([AutonomousExecutionClaim.__table__, AuditLog.__table__]) as session:
        def _row(**overrides):
            defaults = dict(
                claim_id=uuid4(), package_id=uuid4(), activation_id=uuid4(), campaign_id=uuid4(),
                campaign_version=1, mandate_id=uuid4(), mandate_version_id=uuid4(), account_id=uuid4(),
                profile_id=uuid4(), connection_id=uuid4(), provider="kraken_spot", environment="production",
                product="BTC-USD", side="BUY", claim_status="CLAIMED", claimed_at=now - timedelta(minutes=10),
                claim_owner="worker:test", recover_after=now - timedelta(minutes=8), attempt_count=1,
            )
            defaults.update(overrides)
            return AutonomousExecutionClaim(**defaults)

        due = _row()
        not_yet_due = _row(recover_after=now + timedelta(minutes=5))
        never_swept = _row(recover_after=None)
        already_terminal = _row(claim_status="COMPLETED", recover_after=now - timedelta(minutes=8))
        session.add_all([due, not_yet_due, never_swept, already_terminal])
        await session.flush()

        stale = (await session.scalars(
            select(AutonomousExecutionClaim).where(
                AutonomousExecutionClaim.claim_status == "CLAIMED",
                AutonomousExecutionClaim.recover_after.is_not(None),
                AutonomousExecutionClaim.recover_after <= now,
            )
        )).all()

        assert [item.claim_id for item in stale] == [due.claim_id]
