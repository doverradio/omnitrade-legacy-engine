from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.services.live_crypto_orders import _validate_autonomous_one_shot_submission


def _case():
    now = datetime.now(timezone.utc)
    campaign_uuid, package_id, claim_id, activation_id = uuid4(), uuid4(), uuid4(), uuid4()
    account_id, profile_id, connection_id, mandate_id, version_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    preview = SimpleNamespace(crypto_order_preview_id=uuid4())
    order = SimpleNamespace(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=connection_id, provider="kraken_spot", environment="production",
        product_id="BTC-USD", side="BUY", requested_quote_size=5,
        safe_provider_response={"autonomous_execution_claim_id": str(claim_id), "canonical_preview_package_id": str(package_id)},
    )
    claim = SimpleNamespace(
        claim_id=claim_id, package_id=package_id, activation_id=activation_id, live_order_id=order.live_crypto_order_id,
        claim_status="SAFETY_DISABLED", campaign_id=campaign_uuid, campaign_version=1,
        mandate_id=mandate_id, mandate_version_id=version_id, account_id=account_id, profile_id=profile_id,
        connection_id=connection_id, provider="kraken_spot", environment="production", product="BTC-USD", side="BUY",
    )
    package = SimpleNamespace(
        package_id=package_id, crypto_order_preview_id=preview.crypto_order_preview_id, campaign_id=campaign_uuid,
        campaign_version=1, mandate_id=mandate_id, mandate_version_id=version_id, paper_account_id=account_id,
        live_trading_profile_id=profile_id, package_state="ACTIVATED", side="BUY", authorization_source="MANDATE",
        risk_approved_amount=5,
    )
    activation = SimpleNamespace(
        activation_id=activation_id, package_id=package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5), max_order_amount=5,
    )
    mandate = SimpleNamespace(mandate_id=mandate_id, status="ACTIVE", expires_at=now + timedelta(days=1))
    version = SimpleNamespace(mandate_id=mandate_id, is_active=True, is_authorized=True)
    campaign = SimpleNamespace(id=17, status="RUNNING")
    return order, preview, claim, package, activation, mandate, version, campaign


@pytest.mark.asyncio
async def test_one_shot_allows_one_exact_bounded_claim() -> None:
    order, preview, claim, package, activation, mandate, version, campaign = _case()
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[claim, package, activation, mandate, version, None, campaign]))
    campaign_id, result = await _validate_autonomous_one_shot_submission(db=db, live_order=order, preview=preview)
    assert campaign_id == 17
    assert result is claim


@pytest.mark.asyncio
async def test_consumed_claim_blocks_restart_and_repeat_submission() -> None:
    order, preview, claim, package, _activation, _mandate, _version, _campaign = _case()
    claim.claim_status = "SUBMISSION_PENDING"
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[claim, package]))
    with pytest.raises(PermissionError, match="consumed or blocked"):
        await _validate_autonomous_one_shot_submission(db=db, live_order=order, preview=preview)


@pytest.mark.asyncio
async def test_stale_activation_fails_closed() -> None:
    order, preview, claim, package, activation, _mandate, _version, _campaign = _case()
    activation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[claim, package, activation]))
    with pytest.raises(PermissionError, match="activation inactive"):
        await _validate_autonomous_one_shot_submission(db=db, live_order=order, preview=preview)


def test_database_enforces_at_most_one_active_claim_per_campaign_version() -> None:
    """Superseded by the partial unique index uq_aec_active_campaign_scope
    (migration 20260727_0053): the original UNIQUE(campaign_id,
    campaign_version) allowed at most one AutonomousExecutionClaim row EVER
    for a given campaign version regardless of status, which permanently
    blocked every legitimate sequential Controlled Proof after the first.
    The correct, narrower invariant -- at most one claim whose
    provider-submission outcome is not yet resolved per campaign version --
    is now enforced by a unique partial index instead."""
    indexes = {index.name: index for index in AutonomousExecutionClaim.__table__.indexes}
    scope_index = indexes["uq_aec_active_campaign_scope"]
    assert scope_index.unique is True
    assert tuple(column.name for column in scope_index.columns) == ("campaign_id", "campaign_version")
    assert "RECONCILIATION_REQUIRED" in str(scope_index.dialect_options["postgresql"]["where"])
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AutonomousExecutionClaim.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("campaign_id", "campaign_version") not in unique_columns
