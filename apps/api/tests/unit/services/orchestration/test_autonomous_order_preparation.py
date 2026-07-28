from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.services.orchestration import autonomous_order_preparation as subject
from app.services.orchestration.autonomous_order_preparation import prepare_autonomous_claimed_buy


@pytest.fixture(autouse=True)
def _canonical_identity(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    if request.node.name == "test_identity_derivation_reuses_commissioned_preview_generator":
        return
    async def _identity(**_kwargs):
        return SimpleNamespace(), SimpleNamespace(preview_identity_hash="canonical-preview-hash")
    monkeypatch.setattr(subject, "_canonical_preview_identity", _identity)


def _evidence():
    now = datetime.now(timezone.utc)
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), activation_id=uuid4(), claim_status="CLAIMED",
        campaign_id=uuid4(), campaign_version=1, mandate_id=uuid4(), mandate_version_id=uuid4(),
        account_id=uuid4(), profile_id=uuid4(), connection_id=uuid4(), provider="kraken_spot",
        environment="production", product="BTC-USD", live_order_id=None, claim_owner="worker:test",
        side="BUY", last_error_code=None, updated_at=now,
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
        decision_record_id=uuid4(),
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
        order_type="MARKET",
    )
    preview = SimpleNamespace(estimated_base_size=0.05)
    return now, claim, package, activation, risk, order, preview


def _db(claim, package, activation, risk, order, preview):
    return SimpleNamespace(
        scalar=AsyncMock(side_effect=[claim, package, activation, risk, None, None, 0, order, preview]),
        add=Mock(), flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_submission_off_foundation_prepares_exactly_one_canonical_order() -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    db = _db(claim, package, activation, risk, order, preview)
    result = await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert result.order is order
    assert order.status == "PENDING_CONFIRMATION"
    assert order.safe_provider_response["provider_call_made"] is False
    assert order.safe_provider_response["autonomous_execution_claim_id"] == str(claim.claim_id)
    assert claim.live_order_id == order.live_crypto_order_id
    assert claim.claim_status == "EXECUTION_STARTED"
    assert order.safe_provider_response["commissioned_preview_identity_hash"] == "canonical-preview-hash"


@pytest.mark.asyncio
async def test_sell_preparation_uses_exact_controlled_proof_quantity_without_unrelated_btc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    claim.side = package.side = order.side = "SELL"
    preview.base_size = 0.05
    preview.estimated_base_size = 0.05
    preview.quote_size = None
    proof_id = uuid4()
    package.market_evidence_identity = {"controlled_proof_id": str(proof_id)}
    monkeypatch.setattr(
        subject, "compute_controlled_proof_owned_quantity", AsyncMock(return_value=subject.Decimal("0.05")),
    )
    db = SimpleNamespace(
        # Profile owns 0.06, but this proof owns only the requested 0.05.
        scalar=AsyncMock(side_effect=[claim, package, activation, risk, None, None, 0.06, order, preview]),
        add=Mock(), flush=AsyncMock(),
    )
    result = await subject.prepare_autonomous_claimed_order(db=db, claim_id=claim.claim_id, now=now)
    assert result.order is order
    assert order.status == "PENDING_CONFIRMATION"
    assert order.side == "SELL"
    assert claim.claim_status == "EXECUTION_STARTED"
    assert order.safe_provider_response["requested_base_size"] == "0.05"
    assert order.safe_provider_response["approved_base_size"] == "0.05"
    assert order.safe_provider_response["controlled_proof_id"] == str(proof_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owned", "requested", "provider_size", "quote_size", "blocker"),
    [
        (0.04, 0.05, 0.05, None, "sell_quantity_ownership_mismatch"),
        (0.05, 0.05, 0, None, "sell_quantity_evidence_missing"),
        (0.05, 0.05, 0.051, None, "sell_quantity_rounds_up"),
        (0.05, 0.05, 0.05, 5, "sell_quote_size_forbidden"),
    ],
)
async def test_sell_preparation_fails_closed_for_invalid_quantity_lineage(
    owned, requested, provider_size, quote_size, blocker,
) -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    claim.side = package.side = order.side = "SELL"
    preview.base_size = requested
    preview.estimated_base_size = provider_size
    preview.quote_size = quote_size
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[claim, package, activation, risk, None, None, owned, order, preview]),
        add=Mock(), flush=AsyncMock(),
    )

    with pytest.raises(InvalidRequestError) as raised:
        await subject.prepare_autonomous_claimed_order(db=db, claim_id=claim.claim_id, now=now)

    assert raised.value.details["blocker"] == blocker


@pytest.mark.asyncio
async def test_restart_reuses_same_order_without_mutation() -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    claim.live_order_id = order.live_crypto_order_id
    claim.claim_status = "SAFETY_DISABLED"
    order.status = "PENDING_CONFIRMATION"
    order.safe_provider_response = {
        "autonomous_execution_claim_id": str(claim.claim_id), "autonomous_prepared": True,
        "commissioned_preview_identity_hash": "canonical-preview-hash",
        "commissioned_preview_identity_binding": subject._identity_binding(
            claim=claim, package=package, activation=activation, order=order, preview=preview,
        ),
    }
    db = _db(claim, package, activation, risk, order, preview)
    result = await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert result.replayed
    assert result.order.live_crypto_order_id == claim.live_order_id
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_mismatch_fails_closed() -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    package.provider = "coinbase_advanced"
    with pytest.raises(InvalidRequestError) as exc:
        await prepare_autonomous_claimed_buy(db=_db(claim, package, activation, risk, order, preview), claim_id=claim.claim_id, now=now)
    assert exc.value.details["blocker"] == "claim_package_identity_mismatch"


@pytest.mark.asyncio
async def test_missing_risk_evidence_fails_closed() -> None:
    now, claim, package, activation, _risk, order, _preview = _evidence()
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[claim, package, activation, None]), add=Mock(), flush=AsyncMock())
    with pytest.raises(InvalidRequestError) as exc:
        await prepare_autonomous_claimed_buy(db=db, claim_id=claim.claim_id, now=now)
    assert exc.value.details["blocker"] == "authoritative_risk_evidence_missing"


@pytest.mark.asyncio
async def test_enabled_execution_uses_persisted_exact_package_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _now, claim, package, activation, _risk, order, _preview = _evidence()
    prepared = subject.AutonomousOrderPreparationResult(claim=claim, order=order, replayed=False)
    preview = SimpleNamespace(estimated_average_price=100, estimated_base_size=0.05)
    order.safe_provider_response = {
        "commissioned_preview_identity_hash": "canonical-preview-hash",
        "commissioned_preview_identity_binding": subject._identity_binding(
            claim=claim, package=package, activation=activation, order=order, preview=preview,
        ),
    }
    account = SimpleNamespace(current_cash_balance=25)
    asset = SimpleNamespace(id=uuid4(), min_order_notional=1, qty_step_size=0.00001, supports_fractional=True)
    checked_at = datetime.now(timezone.utc)
    refresh = AsyncMock(return_value=SimpleNamespace(
        exchange_connection_id=claim.connection_id, provider=claim.provider, environment=claim.environment,
        readiness=SimpleNamespace(verdict="READY_FOR_DRY_RUN", checked_at=checked_at),
    ))
    monkeypatch.setattr(subject, "refresh_exchange_balances", refresh)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, activation, preview, account, asset]), add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(subject, "resolve_effective_risk_policy", AsyncMock(return_value=SimpleNamespace(max_position_size_pct=1)))
    execute = AsyncMock(return_value=SimpleNamespace(current_state="BUY_RECONCILIATION_PENDING"))
    monkeypatch.setattr(subject, "execute_activated_commissioned_entry", execute)
    await subject.execute_prepared_autonomous_claim(db=db, prepared=prepared)
    kwargs = execute.await_args.kwargs
    assert kwargs["db"] is db
    assert kwargs["package_id"] == claim.package_id
    assert kwargs["request"].live_crypto_order_id == order.live_crypto_order_id
    assert kwargs["request"].confirmation_challenge_id is None
    assert kwargs["request"].expected_preview_identity_hash == "canonical-preview-hash"
    refresh.assert_awaited_once_with(db=db, exchange_connection_id=claim.connection_id, actor=claim.claim_owner)
    assert order.safe_provider_response["execution_readiness_evidence"]["checked_at"] == checked_at.isoformat()
    assert order.safe_provider_response["execution_readiness_evidence"]["claim_id"] == str(claim.claim_id)
    assert order.safe_provider_response["execution_readiness_evidence"]["package_id"] == str(claim.package_id)


@pytest.mark.asyncio
async def test_enabled_sell_execution_uses_identical_activated_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    _now, claim, package, activation, _risk, order, _preview = _evidence()
    claim.side = package.side = order.side = "SELL"
    prepared = subject.AutonomousOrderPreparationResult(claim=claim, order=order, replayed=False)
    preview = SimpleNamespace(estimated_average_price=100, estimated_base_size=0.05)
    order.safe_provider_response = {
        "commissioned_preview_identity_hash": "canonical-preview-hash",
        "commissioned_preview_identity_binding": subject._identity_binding(
            claim=claim, package=package, activation=activation, order=order, preview=preview,
        ),
    }
    account = SimpleNamespace(current_cash_balance=25)
    asset = SimpleNamespace(id=uuid4(), min_order_notional=1, qty_step_size=0.00001, supports_fractional=True)
    monkeypatch.setattr(subject, "refresh_exchange_balances", AsyncMock(return_value=SimpleNamespace(
        exchange_connection_id=claim.connection_id, provider=claim.provider, environment=claim.environment,
        readiness=SimpleNamespace(verdict="READY_FOR_DRY_RUN", checked_at=datetime.now(timezone.utc)),
    )))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, activation, preview, account, asset]), add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(subject, "resolve_effective_risk_policy", AsyncMock(return_value=SimpleNamespace(max_position_size_pct=1)))
    execute = AsyncMock(return_value=SimpleNamespace(current_state="SELL_RECONCILIATION_PENDING"))
    monkeypatch.setattr(subject, "execute_activated_commissioned_entry", execute)

    await subject.execute_prepared_autonomous_claim(db=db, prepared=prepared)

    request = execute.await_args.kwargs["request"]
    assert request.side == "SELL"
    assert request.canonical_package_authorized is True
    assert execute.await_args.kwargs["package_id"] == claim.package_id


@pytest.mark.asyncio
async def test_input_fingerprint_is_rejected_as_preview_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _now, claim, package, activation, _risk, order, _preview = _evidence()
    order.safe_provider_response = {"commissioned_preview_identity_hash": package.input_fingerprint}
    prepared = subject.AutonomousOrderPreparationResult(claim=claim, order=order, replayed=True)
    preview = SimpleNamespace(estimated_average_price=100, estimated_base_size=0.05)
    account = SimpleNamespace(current_cash_balance=25)
    asset = SimpleNamespace(id=uuid4(), min_order_notional=1, qty_step_size=0.00001, supports_fractional=True)
    monkeypatch.setattr(subject, "refresh_exchange_balances", AsyncMock(return_value=SimpleNamespace(
        exchange_connection_id=claim.connection_id, provider=claim.provider, environment=claim.environment,
        readiness=SimpleNamespace(verdict="READY_FOR_DRY_RUN", checked_at=datetime.now(timezone.utc)),
    )))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, activation, preview, account, asset]), add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(
        subject, "_canonical_preview_identity",
        AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(preview_identity_hash=package.input_fingerprint))),
    )
    with pytest.raises(InvalidRequestError) as exc:
        await subject.execute_prepared_autonomous_claim(db=db, prepared=prepared)
    assert exc.value.details["blocker"] == "input_fingerprint_substitution_rejected"


@pytest.mark.asyncio
async def test_historical_replayed_order_without_canonical_identity_fails_closed() -> None:
    now, claim, package, activation, risk, order, preview = _evidence()
    claim.live_order_id = order.live_crypto_order_id
    claim.claim_status = "SAFETY_DISABLED"
    order.status = "PENDING_CONFIRMATION"
    order.safe_provider_response = {"autonomous_execution_claim_id": str(claim.claim_id)}
    with pytest.raises(InvalidRequestError) as exc:
        await prepare_autonomous_claimed_buy(
            db=_db(claim, package, activation, risk, order, preview), claim_id=claim.claim_id, now=now,
        )
    assert exc.value.details["blocker"] == "canonical_preview_identity_missing"


@pytest.mark.asyncio
async def test_identity_derivation_reuses_commissioned_preview_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    now, claim, package, activation, _risk, _order, _preview = _evidence()
    definition = SimpleNamespace(risk_policy_id="risk-1", risk_policy_version="v1")
    connection = SimpleNamespace(
        last_verified_at=now, last_successful_sync_at=now, last_heartbeat_at=now,
        balances=[{"currency": "USD", "available": "25"}], credentials_valid=True, status="connected",
    )
    mandate = SimpleNamespace(mandate_id=claim.mandate_id)
    mandate_version = SimpleNamespace(version_number=3, entry_policy={"minimum_base_quantity": "0.00001"})
    preview = SimpleNamespace(
        created_at=now, estimated_average_price=100, best_ask=None, best_bid=None,
        estimated_fee=0.01, estimated_slippage=0.01,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[definition, connection, mandate, mandate_version]))
    canonical = SimpleNamespace(preview_identity_hash="manual-canonical-hash")
    generator = AsyncMock(return_value=canonical)
    monkeypatch.setattr(subject, "generate_commissioned_campaign_preview", generator)
    request, result = await subject._canonical_preview_identity(
        db=db, claim=claim, package=package, activation=activation, preview=preview,
    )
    assert result.preview_identity_hash == "manual-canonical-hash"
    generator.assert_awaited_once_with(db=db, request=request)
    assert request.mandate_version_id == claim.mandate_version_id
    assert request.requested_quote_amount == package.risk_approved_amount
