from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
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
        scalar=AsyncMock(side_effect=[package, None, activation, runtime, mandate, version, None, None, None, 0, uuid4(), claim]),
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
