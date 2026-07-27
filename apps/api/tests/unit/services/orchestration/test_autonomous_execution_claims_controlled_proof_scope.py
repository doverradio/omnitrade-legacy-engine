from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_run import ControlledProofRun
from app.services.orchestration import autonomous_execution_claims as subject
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [CanonicalPreviewPackage.__table__, ControlledProofRun.__table__]

_AUTO = object()  # sentinel: "generate a real value" -- distinct from an explicitly-passed None


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _make_package(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int, package_id: uuid.UUID | None = None,
    product: str = "BTC-USD", provider: str = "kraken_spot", environment: str = "production",
    package_state: str = "ACTIVATED", side: str = "BUY",
    mandate_id: uuid.UUID | None = None, mandate_version_id: uuid.UUID | None = None,
    mandate_evaluation_id: uuid.UUID | None = None, dry_run_live_crypto_order_id: object = _AUTO,
    authorization_source: str | None = "MANDATE",
) -> CanonicalPreviewPackage:
    package = CanonicalPreviewPackage(
        package_id=package_id or uuid.uuid4(),
        campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), live_trading_profile_id=uuid.uuid4(),
        provider=provider, environment=environment, product=product, side=side,
        proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(), preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        package_state=package_state, generated_at=datetime.now(timezone.utc),
        idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
        mandate_id=mandate_id, mandate_version_id=mandate_version_id, mandate_evaluation_id=mandate_evaluation_id,
        authorization_source=authorization_source,
        dry_run_live_crypto_order_id=uuid.uuid4() if dry_run_live_crypto_order_id is _AUTO else dry_run_live_crypto_order_id,
    )
    db.add(package)
    await db.flush()
    return package


async def _make_proof(
    *, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int, package_id: uuid.UUID,
    sell_package_id: uuid.UUID | None = None,
    product_id: str = "BTC-USD", provider: str = "kraken_spot", environment: str = "production",
    status: str = "PACKAGE_CREATED", expires_at: datetime | None = None,
) -> ControlledProofRun:
    proof = ControlledProofRun(
        proof_id=uuid.uuid4(), status=status, provider=provider, environment=environment,
        campaign_id=campaign_id, campaign_version=campaign_version, product_id=product_id,
        max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(minutes=30)),
        package_id=package_id, sell_package_id=sell_package_id,
    )
    db.add(proof)
    await db.flush()
    return proof


def _mandate_ids() -> dict:
    return {"mandate_id": uuid.uuid4(), "mandate_version_id": uuid.uuid4(), "mandate_evaluation_id": uuid.uuid4()}


# --- _resolve_autonomous_execution_scope: linkage routing -----------------------------

@pytest.mark.asyncio
async def test_package_without_controlled_proof_linkage_falls_back_to_configured_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **ids)
        monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
            automatic_mandate_package_activation_campaign_id=campaign_id,
            automatic_mandate_package_activation_campaign_version=campaign_version,
            automatic_mandate_package_activation_mandate_id=ids["mandate_id"],
            automatic_mandate_package_activation_mandate_version_id=ids["mandate_version_id"],
        ))
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert blocker is None
        assert scope is not None
        assert scope.authority_mode == "CONFIGURED_AUTOMATIC_SCOPE"
        assert scope.controlled_proof_id is None


@pytest.mark.asyncio
async def test_ordinary_package_with_incomplete_configured_scope_remains_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: ordinary automation keeps the byte-for-byte-unchanged
    configured_scope_mismatch behavior -- this fix must not weaken it."""
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
            automatic_mandate_package_activation_campaign_id=campaign_id,
            automatic_mandate_package_activation_campaign_version=None,
            automatic_mandate_package_activation_mandate_id=None,
            automatic_mandate_package_activation_mandate_version_id=None,
        ))
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "configured_scope_mismatch"


@pytest.mark.asyncio
async def test_controlled_proof_package_resolves_scope_despite_partial_configured_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact production regression: campaign_version/mandate_
    version_id unset globally must never block a genuinely Controlled-Proof-
    linked package."""
    campaign_id, campaign_version = uuid.uuid4(), 3
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **ids)
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
            automatic_mandate_package_activation_campaign_id=campaign_id,
            automatic_mandate_package_activation_campaign_version=None,
            automatic_mandate_package_activation_mandate_id=ids["mandate_id"],
            automatic_mandate_package_activation_mandate_version_id=None,
        ))
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert blocker is None
        assert scope is not None
        assert scope.authority_mode == "CONTROLLED_PROOF_DERIVED_SCOPE"
        assert scope.package_id == package.package_id
        assert scope.campaign_id == campaign_id
        assert scope.campaign_version == campaign_version
        assert scope.mandate_id == ids["mandate_id"]
        assert scope.mandate_version_id == ids["mandate_version_id"]
        assert scope.mandate_evaluation_id == ids["mandate_evaluation_id"]


@pytest.mark.asyncio
async def test_controlled_proof_package_not_redirected_by_unrelated_configured_package_or_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    ids = _mandate_ids()
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **ids)
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        # Fully configured, but for a completely different campaign/mandate --
        # must be irrelevant to a genuinely Controlled-Proof-linked package.
        monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
            automatic_mandate_package_activation_campaign_id=uuid.uuid4(),
            automatic_mandate_package_activation_campaign_version=999,
            automatic_mandate_package_activation_mandate_id=uuid.uuid4(),
            automatic_mandate_package_activation_mandate_version_id=uuid.uuid4(),
        ))
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert blocker is None
        assert scope is not None
        assert scope.authority_mode == "CONTROLLED_PROOF_DERIVED_SCOPE"
        assert scope.package_id == package.package_id


@pytest.mark.asyncio
async def test_controlled_proof_package_blocked_when_campaign_version_mismatched() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=2, package_id=package.package_id)
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "campaign_version_mismatch"


@pytest.mark.asyncio
async def test_controlled_proof_package_blocked_when_product_mismatched() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, product="ETH-USD", **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id, product_id="BTC-USD")
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "proof_package_product_mismatch"


@pytest.mark.asyncio
async def test_controlled_proof_package_blocked_when_provider_mismatched() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, provider="coinbase", **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id, provider="kraken_spot")
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "proof_package_provider_mismatch"


@pytest.mark.asyncio
async def test_controlled_proof_package_blocked_when_environment_not_production() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, environment="sandbox", **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id, environment="production")
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "proof_package_environment_mismatch"


@pytest.mark.asyncio
async def test_controlled_proof_sell_package_resolves_the_same_execution_scope() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        buy_package_id = uuid.uuid4()
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, side="SELL", **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            package_id=buy_package_id, sell_package_id=package.package_id,
            status="WAITING_FOR_PROFITABLE_EXIT",
        )
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert blocker is None
        assert scope is not None
        assert scope.package_id == package.package_id
        assert scope.authority_mode == "CONTROLLED_PROOF_DERIVED_SCOPE"


@pytest.mark.asyncio
async def test_controlled_proof_package_not_yet_activated_remains_blocked() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_state="DRY_RUN_PASSED", **_mandate_ids())
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "package_not_activated"


@pytest.mark.asyncio
async def test_controlled_proof_package_missing_mandate_version_id_remains_blocked() -> None:
    """Defense-in-depth: an ACTIVATED, authorization_source='MANDATE' package
    can never actually have a null mandate_version_id in a real database --
    ck_cpp_authorization_evidence already forbids that combination at the
    schema level -- so this is exercised directly against pure, duck-typed
    objects rather than a real insert, to prove the resolver's own defensive
    check still fails closed if that DB invariant were ever violated."""
    campaign_id, campaign_version = uuid.uuid4(), 1
    ids = _mandate_ids()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        product="BTC-USD", provider="kraken_spot", environment="production", side="BUY",
        package_state="ACTIVATED", authorization_source="MANDATE",
        mandate_id=ids["mandate_id"], mandate_version_id=None, mandate_evaluation_id=ids["mandate_evaluation_id"],
        dry_run_live_crypto_order_id=uuid.uuid4(), decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(),
    )
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="PACKAGE_CREATED", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        provider="kraken_spot", environment="production",
    )
    scope, blocker = await subject._resolve_controlled_proof_execution_scope(db=None, package=package, proof=proof)
    assert scope is None
    assert blocker == "missing_mandate_identity"


@pytest.mark.asyncio
async def test_controlled_proof_package_missing_dry_run_evidence_remains_blocked() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version,
            dry_run_live_crypto_order_id=None, **_mandate_ids(),
        )
        await _make_proof(db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id)
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "dry_run_evidence_missing"


@pytest.mark.asyncio
async def test_controlled_proof_package_missing_activation_authority_source_remains_blocked() -> None:
    """A HUMAN-authorized package can never legitimately hold Controlled
    Proof (MANDATE-only) execution scope -- exercised against pure,
    duck-typed objects for the same schema-invariant reason as the
    missing-mandate-version-id test above."""
    campaign_id, campaign_version = uuid.uuid4(), 1
    package = SimpleNamespace(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        product="BTC-USD", provider="kraken_spot", environment="production", side="BUY",
        package_state="ACTIVATED", authorization_source="HUMAN",
        mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        dry_run_live_crypto_order_id=uuid.uuid4(), decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(),
    )
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="PACKAGE_CREATED", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        provider="kraken_spot", environment="production",
    )
    scope, blocker = await subject._resolve_controlled_proof_execution_scope(db=None, package=package, proof=proof)
    assert scope is None
    assert blocker == "package_authority_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["CANCELLED", "EXPIRED", "BLOCKED", "FAILED"])
async def test_controlled_proof_package_blocked_when_proof_not_active(terminal_status: str) -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            status=terminal_status,
        )
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "controlled_proof_not_active"


@pytest.mark.asyncio
async def test_controlled_proof_package_blocked_when_proof_expired() -> None:
    campaign_id, campaign_version = uuid.uuid4(), 1
    async with _real_session() as session:
        package = await _make_package(db=session, campaign_id=campaign_id, campaign_version=campaign_version, **_mandate_ids())
        await _make_proof(
            db=session, campaign_id=campaign_id, campaign_version=campaign_version, package_id=package.package_id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        scope, blocker = await subject._resolve_autonomous_execution_scope(db=session, package=package)
        assert scope is None
        assert blocker == "controlled_proof_expired"


# --- claim_activated_package: end-to-end via the existing mock convention -------------

def _full_package(now: datetime, **overrides) -> SimpleNamespace:
    base = dict(
        package_id=uuid.uuid4(), package_state="ACTIVATED", side="BUY", preview_expires_at=now + timedelta(minutes=5),
        superseded_at=None, authorization_source="MANDATE", mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        mandate_evaluation_id=uuid.uuid4(), campaign_id=uuid.uuid4(), campaign_version=1, paper_account_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), provider="kraken_spot", environment="production", product="BTC-USD",
        runtime_campaign_id=uuid.uuid4(), market_evidence_identity={"exchange_connection_id": str(uuid.uuid4())},
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), dry_run_live_crypto_order_id=uuid.uuid4(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_full_claim_creates_exactly_one_claim_for_controlled_proof_package_despite_conflicting_global_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    package = _full_package(now)
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="PACKAGE_CREATED", expires_at=now + timedelta(minutes=30),
        campaign_id=package.campaign_id, campaign_version=package.campaign_version, product_id=package.product,
        provider=package.provider, environment=package.environment, package_id=package.package_id,
    )
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
    claim = SimpleNamespace(claim_id=uuid.uuid4(), package_id=package.package_id, claim_status="CLAIMED")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[package, None, proof, activation, runtime, mandate, version, None, None, None, 0, uuid.uuid4(), claim]),
        add=Mock(), flush=AsyncMock(),
    )
    # A global selector pointing at a completely unrelated campaign/mandate --
    # must never redirect or block the Controlled-Proof-linked package.
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=uuid.uuid4(),
        automatic_mandate_package_activation_campaign_version=999,
        automatic_mandate_package_activation_mandate_id=uuid.uuid4(),
        automatic_mandate_package_activation_mandate_version_id=uuid.uuid4(),
    ))

    outcome = await subject.claim_activated_package(db=db, package_id=package.package_id, claim_owner="worker:test", now=now)

    assert outcome.created
    assert outcome.claim is claim
    assert outcome.reason_code == "claimed"


@pytest.mark.asyncio
async def test_full_claim_still_blocks_ordinary_package_on_configured_scope_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    package = _full_package(now)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, None, None]))
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=package.campaign_id,
        automatic_mandate_package_activation_campaign_version=None,
        automatic_mandate_package_activation_mandate_id=None,
        automatic_mandate_package_activation_mandate_version_id=None,
    ))

    outcome = await subject.claim_activated_package(db=db, package_id=package.package_id, now=now)

    assert outcome.claim is None
    assert outcome.reason_code == "configured_scope_mismatch"
