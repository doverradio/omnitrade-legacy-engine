from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_run import ControlledProofRun
from app.services.orchestration import automatic_package_executor as executor
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [CanonicalPreviewPackage.__table__, ControlledProofRun.__table__]


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
    return proof


def _mandate_ids() -> dict:
    return {"mandate_id": uuid.uuid4(), "mandate_version_id": uuid.uuid4(), "mandate_evaluation_id": uuid.uuid4()}


# --- _authorize_controlled_proof_activation_override -----------------------------------

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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
        assert authority is not None
        assert authority.proof_id == proof.proof_id


@pytest.mark.asyncio
async def test_override_blocked_when_no_controlled_proof_linkage() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=package.decision_record_id, package_id=package.package_id,
        )
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
        assert authority is None


@pytest.mark.asyncio
async def test_override_blocked_when_sell_package_already_has_live_capital_evidence() -> None:
    async with _real_session() as session:
        campaign_id, campaign_version = uuid.uuid4(), 1
        sell_package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_state="DRY_RUN_PASSED", side="SELL", **_mandate_ids(),
        )
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_id=uuid.uuid4(), sell_package_id=sell_package.package_id,
            buy_live_crypto_order_id=uuid.uuid4(), position_id="pos-1",
            sell_live_crypto_order_id=uuid.uuid4(),
        )
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=campaign_id, campaign_version=campaign_version,
            decision_record_id=sell_package.decision_record_id, package_id=sell_package.package_id,
        )
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
        assert authority is None


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
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
        assert authority is not None


@pytest.mark.asyncio
async def test_override_blocked_when_request_has_no_package_id() -> None:
    async with _real_session() as session:
        request = executor.AutomaticPackageExecutionRequest(
            campaign_id=uuid.uuid4(), campaign_version=1, decision_record_id=uuid.uuid4(), package_id=None,
        )
        authority = await executor._authorize_controlled_proof_activation_override(db=session, request=request)
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
async def test_full_executor_progresses_controlled_proof_package_through_activation_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
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

        assert calls == ["authorize", "dry_run", "activate"]
        assert outcome.activation_state == "ACTIVATED"
        assert outcome.final_reason_code == "activated_under_mandate"
        assert outcome.failed_closed is False


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
