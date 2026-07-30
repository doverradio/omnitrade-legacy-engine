from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
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
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration import automatic_package_executor
from app.services.orchestration.autonomous_execution_claims import AutonomousClaimOutcome
from app.services.orchestration import continuous_pipeline_worker as worker
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [
    CapitalCampaign.__table__,
    CanonicalPreviewPackage.__table__,
    ControlledProofRun.__table__,
    ControlledProofExitRecovery.__table__,
    AutonomousExecutionClaim.__table__,
    LiveCryptoOrder.__table__,
    AuditLog.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _mandate_ids() -> dict:
    return {"mandate_id": uuid.uuid4(), "mandate_version_id": uuid.uuid4(), "mandate_evaluation_id": uuid.uuid4()}


async def _seed_expired_proof_with_preexisting_sell_package(
    session: AsyncSession, *, sell_authorization_expires_at: datetime | None = None,
) -> tuple[ControlledProofRun, CanonicalPreviewPackage]:
    """The exact confirmed production shape: a SELL package that reached
    PACKAGE_ONLY under *ordinary* WAITING_FOR_PROFITABLE_EXIT (no exit
    recovery involved at all -- market_evidence_identity carries no
    controlled_proof_exit_recovery_id stamp) before the proof itself went
    EXPIRED, later resumed by a freshly authorized, claimed Exit Recovery
    via authorize_controlled_proof_exit_recovery's allow_existing_sell_
    package contract."""
    campaign_id = uuid.uuid4()
    campaign_version = 1
    proof_id = uuid.uuid4()
    ids = _mandate_ids()

    sell_package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), live_trading_profile_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product="BTC-USD", side="SELL",
        proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="READY",
        authorization_expires_at=sell_authorization_expires_at,
        generated_at=datetime.now(timezone.utc), idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
        mandate_id=ids["mandate_id"], mandate_version_id=ids["mandate_version_id"],
        mandate_evaluation_id=ids["mandate_evaluation_id"],
        # Stamped with controlled_proof_id (as every Controlled-Proof-mode
        # package is, per create_canonical_preview_package) but deliberately
        # WITHOUT controlled_proof_exit_recovery_id -- this package was
        # created during ordinary WAITING_FOR_PROFITABLE_EXIT, never under
        # any exit recovery at all.
        market_evidence_identity={"controlled_proof_id": str(proof_id)},
    )
    session.add(sell_package)
    await session.flush()

    proof = ControlledProofRun(
        proof_id=proof_id, status="EXPIRED", provider="kraken_spot", environment="production",
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
    )
    session.add(proof)
    await session.flush()

    return proof, sell_package


def _install_no_op_completion_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refresh(*, db, recovery, proof):
        return None

    monkeypatch.setattr(worker, "refresh_exit_recovery_completion", _refresh)


@pytest.mark.asyncio
async def test_claimed_recovery_resumes_preexisting_sell_package_and_activates(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end reproduction of the confirmed production fix: dispatch via
    recovery_id for an EXPIRED proof whose SELL package predates the
    recovery must reach activation -- not controlled_proof_not_active."""
    async with _real_session() as session:
        proof, sell_package = await _seed_expired_proof_with_preexisting_sell_package(session)
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
        _install_no_op_completion_refresh(monkeypatch)

        activated_calls: list[uuid.UUID] = []

        async def _fake_activate(*, db, request):
            calls_package = await db.get(CanonicalPreviewPackage, request.package_id)
            calls_package.package_state = "ACTIVATED"
            activated_calls.append(request.package_id)

        async def _fake_authorize(*, db, request):
            pkg = await db.get(CanonicalPreviewPackage, request.package_id)
            pkg.package_state = "AUTHORIZED"
            pkg.authorization_source = "MANDATE"

        async def _fake_dry_run(*, db, request):
            pkg = await db.get(CanonicalPreviewPackage, request.package_id)
            pkg.package_state = "DRY_RUN_PASSED"
            pkg.dry_run_live_crypto_order_id = uuid.uuid4()

        monkeypatch.setattr(automatic_package_executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
        monkeypatch.setattr(automatic_package_executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
        monkeypatch.setattr(automatic_package_executor, "activate_canonical_proving_campaign", _fake_activate)

        claim_calls: list[uuid.UUID] = []

        async def _claim_activated(*, db, package_id, claim_owner=None, now=None):
            claim_calls.append(package_id)
            return AutonomousClaimOutcome(None, False, "test_stub_no_claim_machinery")

        monkeypatch.setattr(worker, "claim_activated_package", _claim_activated)

        await worker._attempt_operator_controlled_proof_entry(db=session, recovery_id=recovery.recovery_id)

        assert activated_calls == [sell_package.package_id]
        refreshed = await session.get(CanonicalPreviewPackage, sell_package.package_id)
        assert refreshed.package_state == "ACTIVATED"
        assert claim_calls == [sell_package.package_id]
        # The recovery itself is untouched by a successful activation --
        # completion is refresh_exit_recovery_completion's job (stubbed
        # here), not _progress_package's.
        refreshed_recovery = await session.get(ControlledProofExitRecovery, recovery.recovery_id)
        assert refreshed_recovery.status == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_unsuccessful_dispatch_records_retryable_reason_when_not_failed_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When activation does not achieve ACTIVATED but the executor did not
    report a definitive, config-level failed_closed stop, the recovery must
    still receive an explicit failure_reason -- and remains IN_PROGRESS so a
    later cycle, within the recovery's own bounded expiry, can retry."""
    async with _real_session() as session:
        proof, sell_package = await _seed_expired_proof_with_preexisting_sell_package(session)
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
        _install_no_op_completion_refresh(monkeypatch)

        async def _not_achieved(*, db, request):
            return automatic_package_executor.AutomaticPackageExecutionOutcome(
                package_id=request.package_id, campaign_id=request.campaign_id, campaign_version=request.campaign_version,
                decision_record_id=request.decision_record_id, mandate_id=None, authorization_state="UNKNOWN",
                dry_run_state="UNKNOWN", activation_state="NOT_ACTIVATED", authority_source=None,
                replayed=False, final_reason_code="automatic_mandate_package_activation_disabled",
                failed_closed=False, starting_state="READY",
            )

        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", _not_achieved)

        await worker._attempt_operator_controlled_proof_entry(db=session, recovery_id=recovery.recovery_id)

        refreshed_recovery = await session.get(ControlledProofExitRecovery, recovery.recovery_id)
        assert refreshed_recovery.status == "IN_PROGRESS"
        assert refreshed_recovery.failure_reason == (
            "retryable:activation_not_achieved:automatic_mandate_package_activation_disabled"
        )
        assert refreshed_recovery.blocked_reason is None
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery",
            AuditLog.entity_id == recovery.recovery_id,
            AuditLog.action == "controlled_proof_exit_recovery.waiting",
        ))).all()
        assert len(audits) == 1


@pytest.mark.asyncio
async def test_unsuccessful_dispatch_terminalizes_recovery_when_failed_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A definitive, config/scope-level failed_closed stop must terminalize
    the claimed recovery to BLOCKED with an explicit reason -- it must not
    be left to retry forever against a condition that cannot self-resolve."""
    async with _real_session() as session:
        proof, sell_package = await _seed_expired_proof_with_preexisting_sell_package(session)
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
        _install_no_op_completion_refresh(monkeypatch)

        async def _failed_closed(*, db, request):
            return automatic_package_executor.AutomaticPackageExecutionOutcome(
                package_id=request.package_id, campaign_id=request.campaign_id, campaign_version=request.campaign_version,
                decision_record_id=request.decision_record_id, mandate_id=None, authorization_state="UNKNOWN",
                dry_run_state="UNKNOWN", activation_state="NOT_ACTIVATED", authority_source=None,
                replayed=False, final_reason_code="automatic_activation_mandate_scope_mismatch",
                failed_closed=True, starting_state="READY",
            )

        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", _failed_closed)

        await worker._attempt_operator_controlled_proof_entry(db=session, recovery_id=recovery.recovery_id)

        refreshed_recovery = await session.get(ControlledProofExitRecovery, recovery.recovery_id)
        assert refreshed_recovery.status == "BLOCKED"
        assert refreshed_recovery.blocked_reason == "activation_failed_closed:automatic_activation_mandate_scope_mismatch"
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery",
            AuditLog.entity_id == recovery.recovery_id,
            AuditLog.action == "controlled_proof_exit_recovery.blocked",
        ))).all()
        assert len(audits) == 1


@pytest.mark.asyncio
async def test_unsuccessful_dispatch_never_touches_ordinary_non_recovery_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: this fix must not reopen or alter ordinary (recovery=
    None) Controlled Proof progression -- a failed activation for a plain
    proof-driven dispatch must never invoke the new recovery-terminalization
    logic (gated strictly on `recovery is not None`), regardless of what
    get_controlled_proof_view's own, pre-existing, unrelated evidence-based
    status projection (service.py's _derive_fine_grained_status, called
    unconditionally by this same code path) independently does to
    proof.status for a proof whose BUY/decision/position evidence this
    fixture does not fully populate."""
    async with _real_session() as session:
        proof, sell_package = await _seed_expired_proof_with_preexisting_sell_package(session)
        proof.status = "WAITING_FOR_PROFITABLE_EXIT"
        await session.flush()

        async def _claim_proof_stub(*, db, proof_id):
            return proof

        monkeypatch.setattr(worker, "claim_controlled_proof_by_id", _claim_proof_stub)

        async def _not_achieved(*, db, request):
            return automatic_package_executor.AutomaticPackageExecutionOutcome(
                package_id=request.package_id, campaign_id=request.campaign_id, campaign_version=request.campaign_version,
                decision_record_id=request.decision_record_id, mandate_id=None, authorization_state="UNKNOWN",
                dry_run_state="UNKNOWN", activation_state="NOT_ACTIVATED", authority_source=None,
                replayed=False, final_reason_code="automatic_activation_mandate_scope_mismatch",
                failed_closed=True, starting_state="READY",
            )

        monkeypatch.setattr(worker, "execute_automatic_ready_package_through_activation", _not_achieved)

        await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof.proof_id)

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        # get_controlled_proof_view's own pre-existing status projection is
        # untouched by this fix and may legitimately re-derive status from
        # this fixture's (deliberately minimal) evidence -- not asserted
        # on here. What this fix must guarantee: no failure_reason/audit
        # event from _record_wait/_record_block (this fix's new call) ever
        # reaches the ordinary, non-recovery proof.
        assert refreshed_proof.failure_reason is None
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_run", AuditLog.entity_id == proof.proof_id,
            AuditLog.action.in_(("controlled_proof_run.waiting", "controlled_proof_run.blocked")),
        ))).all()
        assert audits == []
