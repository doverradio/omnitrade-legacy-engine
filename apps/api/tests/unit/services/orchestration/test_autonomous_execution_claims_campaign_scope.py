from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.services.orchestration import autonomous_execution_claims as subject
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [CanonicalProvingActivation.__table__, AutonomousExecutionClaim.__table__, AuditLog.__table__]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _claim_row(*, campaign_id: uuid.UUID, campaign_version: int, claim_status: str, now: datetime) -> AutonomousExecutionClaim:
    return AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=uuid.uuid4(), activation_id=uuid.uuid4(),
        campaign_id=campaign_id, campaign_version=campaign_version,
        mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        account_id=uuid.uuid4(), profile_id=uuid.uuid4(), connection_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product="BTC-USD", side="BUY",
        claim_status=claim_status, claimed_at=now, claim_owner="worker:test",
        recover_after=now + timedelta(minutes=2), attempt_count=1,
    )


# --- schema-level: the partial unique index that replaces the old permanent one -------

@pytest.mark.asyncio
async def test_two_nonterminal_claims_for_same_campaign_version_conflict() -> None:
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=1, claim_status="CLAIMED", now=now))
        await session.flush()
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=1, claim_status="CLAIMED", now=now))
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("nonterminal_status", sorted(subject._CLAIM_SCOPE_NONTERMINAL_STATES))
async def test_every_nonterminal_status_blocks_a_second_claim_in_the_same_scope(nonterminal_status: str) -> None:
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=1, claim_status=nonterminal_status, now=now))
        await session.flush()
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=1, claim_status="CLAIMED", now=now))
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("released_status", sorted(subject._CLAIM_SCOPE_RELEASED_STATES))
async def test_a_released_historical_claim_never_blocks_a_new_sequential_claim(released_status: str) -> None:
    """Reproduces the exact production regression: the OLD, plain
    UNIQUE(campaign_id, campaign_version) blocked every second claim
    forever, regardless of the first claim's resolved status. The new
    partial index must let a released (provider-never-called or fully
    resolved) historical claim's campaign/version be reclaimed."""
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=3, claim_status=released_status, now=now))
        await session.flush()
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=3, claim_status="CLAIMED", now=now))
        await session.flush()  # must not raise
        from sqlalchemy import select
        rows = (await session.execute(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.campaign_id == campaign_id)
        )).scalars().all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_different_campaign_versions_never_conflict_even_when_both_nonterminal() -> None:
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=1, claim_status="CLAIMED", now=now))
        await session.flush()
        session.add(_claim_row(campaign_id=campaign_id, campaign_version=2, claim_status="CLAIMED", now=now))
        await session.flush()  # must not raise


@pytest.mark.asyncio
async def test_package_id_uniqueness_is_still_enforced() -> None:
    """Requirement: one durable claim per package remains an absolute
    invariant -- unaffected by replacing the campaign-version constraint."""
    now = datetime.now(timezone.utc)
    package_id = uuid.uuid4()
    async with _real_session() as session:
        first = _claim_row(campaign_id=uuid.uuid4(), campaign_version=1, claim_status="CLAIMED", now=now)
        first.package_id = package_id
        session.add(first)
        await session.flush()
        second = _claim_row(campaign_id=uuid.uuid4(), campaign_version=1, claim_status="CLAIMED", now=now)
        second.package_id = package_id
        session.add(second)
        with pytest.raises(IntegrityError):
            await session.flush()


# --- application-level: distinguishing a real conflict from a same-package race -------

def _package(now: datetime):
    return SimpleNamespace(
        package_id=uuid.uuid4(), package_state="ACTIVATED", side="BUY", preview_expires_at=now + timedelta(minutes=5),
        superseded_at=None, authorization_source="MANDATE", mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        mandate_evaluation_id=uuid.uuid4(), campaign_id=uuid.uuid4(), campaign_version=1, paper_account_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), provider="kraken_spot", environment="production", product="BTC-USD",
        runtime_campaign_id=uuid.uuid4(), market_evidence_identity={"exchange_connection_id": str(uuid.uuid4())},
    )


def _settings(package):
    return SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=package.campaign_id,
        automatic_mandate_package_activation_campaign_version=package.campaign_version,
        automatic_mandate_package_activation_mandate_id=package.mandate_id,
        automatic_mandate_package_activation_mandate_version_id=package.mandate_version_id,
    )


@pytest.mark.asyncio
async def test_insert_rejected_by_active_campaign_scope_reports_the_conflicting_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), package_id=package.package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=4),
        campaign_id=package.campaign_id, campaign_version=1, paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id, provider=package.provider,
        environment=package.environment, product=package.product,
    )
    runtime = SimpleNamespace(id=7, status="RUNNING", definition_version=1)
    mandate = SimpleNamespace(status="ACTIVE", expires_at=now + timedelta(days=1))
    version = SimpleNamespace(is_active=True, is_authorized=True, mandate_id=package.mandate_id)
    conflicting_claim = SimpleNamespace(
        claim_id=uuid.uuid4(), package_id=uuid.uuid4(), claim_owner="worker:other", claim_status="SUBMISSION_PENDING",
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[
            package, None, None, activation, runtime, mandate, version, None, None, None, 0, None, None, conflicting_claim,
        ]),
        add=Mock(), flush=AsyncMock(),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: _settings(package))

    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, claim_owner="worker:test", now=now)

    assert outcome.claim is None
    assert not outcome.created
    assert outcome.reason_code == "active_campaign_execution_claim_exists"


@pytest.mark.asyncio
async def test_insert_rejected_with_no_identifiable_conflicting_claim_falls_back_to_generic_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), package_id=package.package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=4),
        campaign_id=package.campaign_id, campaign_version=1, paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id, provider=package.provider,
        environment=package.environment, product=package.product,
    )
    runtime = SimpleNamespace(id=7, status="RUNNING", definition_version=1)
    mandate = SimpleNamespace(status="ACTIVE", expires_at=now + timedelta(days=1))
    version = SimpleNamespace(is_active=True, is_authorized=True, mandate_id=package.mandate_id)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[
            package, None, None, activation, runtime, mandate, version, None, None, None, 0, None, None, None,
        ]),
        add=Mock(), flush=AsyncMock(),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: _settings(package))

    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, claim_owner="worker:test", now=now)

    assert outcome.claim is None
    assert outcome.reason_code == "claim_concurrency_conflict"


@pytest.mark.asyncio
async def test_same_package_replay_returns_existing_claim_without_a_second_insert_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    claim = SimpleNamespace(claim_id=uuid.uuid4(), package_id=package.package_id, claim_status="SUBMISSION_PENDING", claim_owner="worker:test")
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, claim]))

    outcome = await subject.claim_activated_buy_package(db=db, package_id=package.package_id, now=now)

    assert outcome.claim is claim
    assert not outcome.created
    assert outcome.reason_code == "already_claimed"
    # Only 2 db.scalar calls: package fetch + existing-claim lookup -- proves
    # no second insert or any further query was attempted for a same-package replay.
    assert db.scalar.await_count == 2


# --- release_execution_claim_scope_if_order_resolved -----------------------------------

@pytest.mark.asyncio
async def test_filled_order_releases_scope_via_buy_reconciled() -> None:
    """A genuinely successful BUY must eventually release its campaign
    scope -- before this existed, nothing in the codebase ever advanced a
    claim past SUBMISSION_PENDING, so a successful execution would have
    reserved the scope forever, exactly like the original defect."""
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    live_order_id = uuid.uuid4()
    async with _real_session() as session:
        claim = _claim_row(campaign_id=campaign_id, campaign_version=1, claim_status="SUBMISSION_PENDING", now=now)
        claim.live_order_id = live_order_id
        session.add(claim)
        await session.flush()

        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=live_order_id, order_status="FILLED",
        )

        refreshed = await session.get(AutonomousExecutionClaim, claim.claim_id)
        assert refreshed.claim_status == "BUY_RECONCILED"
        assert refreshed.claim_status in subject._CLAIM_SCOPE_RELEASED_STATES
        assert refreshed.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("order_status", "expected_claim_status"), [("CANCELLED", "CANCELLED"), ("REJECTED", "CANCELLED"), ("EXPIRED", "CANCELLED")])
async def test_terminal_provider_failure_releases_scope(order_status: str, expected_claim_status: str) -> None:
    now = datetime.now(timezone.utc)
    live_order_id = uuid.uuid4()
    async with _real_session() as session:
        claim = _claim_row(campaign_id=uuid.uuid4(), campaign_version=1, claim_status="SUBMISSION_PENDING", now=now)
        claim.live_order_id = live_order_id
        session.add(claim)
        await session.flush()

        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=live_order_id, order_status=order_status,
        )

        refreshed = await session.get(AutonomousExecutionClaim, claim.claim_id)
        assert refreshed.claim_status == expected_claim_status


@pytest.mark.asyncio
@pytest.mark.parametrize("still_unresolved_status", ["PARTIALLY_FILLED", "ACKNOWLEDGED", "SUBMITTED", "UNKNOWN", "PENDING_CONFIRMATION"])
async def test_still_ambiguous_order_status_does_not_release_scope(still_unresolved_status: str) -> None:
    now = datetime.now(timezone.utc)
    live_order_id = uuid.uuid4()
    async with _real_session() as session:
        claim = _claim_row(campaign_id=uuid.uuid4(), campaign_version=1, claim_status="SUBMISSION_PENDING", now=now)
        claim.live_order_id = live_order_id
        session.add(claim)
        await session.flush()

        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=live_order_id, order_status=still_unresolved_status,
        )

        refreshed = await session.get(AutonomousExecutionClaim, claim.claim_id)
        assert refreshed.claim_status == "SUBMISSION_PENDING"


@pytest.mark.asyncio
async def test_release_is_idempotent_and_never_reverses_an_already_released_claim() -> None:
    now = datetime.now(timezone.utc)
    live_order_id = uuid.uuid4()
    async with _real_session() as session:
        claim = _claim_row(campaign_id=uuid.uuid4(), campaign_version=1, claim_status="SAFETY_DISABLED", now=now)
        claim.live_order_id = live_order_id
        session.add(claim)
        await session.flush()

        # A late/duplicate reconciliation pass for an order whose claim was
        # already dead-ended pre-provider must never resurrect or overwrite it.
        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=live_order_id, order_status="FILLED",
        )

        refreshed = await session.get(AutonomousExecutionClaim, claim.claim_id)
        assert refreshed.claim_status == "SAFETY_DISABLED"


@pytest.mark.asyncio
async def test_release_is_a_no_op_when_no_claim_references_the_order() -> None:
    async with _real_session() as session:
        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=uuid.uuid4(), order_status="FILLED",
        )  # must not raise


@pytest.mark.asyncio
async def test_released_claim_via_reconciliation_frees_scope_for_a_later_sequential_claim() -> None:
    """End-to-end proof of the full lifecycle fix: a genuinely successful
    BUY's claim, once reconciled, must free its campaign scope for a later,
    legitimate sequential Controlled Proof -- combining the schema-level
    partial index with the new release hook. (The "still blocked while
    unresolved" half of this lifecycle is already covered by
    test_two_nonterminal_claims_for_same_campaign_version_conflict and the
    parametrized nonterminal-status test above.)"""
    now = datetime.now(timezone.utc)
    campaign_id = uuid.uuid4()
    live_order_id = uuid.uuid4()
    async with _real_session() as session:
        first = _claim_row(campaign_id=campaign_id, campaign_version=5, claim_status="SUBMISSION_PENDING", now=now)
        first.live_order_id = live_order_id
        session.add(first)
        await session.flush()

        await subject.release_execution_claim_scope_if_order_resolved(
            db=session, live_crypto_order_id=live_order_id, order_status="FILLED",
        )

        second = _claim_row(campaign_id=campaign_id, campaign_version=5, claim_status="CLAIMED", now=now)
        session.add(second)
        await session.flush()  # must not raise now that the first claim's scope is released

        from sqlalchemy import select
        rows = (await session.execute(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.campaign_id == campaign_id)
        )).scalars().all()
        assert len(rows) == 2
        assert {row.claim_status for row in rows} == {"BUY_RECONCILED", "CLAIMED"}
