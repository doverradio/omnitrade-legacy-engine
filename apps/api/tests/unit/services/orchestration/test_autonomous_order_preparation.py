from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.services.orchestration import autonomous_order_preparation as subject
from app.services.orchestration.autonomous_order_preparation import prepare_autonomous_claimed_buy


def _evidence():
    now = datetime.now(timezone.utc)
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), activation_id=uuid4(), claim_status="CLAIMED",
        campaign_id=uuid4(), campaign_version=1, mandate_id=uuid4(), mandate_version_id=uuid4(),
        account_id=uuid4(), profile_id=uuid4(), connection_id=uuid4(), provider="kraken_spot",
        environment="production", product="BTC-USD", live_order_id=None, claim_owner="worker:test",
        last_error_code=None, updated_at=now,
    )
    order_id = uuid4()
    package = SimpleNamespace(
        package_id=claim.package_id, package_state="ACTIVATED", side="BUY", preview_expires_at=now + timedelta(minutes=5),
        campaign_id=claim.campaign_id, campaign_version=1, mandate_id=claim.mandate_id,
        mandate_version_id=claim.mandate_version_id, paper_account_id=claim.account_id,
        live_trading_profile_id=claim.profile_id, provider=claim.provider, environment=claim.environment,
        product=claim.product, risk_event_id=uuid4(), dry_run_live_crypto_order_id=order_id,
        crypto_order_preview_id=uuid4(),
        risk_approved_amount=5, input_fingerprint="persisted-package-fingerprint",
    )
    activation = SimpleNamespace(
        activation_id=claim.activation_id, package_id=claim.package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
        campaign_id=claim.campaign_id, campaign_version=1, paper_account_id=claim.account_id,
        live_trading_profile_id=claim.profile_id, provider=claim.provider, environment=claim.environment, product=claim.product,
    )
    risk = SimpleNamespace(paper_account_id=claim.account_id)
    order = SimpleNamespace(
        live_crypto_order_id=order_id, crypto_order_preview_id=package.crypto_order_preview_id,
        exchange_connection_id=claim.connection_id, provider=claim.provider, environment=claim.environment,
        product_id=claim.product, side="BUY", status="DRY_RUN_READY", safe_provider_response={}, updated_at=now,
    )
    return now, claim, package, activation, risk, order


def _db(claim, package, activation, risk, order):
    return SimpleNamespace(
        scalar=AsyncMock(side_effect=[claim, package, activation, risk, None, None, 0, order]),
        add=Mock(), flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_submission_off_foundation_prepares_exactly_one_canonical_order() -> None:
    now, claim, package, activation, risk, order = _evidence()
    db = _db(claim, package, activation, risk, order)
    result = await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert result.order is order
    assert order.status == "PENDING_CONFIRMATION"
    assert order.safe_provider_response["provider_call_made"] is False
    assert order.safe_provider_response["autonomous_execution_claim_id"] == str(claim.claim_id)
    assert claim.live_order_id == order.live_crypto_order_id
    assert claim.claim_status == "EXECUTION_STARTED"


@pytest.mark.asyncio
async def test_restart_reuses_same_order_without_mutation() -> None:
    now, claim, package, activation, risk, order = _evidence()
    claim.live_order_id = order.live_crypto_order_id
    claim.claim_status = "SAFETY_DISABLED"
    order.status = "PENDING_CONFIRMATION"
    order.safe_provider_response = {
        "autonomous_execution_claim_id": str(claim.claim_id), "autonomous_prepared": True,
    }
    db = _db(claim, package, activation, risk, order)
    result = await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert result.replayed
    assert result.order.live_crypto_order_id == claim.live_order_id
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_mismatch_fails_closed() -> None:
    now, claim, package, activation, risk, order = _evidence()
    package.provider = "coinbase_advanced"
    with pytest.raises(InvalidRequestError) as exc:
        await prepare_autonomous_claimed_buy(db=_db(claim, package, activation, risk, order), claim_id=claim.claim_id, now=now)
    assert exc.value.details["blocker"] == "claim_package_identity_mismatch"


@pytest.mark.asyncio
async def test_missing_risk_evidence_fails_closed() -> None:
    now, claim, package, activation, _risk, order = _evidence()
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[claim, package, activation, None]), add=Mock(), flush=AsyncMock())
    with pytest.raises(InvalidRequestError) as exc:
        await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert exc.value.details["blocker"] == "authoritative_risk_evidence_missing"


@pytest.mark.asyncio
async def test_enabled_execution_uses_persisted_exact_package_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _now, claim, package, activation, _risk, order = _evidence()
    prepared = subject.AutonomousOrderPreparationResult(claim=claim, order=order, replayed=False)
    preview = SimpleNamespace(estimated_average_price=100, estimated_base_size=0.05)
    account = SimpleNamespace(current_cash_balance=25)
    asset = SimpleNamespace(id=uuid4(), min_order_notional=1, qty_step_size=0.00001, supports_fractional=True)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, activation, preview, account, asset]))
    monkeypatch.setattr(subject, "resolve_effective_risk_policy", AsyncMock(return_value=SimpleNamespace(max_position_size_pct=1)))
    execute = AsyncMock(return_value=SimpleNamespace(current_state="BUY_RECONCILIATION_PENDING"))
    monkeypatch.setattr(subject, "execute_activated_commissioned_entry", execute)
    await subject.execute_prepared_autonomous_claim(db=db, prepared=prepared)
    kwargs = execute.await_args.kwargs
    assert kwargs["db"] is db
    assert kwargs["package_id"] == claim.package_id
    assert kwargs["request"].live_crypto_order_id == order.live_crypto_order_id
    assert kwargs["request"].confirmation_challenge_id is None
