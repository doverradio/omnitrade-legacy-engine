from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.audit_log import AuditLog
from app.models.controlled_proof_run import ControlledProofRun
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration import automatic_package_executor as executor
from app.services.controlled_proof import service as proof_service
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [
    CanonicalPreviewPackage.__table__, ControlledProofRun.__table__, ControlledProofExitRecovery.__table__,
    AutonomousExecutionClaim.__table__, LiveCryptoOrder.__table__, AuditLog.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _make_package(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int, package_id: uuid.UUID | None = None,
    product: str = "BTC-USD", provider: str = "kraken_spot", environment: str = "production",
    package_state: str = "READY", risk_approved_amount: Decimal = Decimal("5"), side: str = "BUY",
    mandate_id: uuid.UUID | None = None, mandate_version_id: uuid.UUID | None = None,
    mandate_evaluation_id: uuid.UUID | None = None,
    decision_record_id: uuid.UUID | None = None, risk_event_id: uuid.UUID | None = None,
) -> CanonicalPreviewPackage:
    package = CanonicalPreviewPackage(
        package_id=package_id or uuid.uuid4(),
        campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), live_trading_profile_id=uuid.uuid4(),
        provider=provider, environment=environment, product=product, side=side,
        proposed_order_amount=Decimal("5"), risk_approved_amount=risk_approved_amount,
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=decision_record_id or uuid.uuid4(), risk_event_id=risk_event_id or uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(), preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        package_state=package_state, generated_at=datetime.now(timezone.utc),
        idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
        mandate_id=mandate_id, mandate_version_id=mandate_version_id, mandate_evaluation_id=mandate_evaluation_id,
        authorization_source=None if package_state == "READY" else "MANDATE",
        dry_run_live_crypto_order_id=uuid.uuid4() if package_state in {"DRY_RUN_PASSED", "ACTIVATED"} else None,
    )
    db.add(package)
    await db.flush()
    return package


async def _make_proof(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int,
    package_id: uuid.UUID | None = None, sell_package_id: uuid.UUID | None = None,
    product_id: str = "BTC-USD", provider: str = "kraken_spot", environment: str = "production",
    status: str = "PACKAGE_CREATED", max_notional_usd: Decimal = Decimal("5"),
    expires_at: datetime | None = None, buy_live_crypto_order_id: uuid.UUID | None = None,
    sell_live_crypto_order_id: uuid.UUID | None = None, position_id: str | None = None,
) -> ControlledProofRun:
    proof = ControlledProofRun(
        proof_id=uuid.uuid4(), status=status, provider=provider, environment=environment,
        campaign_id=campaign_id, campaign_version=campaign_version, product_id=product_id,
        max_notional_usd=max_notional_usd, idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(minutes=30)),
        package_id=package_id, sell_package_id=sell_package_id,
        buy_live_crypto_order_id=buy_live_crypto_order_id, sell_live_crypto_order_id=sell_live_crypto_order_id,
        position_id=position_id,
    )
    db.add(proof)
    await db.flush()
    for linked_package_id in (package_id, sell_package_id):
        if linked_package_id is None:
            continue
        linked_package = await db.get(CanonicalPreviewPackage, linked_package_id)
        if linked_package is not None:
            linked_package.market_evidence_identity = {
                **(linked_package.market_evidence_identity or {}),
                "controlled_proof_id": str(proof.proof_id),
            }
    await db.flush()
    return proof


def _mandate_ids() -> dict:
    return {"mandate_id": uuid.uuid4(), "mandate_version_id": uuid.uuid4(), "mandate_evaluation_id": uuid.uuid4()}


async def _make_claim(
    *, db: AsyncSession, proof: ControlledProofRun, package: CanonicalPreviewPackage,
    with_order: bool = True, claim_status: str = "SUBMISSION_PENDING", side: str = "SELL",
) -> tuple[AutonomousExecutionClaim, LiveCryptoOrder | None]:
    order = None
    if with_order:
        order = LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=package.crypto_order_preview_id,
            exchange_connection_id=uuid.uuid4(), provider=proof.provider, environment=proof.environment,
            product_id=proof.product_id, side=side, order_type="MARKET", requested_quote_size=Decimal("5"),
            client_order_id=f"{side.lower()}-{uuid.uuid4()}", status="ACKNOWLEDGED",
            provider_order_id=f"provider-{uuid.uuid4()}", submitted_at=datetime.now(timezone.utc),
            audit_correlation_id=proof.audit_correlation_id,
        )
        db.add(order)
        await db.flush()
    claim = AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=uuid.uuid4(),
        campaign_id=proof.campaign_id, campaign_version=proof.campaign_version,
        mandate_id=package.mandate_id, mandate_version_id=package.mandate_version_id,
        account_id=package.paper_account_id, profile_id=package.live_trading_profile_id,
        connection_id=uuid.uuid4(), provider=proof.provider, environment=proof.environment,
        product=proof.product_id, side=side, claim_status=claim_status,
        claimed_at=datetime.now(timezone.utc), claim_owner="test",
        live_order_id=None if order is None else order.live_crypto_order_id,
    )
    db.add(claim)
    await db.flush()
    return claim, order


@pytest.mark.asyncio
async def test_terminal_proof_sell_activation_requires_active_exit_recovery() -> None:
    campaign_id = uuid.uuid4()
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **ids)
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=1, package_id=uuid.uuid4(), sell_package_id=package.package_id, status="EXPIRED")
        request = executor.AutomaticPackageExecutionRequest(campaign_id=campaign_id, campaign_version=1, decision_record_id=package.decision_record_id, package_id=package.package_id)
        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None
        recovery = ControlledProofExitRecovery(
            proof_id=proof.proof_id, status="IN_PROGRESS", idempotency_key="activation-recovery",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        session.add(recovery)
        await session.flush()
        # Confirmed production defect: this package predates the recovery
        # entirely (market_evidence_identity carries no
        # controlled_proof_exit_recovery_id stamp at all -- exactly the
        # shape of a SELL package that reached PACKAGE_ONLY under ordinary
        # WAITING_FOR_PROFITABLE_EXIT before the proof expired, then was
        # resumed via authorize_controlled_proof_exit_recovery's
        # allow_existing_sell_package contract). A claimed, unexpired
        # recovery for this exact proof and its exact linked SELL package
        # must still authorize activation -- the package's own stamp is not
        # part of the eligibility binding.
        scope = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert scope is not None and scope.controlled_proof_id == proof.proof_id
        assert scope.authority_mode == "CONTROLLED_PROOF_DERIVED_SCOPE"


@pytest.mark.asyncio
async def test_exit_recovery_activation_ignores_package_stamped_with_a_different_terminal_recovery() -> None:
    """A SELL package stamped with an EARLIER, now-terminal recovery's id
    for this exact same proof (the package/recovery-reissue shape) must
    still be resumable by a freshly authorized, claimed recovery for that
    same proof -- "the later authority may resume only that package" per
    docs/CONTROLLED_PROOF_ACTIVATION.md."""
    campaign_id = uuid.uuid4()
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **ids)
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=1, package_id=uuid.uuid4(), sell_package_id=package.package_id, status="EXPIRED")
        package.market_evidence_identity = {
            **(package.market_evidence_identity or {}), "controlled_proof_exit_recovery_id": str(uuid.uuid4()),
        }
        await session.flush()
        new_recovery = ControlledProofExitRecovery(
            proof_id=proof.proof_id, status="IN_PROGRESS", idempotency_key="fresh-recovery-attempt",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        session.add(new_recovery)
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )

        scope = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)

        assert scope is not None
        assert scope.controlled_proof_id == proof.proof_id


@pytest.mark.asyncio
async def test_exit_recovery_activation_fails_closed_when_recovery_is_unclaimed() -> None:
    """An AUTHORIZED-but-not-yet-claimed recovery (claim_exit_recovery_by_id
    has not transitioned it to IN_PROGRESS) must not authorize activation."""
    campaign_id = uuid.uuid4()
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **ids)
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=1, package_id=uuid.uuid4(), sell_package_id=package.package_id, status="EXPIRED")
        session.add(ControlledProofExitRecovery(
            proof_id=proof.proof_id, status="AUTHORIZED", idempotency_key="unclaimed-recovery",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ))
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None


@pytest.mark.asyncio
async def test_exit_recovery_activation_fails_closed_when_recovery_is_expired() -> None:
    campaign_id = uuid.uuid4()
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **ids)
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=1, package_id=uuid.uuid4(), sell_package_id=package.package_id, status="EXPIRED")
        session.add(ControlledProofExitRecovery(
            proof_id=proof.proof_id, status="IN_PROGRESS", idempotency_key="expired-recovery",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc) - timedelta(minutes=40),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        ))
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None


@pytest.mark.asyncio
async def test_exit_recovery_activation_ignores_unrelated_proofs_recovery() -> None:
    """A claimed, unexpired recovery that belongs to a DIFFERENT proof must
    never authorize this proof's package -- eligibility is scoped by
    ControlledProofExitRecovery.proof_id, not merely "any active recovery
    exists somewhere"."""
    campaign_id = uuid.uuid4()
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **ids)
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=1, package_id=uuid.uuid4(), sell_package_id=package.package_id, status="EXPIRED")
        other_package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **_mandate_ids())
        other_proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=uuid.uuid4(), sell_package_id=other_package.package_id, status="EXPIRED",
        )
        session.add(ControlledProofExitRecovery(
            proof_id=other_proof.proof_id, status="IN_PROGRESS", idempotency_key="unrelated-proof-recovery",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ))
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None


# --- _resolve_controlled_proof_activation_scope -----------------------------------

@pytest.mark.asyncio
async def test_override_allowed_when_all_invariants_pass() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        proof = await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)

        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        scope = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert scope is not None
        assert scope.controlled_proof_id == proof.proof_id
        assert scope.package_id == package.package_id
        assert scope.campaign_id == campaign_id
        assert scope.campaign_version == campaign_version
        assert scope.authority_mode == "CONTROLLED_PROOF_DERIVED_SCOPE"


@pytest.mark.asyncio
async def test_override_blocked_when_no_controlled_proof_linkage() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_product_mismatched() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, product="ETH-USD", **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id, product_id="BTC-USD")
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_campaign_scope_mismatched() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=2, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_proof_is_expired() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_proof_is_cancelled() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            status="CANCELLED",
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["BLOCKED", "EXPIRED", "FAILED", "EXITED", "RECONCILED", "PROFIT_CONFIRMED"])
async def test_override_blocked_for_every_non_active_proof_status(terminal_status: str) -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            status=terminal_status,
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_evidence_incomplete() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        # No mandate_id/mandate_version_id/mandate_evaluation_id -- evidence bundle incomplete.
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version)
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_only_mandate_version_id_missing() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        ids = _mandate_ids()
        ids["mandate_version_id"] = None
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **ids)
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_only_mandate_evaluation_id_missing() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        ids = _mandate_ids()
        ids["mandate_evaluation_id"] = None
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **ids)
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_decision_record_id_mismatched() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=uuid.uuid4(),  # Deliberately not package.decision_record_id.
            package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_approved_notional_exceeds_proof_maximum() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            risk_approved_amount=Decimal("5"), **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            max_notional_usd=Decimal("2"),
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_provider_or_environment_mismatched() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            provider="kraken_spot", environment="production", **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            provider="kraken_spot", environment="production",
        )
        # Corrupt just the package's environment after both rows exist, proving the
        # check compares live values rather than something assumed at creation time.
        package.environment = "sandbox"
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_buy_package_already_has_live_capital_evidence() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_state="DRY_RUN_PASSED", **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            buy_live_crypto_order_id=uuid.uuid4(),
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_sell_package_already_has_live_capital_evidence() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            buy_live_crypto_order_id=uuid.uuid4(), position_id="pos-1",
            sell_live_crypto_order_id=uuid.uuid4(),
        )
        _claim, exact_order = await _make_claim(db=session, proof=proof, package=sell_package)
        proof.sell_live_crypto_order_id = exact_order.live_crypto_order_id
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_foreign_cached_sell_order_does_not_block_first_exact_sell_and_is_audited() -> None:
    async with _real_session() as session:
        campaign_id = uuid.uuid4()
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        foreign_order_id = uuid.uuid4()
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT", sell_live_crypto_order_id=foreign_order_id,
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )

        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)

        assert authority is not None
        assert proof.sell_live_crypto_order_id is None
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_id == proof.proof_id,
            AuditLog.action == "controlled_proof_run.cached_order_lineage_repaired",
        ))).all()
        assert len(audits) == 1
        assert audits[0].before_state["sell_live_crypto_order_id"] == str(foreign_order_id)
        assert audits[0].after_state["sell_live_crypto_order_id"] is None
        assert audits[0].after_state["canonical_lineage"]["sell"]["state"] == "PACKAGE_ONLY"


@pytest.mark.asyncio
async def test_claim_only_sell_lineage_remains_blocked_and_cache_is_not_cleared() -> None:
    async with _real_session() as session:
        campaign_id = uuid.uuid4()
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        cached_id = uuid.uuid4()
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT", sell_live_crypto_order_id=cached_id,
        )
        await _make_claim(db=session, proof=proof, package=sell_package, with_order=False, claim_status="RECONCILIATION_REQUIRED")
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None
        assert proof.sell_live_crypto_order_id == cached_id


@pytest.mark.asyncio
async def test_ambiguous_multiple_claim_sell_lineage_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        campaign_id = uuid.uuid4()
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT",
        )
        async def _no_repair(**_kwargs):
            return False
        async def _ambiguous(**_kwargs):
            return SimpleNamespace(state="INCONSISTENT", reason="multiple_execution_claims")
        monkeypatch.setattr(executor, "repair_controlled_proof_cached_order_ids", _no_repair)
        monkeypatch.setattr(executor, "resolve_controlled_proof_leg_execution_lineage", _ambiguous)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("order_status", ["ACKNOWLEDGED", "RECONCILIATION_REQUIRED", "FILLED"])
async def test_submitted_unresolved_or_filled_exact_sell_lineage_blocks_activation(order_status: str) -> None:
    async with _real_session() as session:
        campaign_id = uuid.uuid4()
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT",
        )
        _claim, order = await _make_claim(db=session, proof=proof, package=sell_package)
        order.status = order_status
        await session.flush()
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=1,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )

        assert await executor._resolve_controlled_proof_activation_scope(db=session, request=request) is None


@pytest.mark.asyncio
async def test_cached_buy_and_sell_repair_is_idempotent_and_preserves_exact_buy() -> None:
    async with _real_session() as session:
        campaign_id = uuid.uuid4()
        buy_package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=1, **_mandate_ids())
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=1, side="SELL", **_mandate_ids(),
        )
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=1,
            package_id=buy_package.package_id, sell_package_id=sell_package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT", buy_live_crypto_order_id=uuid.uuid4(),
            sell_live_crypto_order_id=uuid.uuid4(),
        )
        _claim, exact_buy = await _make_claim(
            db=session, proof=proof, package=buy_package, side="BUY", claim_status="BUY_RECONCILED",
        )

        assert await proof_service.repair_controlled_proof_cached_order_ids(db=session, proof=proof) is True
        assert proof.buy_live_crypto_order_id == exact_buy.live_crypto_order_id
        assert proof.sell_live_crypto_order_id is None
        assert await proof_service.repair_controlled_proof_cached_order_ids(db=session, proof=proof) is False
        audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_id == proof.proof_id,
            AuditLog.action == "controlled_proof_run.cached_order_lineage_repaired",
        ))).all()
        assert len(audits) == 1


@pytest.mark.asyncio
async def test_override_allowed_for_already_activated_replay_despite_live_capital_evidence() -> None:
    """The duplicate-submission guard only applies to a package that has not
    yet been activated -- an ACTIVATED package's own idempotent-replay
    branch (in execute_automatic_ready_package_through_activation) is where
    replay safety is actually enforced, by re-validating mandate authority."""
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_state="ACTIVATED", **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            buy_live_crypto_order_id=uuid.uuid4(),
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is not None


@pytest.mark.asyncio
async def test_override_blocked_when_request_has_no_package_id() -> None:
    async with _real_session() as session:
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=uuid.uuid4(), campaign_version=1, decision_record_id=uuid.uuid4(), package_id=None,
        )
        authority = await executor._resolve_controlled_proof_activation_scope(db=session, request=request)
        assert authority is None


# --- full executor: flag disabled ------------------------------------------------------

def _disabled_settings() -> SimpleNamespace:
    return SimpleNamespace(
        automatic_mandate_package_activation_enabled=False,
        automatic_mandate_package_activation_package_id=None,
        automatic_mandate_package_activation_campaign_id=None,
        automatic_mandate_package_activation_campaign_version=None,
        automatic_mandate_package_activation_mandate_id=None,
        automatic_mandate_package_activation_mandate_version_id=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
async def test_full_executor_progresses_controlled_proof_package_through_activation_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, side: str,
) -> None:
    monkeypatch.setattr(executor, "get_settings", _disabled_settings)
    calls: list[str] = []

    async def _fake_activate(*, db, request):
        calls.append("activate")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "ACTIVATED"

    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)

    async def _fake_authorize(*, db, request):
        calls.append("authorize")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)

    async def _fake_dry_run(*, db, request):
        calls.append("dry_run")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()

    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY",
            side=side, **_mandate_ids(),
        )
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_id=package.package_id if side == "BUY" else uuid.uuid4(),
            sell_package_id=package.package_id if side == "SELL" else None,
            status="WAITING_FOR_PROFITABLE_EXIT" if side == "SELL" else "PACKAGE_CREATED",
            sell_live_crypto_order_id=uuid.uuid4() if side == "SELL" else None,
        )

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert calls == ["authorize", "dry_run", "activate"]
        assert outcome.activation_state == "ACTIVATED"
        assert outcome.final_reason_code == "activated_under_mandate"
        assert outcome.failed_closed is False
        # Regression: package_id/mandate_id must reflect the real,
        # already-resolved package -- never None from an early return that
        # discarded the package the override itself had already loaded.
        assert outcome.package_id == package.package_id
        assert outcome.mandate_id == package.mandate_id
        if side == "SELL":
            assert proof.sell_live_crypto_order_id is None


def _partially_configured_settings() -> SimpleNamespace:
    """The exact production shape that produced automatic_activation_scope_
    incomplete: the global boolean is off (so Controlled Proof packages
    still need the override), and the legacy global selector settings are
    only partially populated -- campaign_id/mandate_id set, campaign_version/
    mandate_version_id left None. Before the fix, execute_automatic_ready_
    package_through_activation read this settings-derived scope
    unconditionally, even for an already-authorized Controlled Proof
    package, and failed closed before ever resolving a package."""
    return SimpleNamespace(
        automatic_mandate_package_activation_enabled=False,
        automatic_mandate_package_activation_package_id=None,
        automatic_mandate_package_activation_campaign_id=uuid.uuid4(),
        automatic_mandate_package_activation_campaign_version=None,
        automatic_mandate_package_activation_mandate_id=uuid.uuid4(),
        automatic_mandate_package_activation_mandate_version_id=None,
    )


@pytest.mark.asyncio
async def test_full_executor_controlled_proof_package_activates_despite_incomplete_global_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the production regression directly: a valid Controlled
    Proof package must derive its own scope from the package/proof, never
    fall through to (or be blocked by) the legacy global selector settings
    -- even when those settings are only partially configured."""
    monkeypatch.setattr(executor, "get_settings", _partially_configured_settings)

    async def _fake_authorize(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"

    async def _fake_dry_run(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()

    async def _fake_activate(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "ACTIVATED"

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 3
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY",
            **_mandate_ids(),
        )
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert outcome.final_reason_code == "activated_under_mandate"
        assert outcome.activation_state == "ACTIVATED"
        assert outcome.failed_closed is False
        assert outcome.package_id == package.package_id
        assert outcome.mandate_id == package.mandate_id


@pytest.mark.asyncio
async def test_full_executor_conflicting_global_package_selector_cannot_redirect_controlled_proof_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A statically pinned automatic_mandate_package_activation_package_id
    (pointing at some unrelated package) must never redirect or block a
    genuinely Controlled-Proof-authorized package -- Controlled Proof scope
    resolution bypasses the global pin entirely."""
    other_pinned_package_id = uuid.uuid4()
    monkeypatch.setattr(
        executor, "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_enabled=False,
            automatic_mandate_package_activation_package_id=other_pinned_package_id,
            automatic_mandate_package_activation_campaign_id=None,
            automatic_mandate_package_activation_campaign_version=None,
            automatic_mandate_package_activation_mandate_id=None,
            automatic_mandate_package_activation_mandate_version_id=None,
        ),
    )

    async def _fake_authorize(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"

    async def _fake_dry_run(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()

    async def _fake_activate(*, db, request):
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "ACTIVATED"

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY",
            **_mandate_ids(),
        )
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert outcome.activation_state == "ACTIVATED"
        assert outcome.package_id == package.package_id


@pytest.mark.asyncio
async def test_full_executor_idempotent_replay_when_package_already_activated_and_global_scope_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "get_settings", _partially_configured_settings)

    async def _validate(**kwargs):
        return None

    monkeypatch.setattr(executor, "_validate_canonical_package_authority", _validate)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="ACTIVATED",
            **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            buy_live_crypto_order_id=uuid.uuid4(),
        )

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert outcome.final_reason_code == "already_activated"
        assert outcome.replayed is True
        assert outcome.activation_state == "ACTIVATED"
        assert outcome.package_id == package.package_id


@pytest.mark.asyncio
async def test_full_executor_sequential_progression_activates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proxy for the concurrent-progression-exactly-once requirement: the
    real race-safety mechanism is the row lock taken on both the
    ControlledProofRun and the CanonicalPreviewPackage rows, which a single
    shared sqlite connection cannot exercise as true concurrency (see this
    file's other documented sqlite-limitation notes) -- so this instead
    proves the invariant those locks exist to protect: repeated calls
    against an already-ACTIVATED package never re-run authorize/dry-run/
    activate a second time."""
    monkeypatch.setattr(executor, "get_settings", _disabled_settings)
    calls: list[str] = []

    async def _fake_authorize(*, db, request):
        calls.append("authorize")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"

    async def _fake_dry_run(*, db, request):
        calls.append("dry_run")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()

    async def _fake_activate(*, db, request):
        calls.append("activate")
        package = await db.get(CanonicalPreviewPackage, request.package_id)
        package.package_state = "ACTIVATED"

    async def _validate(**kwargs):
        return None

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)
    monkeypatch.setattr(executor, "_validate_canonical_package_authority", _validate)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY",
            **_mandate_ids(),
        )
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )

        first = await executor.execute_automatic_ready_package_through_activation(db=session, request=request)
        second = await executor.execute_automatic_ready_package_through_activation(db=session, request=request)

        assert calls == ["authorize", "dry_run", "activate"]
        assert first.activation_state == "ACTIVATED" and first.final_reason_code == "activated_under_mandate"
        assert second.activation_state == "ACTIVATED" and second.final_reason_code == "already_activated"
        assert second.replayed is True


@pytest.mark.asyncio
async def test_full_executor_still_blocks_ordinary_package_without_controlled_proof_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: ordinary automatic packages remain governed by the
    existing global feature flag, unchanged -- no Controlled Proof
    linkage means no override, regardless of how "ready" the package
    otherwise looks."""
    monkeypatch.setattr(executor, "get_settings", _disabled_settings)

    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY",
            **_mandate_ids(),
        )
        # Deliberately no ControlledProofRun row at all.

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert outcome.final_reason_code == "automatic_mandate_package_activation_disabled"
        assert outcome.activation_state == "NOT_ACTIVATED"
        refreshed = await session.get(CanonicalPreviewPackage, package.package_id)
        assert refreshed.package_state == "READY"


# --- full executor: flag enabled (ordinary autonomous production) ---------------------


def _enabled_settings_pinned_to_mismatched_mandate(*, campaign_id: uuid.UUID, campaign_version: int) -> SimpleNamespace:
    """Reproduces production exactly: AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=true
    (the documented, supported configuration for ordinary autonomous
    production -- AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_RUNBOOK.md), with the
    legacy global selector settings pinned to the *ordinary* production
    mandate. That mandate is deliberately different from
    controlled_proof_mandate_id (see config.py) so a Controlled Proof
    attempt can never resolve, and ordinary autonomous trading can never be
    governed by, the other's mandate -- exactly the mismatch that must
    never leak into a Controlled Proof package's own scope resolution."""
    return SimpleNamespace(
        automatic_mandate_package_activation_enabled=True,
        automatic_mandate_package_activation_package_id=None,
        automatic_mandate_package_activation_campaign_id=campaign_id,
        automatic_mandate_package_activation_campaign_version=campaign_version,
        automatic_mandate_package_activation_mandate_id=uuid.uuid4(),
        automatic_mandate_package_activation_mandate_version_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_full_executor_authorized_exit_recovery_activates_when_global_flag_enabled_with_mismatched_mandate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact confirmed production defect: with the global
    activation switch on and the legacy global selector pinned to the
    ordinary production mandate/campaign scope, an authorized Controlled
    Proof Exit Recovery's SELL package -- authorized under the deliberately
    separate controlled_proof_mandate_id -- must still resolve through its
    own CONTROLLED_PROOF_DERIVED_SCOPE and activate. Before the fix,
    _resolve_controlled_proof_activation_scope was never even attempted
    once the flag was on, so this package fell straight through to
    GLOBAL_CONFIGURED_SCOPE (logged as automatic_activation_scope_resolved
    authority_mode=GLOBAL_CONFIGURED_SCOPE controlled_proof_id=None) and
    failed closed on automatic_activation_mandate_scope_mismatch despite
    valid, authorized Controlled Proof authority."""
    campaign_id, campaign_version = uuid.uuid4(), 1
    ids = _mandate_ids()
    async with _real_session() as session:
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, side="SELL", **ids,
        )
        proof = await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id, status="EXPIRED",
        )
        recovery = ControlledProofExitRecovery(
            proof_id=proof.proof_id, status="IN_PROGRESS", idempotency_key="exit-recovery-enabled-flag",
            authorized_by="operator:human", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        session.add(recovery)
        await session.flush()
        sell_package.market_evidence_identity = {
            **(sell_package.market_evidence_identity or {}),
            "controlled_proof_exit_recovery_id": str(recovery.recovery_id),
        }
        await session.flush()

        monkeypatch.setattr(
            executor, "get_settings",
            lambda: _enabled_settings_pinned_to_mismatched_mandate(
                campaign_id=campaign_id, campaign_version=campaign_version,
            ),
        )

        async def _fake_authorize(*, db, request):
            package = await db.get(CanonicalPreviewPackage, request.package_id)
            package.package_state = "AUTHORIZED"
            package.authorization_source = "MANDATE"

        async def _fake_dry_run(*, db, request):
            package = await db.get(CanonicalPreviewPackage, request.package_id)
            package.package_state = "DRY_RUN_PASSED"
            package.dry_run_live_crypto_order_id = uuid.uuid4()

        async def _fake_activate(*, db, request):
            package = await db.get(CanonicalPreviewPackage, request.package_id)
            package.package_state = "ACTIVATED"

        monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
        monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
        monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
            ),
        )

        assert outcome.final_reason_code == "activated_under_mandate"
        assert outcome.activation_state == "ACTIVATED"
        assert outcome.failed_closed is False
        assert outcome.package_id == sell_package.package_id
        assert outcome.mandate_id == sell_package.mandate_id
        assert outcome.mandate_id != executor.get_settings().automatic_mandate_package_activation_mandate_id


@pytest.mark.asyncio
async def test_full_executor_still_governed_by_global_scope_when_flag_enabled_and_no_controlled_proof_linkage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement preserved: an ordinary automatic package (no Controlled
    Proof linkage at all) is completely unaffected by always attempting CP
    scope resolution first -- it still resolves via GLOBAL_CONFIGURED_SCOPE
    exactly as before, since _resolve_controlled_proof_activation_scope
    itself returns None (no_controlled_proof_linkage) regardless of the
    flag."""
    campaign_id, campaign_version = uuid.uuid4(), 1
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="READY", **ids,
        )
        # Deliberately no ControlledProofRun row at all.
        monkeypatch.setattr(
            executor, "get_settings",
            lambda: SimpleNamespace(
                automatic_mandate_package_activation_enabled=True,
                automatic_mandate_package_activation_package_id=None,
                automatic_mandate_package_activation_campaign_id=campaign_id,
                automatic_mandate_package_activation_campaign_version=campaign_version,
                automatic_mandate_package_activation_mandate_id=ids["mandate_id"],
                automatic_mandate_package_activation_mandate_version_id=ids["mandate_version_id"],
            ),
        )

        async def _fake_authorize(*, db, request):
            pkg = await db.get(CanonicalPreviewPackage, request.package_id)
            pkg.package_state = "AUTHORIZED"
            pkg.authorization_source = "MANDATE"

        async def _fake_dry_run(*, db, request):
            pkg = await db.get(CanonicalPreviewPackage, request.package_id)
            pkg.package_state = "DRY_RUN_PASSED"
            pkg.dry_run_live_crypto_order_id = uuid.uuid4()

        async def _fake_activate(*, db, request):
            pkg = await db.get(CanonicalPreviewPackage, request.package_id)
            pkg.package_state = "ACTIVATED"

        monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
        monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
        monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _fake_activate)

        outcome = await executor.execute_automatic_ready_package_through_activation(
            db=session,
            request=executor.AutomaticPackageExecutionRequest(
                campaign_id=campaign_id, campaign_version=campaign_version,
                decision_record_id=package.decision_record_id, package_id=package.package_id,
            ),
        )

        assert outcome.activation_state == "ACTIVATED"
        assert outcome.final_reason_code == "activated_under_mandate"
        assert outcome.package_id == package.package_id
