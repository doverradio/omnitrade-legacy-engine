from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration import automatic_package_executor
from app.services.orchestration import autonomous_execution_claims as claims_module
from app.services.orchestration import continuous_pipeline_worker as worker
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [
    CapitalCampaign.__table__,
    CanonicalPreviewPackage.__table__,
    ControlledProofRun.__table__,
    ControlledProofExitRecovery.__table__,
    AutonomousExecutionClaim.__table__,
    LiveCryptoOrder.__table__,
    LiveAccountingRecord.__table__,
    AuditLog.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_proof_with_sell_package(
    session: AsyncSession,
    *,
    sell_package_state: str = "READY",
    sell_authorization_expires_at: datetime | None = None,
    with_sell_claim: bool = False,
    with_sell_order: bool = False,
) -> tuple[ControlledProofRun, CanonicalPreviewPackage]:
    """A proof whose BUY leg is already fully reconciled and filled (the
    real incident shape) and whose SELL leg is linked to package_id but in
    a caller-controlled lineage/state, to exercise every branch of the new
    ordinary-cycle retry guard without needing the full risk/mandate/
    execution machinery for the BUY side."""
    campaign_id = uuid.uuid4()
    campaign_version = 1

    buy_package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), live_trading_profile_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product="BTC-USD", side="BUY",
        proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="ACTIVATED",
        generated_at=datetime.now(timezone.utc), idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
    )
    session.add(buy_package)
    await session.flush()

    proof = ControlledProofRun(
        proof_id=uuid.uuid4(), status="WAITING_FOR_PROFITABLE_EXIT", provider="kraken_spot", environment="production",
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30), package_id=buy_package.package_id,
    )
    session.add(proof)
    await session.flush()

    sell_package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=buy_package.paper_account_id,
        live_trading_profile_id=buy_package.live_trading_profile_id,
        provider="kraken_spot", environment="production", product="BTC-USD", side="SELL",
        proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state=sell_package_state,
        authorization_expires_at=sell_authorization_expires_at,
        authorization_source=None if sell_package_state == "READY" else "MANDATE",
        mandate_id=None if sell_package_state == "READY" else uuid.uuid4(),
        mandate_version_id=None if sell_package_state == "READY" else uuid.uuid4(),
        mandate_evaluation_id=None if sell_package_state == "READY" else uuid.uuid4(),
        dry_run_live_crypto_order_id=(
            uuid.uuid4() if sell_package_state in {"DRY_RUN_PASSED", "ACTIVATED"} else None
        ),
        generated_at=datetime.now(timezone.utc), idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
    )
    session.add(sell_package)
    await session.flush()

    proof.sell_package_id = sell_package.package_id
    for package in (buy_package, sell_package):
        package.market_evidence_identity = {
            **(package.market_evidence_identity or {}), "controlled_proof_id": str(proof.proof_id),
        }
    await session.flush()

    if with_sell_claim:
        order = None
        if with_sell_order:
            order = LiveCryptoOrder(
                live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=sell_package.crypto_order_preview_id,
                exchange_connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
                product_id="BTC-USD", side="SELL", order_type="MARKET", requested_quote_size=Decimal("5"),
                client_order_id=f"sell-{uuid.uuid4()}", status="ACKNOWLEDGED",
                provider_order_id=f"provider-{uuid.uuid4()}", submitted_at=datetime.now(timezone.utc),
                audit_correlation_id=proof.audit_correlation_id,
            )
            session.add(order)
            await session.flush()
        claim = AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=sell_package.package_id, activation_id=uuid.uuid4(),
            campaign_id=campaign_id, campaign_version=campaign_version, mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
            account_id=sell_package.paper_account_id, profile_id=sell_package.live_trading_profile_id,
            connection_id=uuid.uuid4(), provider="kraken_spot", environment="production", product="BTC-USD", side="SELL",
            claim_status="SUBMISSION_PENDING", claimed_at=datetime.now(timezone.utc), claim_owner="test",
            live_order_id=None if order is None else order.live_crypto_order_id,
        )
        session.add(claim)
        await session.flush()

    return proof, sell_package


def _install_claim_activated_stub(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Stands in for claim_activated_package's own (separately, already
    tested) mandate/activation/dry-run governance chain: persists one real
    AutonomousExecutionClaim the first time it is asked to claim a given
    package, and replays that same claim (created=False) on any later
    call for the same package -- the same idempotent contract the real
    function guarantees. Proves this fix's new branch genuinely continues
    into claim persistence + advance_claimed_execution, without needing to
    reconstruct claim_activated_package's own unrelated governance fixture."""
    calls: list[uuid.UUID] = []

    async def _claim_activated(*, db, package_id, claim_owner=None, now=None):
        calls.append(package_id)
        existing = await db.scalar(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == package_id).limit(1)
        )
        if existing is not None:
            return claims_module.AutonomousClaimOutcome(existing, False, "already_claimed")
        package = await db.get(CanonicalPreviewPackage, package_id)
        claim = AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=package_id, activation_id=uuid.uuid4(),
            campaign_id=package.campaign_id, campaign_version=package.campaign_version,
            mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
            account_id=package.paper_account_id, profile_id=package.live_trading_profile_id,
            connection_id=uuid.uuid4(), provider=package.provider, environment=package.environment,
            product=package.product, side=package.side, claim_status="CLAIMED",
            claimed_at=datetime.now(timezone.utc), claim_owner=claim_owner or "system:controlled_proof_worker",
        )
        db.add(claim)
        await db.flush()
        return claims_module.AutonomousClaimOutcome(claim, True, "claimed")

    monkeypatch.setattr(worker, "claim_activated_package", _claim_activated)
    return calls


def _install_claim_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """claim_controlled_proof_by_id's own expires_at <= now comparison
    fails under sqlite (DateTime(timezone=True) round-trips tz-naive here
    -- a pre-existing sqlite-test-environment limitation, unrelated to this
    fix, already worked around the same way in test_reconciliation_correction.py).
    Bypasses only that locking mechanic, not anything this test verifies."""

    async def _claim_stub(*, db, proof_id, cycle_id=None):
        return await db.get(ControlledProofRun, proof_id)

    monkeypatch.setattr(worker, "claim_controlled_proof_by_id", _claim_stub)


@dataclass
class _ProgressionSpy:
    calls: list[uuid.UUID]
    outcome_activation_state: str = "BLOCKED"
    outcome_failed_closed: bool = False

    async def __call__(self, *, db, request):
        self.calls.append(request.package_id)
        return automatic_package_executor.AutomaticPackageExecutionOutcome(
            package_id=request.package_id, campaign_id=request.campaign_id, campaign_version=request.campaign_version,
            decision_record_id=request.decision_record_id, mandate_id=None, authorization_state="UNKNOWN",
            dry_run_state="UNKNOWN", activation_state=self.outcome_activation_state, authority_source=None,
            replayed=False, final_reason_code="test_stub", failed_closed=self.outcome_failed_closed,
            starting_state="UNKNOWN",
        )


@pytest.mark.asyncio
async def test_package_only_sell_package_retried_on_ordinary_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, sell_package = await _seed_proof_with_sell_package(session, sell_package_state="READY")
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == [sell_package.package_id]


@pytest.mark.asyncio
async def test_successful_retry_continues_into_claim_execution_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, sell_package = await _seed_proof_with_sell_package(session, sell_package_state="ACTIVATED")
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[], outcome_activation_state="ACTIVATED", outcome_failed_closed=False)
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)
        claim_calls = _install_claim_activated_stub(monkeypatch)

        advance_calls: list[uuid.UUID] = []

        async def _advance(*, db, claim):
            advance_calls.append(claim.claim_id)

        monkeypatch.setattr(worker, "advance_claimed_execution", _advance)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == [sell_package.package_id]
        assert claim_calls == [sell_package.package_id]
        created_claim = await session.scalar(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == sell_package.package_id)
        )
        assert created_claim is not None
        assert created_claim.side == "SELL"
        assert advance_calls == [created_claim.claim_id]


@pytest.mark.asyncio
async def test_claim_only_sell_package_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, _sell_package = await _seed_proof_with_sell_package(
            session, sell_package_state="ACTIVATED", with_sell_claim=True, with_sell_order=False,
        )
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == []


@pytest.mark.asyncio
async def test_order_linked_sell_package_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, _sell_package = await _seed_proof_with_sell_package(
            session, sell_package_state="ACTIVATED", with_sell_claim=True, with_sell_order=True,
        )
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state", ["EXPIRED", "INVALIDATED", "SUPERSEDED", "FAILED_CLOSED", "COMPLETED"],
)
async def test_terminal_sell_package_untouched(terminal_state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, _sell_package = await _seed_proof_with_sell_package(session, sell_package_state=terminal_state)
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == []


@pytest.mark.asyncio
async def test_expired_sell_package_untouched_and_still_requires_exit_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, sell_package = await _seed_proof_with_sell_package(
            session, sell_package_state="READY",
            sell_authorization_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        assert spy.calls == []
        # Untouched: no claim was created, and the package is exactly as it
        # was -- only an authorized exit-recovery may supersede it.
        assert (await session.scalar(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == sell_package.package_id)
        )) is None
        refreshed_package = await session.get(CanonicalPreviewPackage, sell_package.package_id)
        assert refreshed_package.package_state == "READY"
        assert refreshed_package.superseded_at is None


@pytest.mark.asyncio
async def test_repeated_ordinary_cycles_never_duplicate_claims_or_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        proof, sell_package = await _seed_proof_with_sell_package(session, sell_package_state="ACTIVATED")
        _install_claim_stub(monkeypatch)
        spy = _ProgressionSpy(calls=[], outcome_activation_state="ACTIVATED", outcome_failed_closed=False)
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)
        claim_calls = _install_claim_activated_stub(monkeypatch)

        advance_calls: list[uuid.UUID] = []

        async def _advance(*, db, claim):
            advance_calls.append(claim.claim_id)

        monkeypatch.setattr(worker, "advance_claimed_execution", _advance)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)
        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        # The claim-persistence stub ran on the first cycle -- exactly one
        # claim exists no matter how many ordinary cycles run afterward.
        claims = (await session.scalars(
            select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == sell_package.package_id)
        )).all()
        assert len(claims) == 1
        assert advance_calls == [claims[0].claim_id]
        # The second ordinary cycle's own lineage check must see CLAIM_ONLY
        # (or ORDER_LINKED) now, not PACKAGE_ONLY -- so it never re-invokes
        # progression at all.
        assert spy.calls == [sell_package.package_id]
        assert claim_calls == [sell_package.package_id]


@pytest.mark.asyncio
async def test_exit_recovery_progression_retry_behavior_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new ordinary-cycle branch must not alter the pre-existing
    recovery-authorized retry path at all: a non-expired linked SELL
    package still progresses via the same _progress_package call when a
    recovery is IN_PROGRESS."""
    async with _real_session() as session:
        proof, sell_package = await _seed_proof_with_sell_package(session, sell_package_state="READY")
        recovery = ControlledProofExitRecovery(
            recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
            idempotency_key=f"idem-{uuid.uuid4()}", authorized_by="operator:alice",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            claimed_at=datetime.now(timezone.utc),
        )
        session.add(recovery)
        await session.flush()

        async def _claim_recovery_stub(*, db, recovery_id):
            return recovery, proof

        monkeypatch.setattr(worker, "claim_exit_recovery_by_id", _claim_recovery_stub)

        async def _refresh_stub(*, db, recovery, proof):
            return None

        monkeypatch.setattr(worker, "refresh_exit_recovery_completion", _refresh_stub)

        spy = _ProgressionSpy(calls=[])
        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", spy)

        await worker._attempt_operator_controlled_proof_entry(db=session, recovery_id=recovery.recovery_id)

        assert spy.calls == [sell_package.package_id]
