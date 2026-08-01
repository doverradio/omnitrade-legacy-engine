from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.orchestration import autonomous_position_exit_order as subject
from app.services.orchestration import autonomous_execution_claims as execution


class _Db:
    def __init__(self, scalars, gets=None, discovered=()):
        self.queue = list(scalars); self.gets = gets or {}; self.discovered = list(discovered)
        self.added = []; self.flushes = 0
    async def scalar(self, _statement): return self.queue.pop(0)
    async def get(self, model, identity): return self.gets.get((model, identity))
    async def scalars(self, _statement): return SimpleNamespace(all=lambda: self.discovered)
    def add(self, row): self.added.append(row)
    async def flush(self): self.flushes += 1
    @asynccontextmanager
    async def begin_nested(self): yield


async def _value(value): return value


def _rows(*, proceeds="7.20", proof=True, step="0.00001", product="BTC-USD"):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc); quantity = Decimal("0.00008")
    ids = {name: uuid.uuid4() for name in ("claim", "custody", "authority", "package", "activation", "preview", "risk", "audit", "decision", "account", "profile", "connection", "campaign", "mandate", "mandate_version", "buy_claim", "reconciliation")}
    evaluation = {"evaluated_at": now.isoformat(), "disposition": "EXIT_RECOMMENDED", "price_fresh": True}
    claim = SimpleNamespace(
        claim_id=ids["claim"], claim_version=1, live_order_id=None, claim_status="CLAIMED",
        expires_at=now + timedelta(minutes=2), reconciliation_state=None, side="SELL",
        exposure_effect="REDUCE_ONLY", capital_deployment_amount=Decimal("0"),
        provider_submission_connected=False, custody_id=ids["custody"], exit_authority_id=ids["authority"], exit_authority_version=1,
        package_id=ids["package"], activation_id=ids["activation"], evaluation_integrity_hash=subject._digest(evaluation),
        profile_id=ids["profile"], account_id=ids["account"], connection_id=ids["connection"],
        provider="kraken_spot", environment="production", product=product,
        claimed_base_quantity=quantity, maximum_authorized_base_quantity=quantity,
        expected_quote_proceeds=Decimal(proceeds), originating_buy_claim_id=ids["buy_claim"],
        originating_reconciliation_event_id=ids["reconciliation"], proof_eligible=proof,
        disqualification_reason=None if proof else "permanent_nonqualifying_lineage",
        preview_id=ids["preview"], risk_event_id=ids["risk"], audit_correlation_id=ids["audit"],
        campaign_id=ids["campaign"], campaign_version=1, mandate_id=ids["mandate"], mandate_version_id=ids["mandate_version"],
        claim_owner="test", claimed_at=now, recover_after=now + timedelta(minutes=1), updated_at=now,
    )
    custody = SimpleNamespace(
        custody_id=ids["custody"], custody_state="EXIT_PENDING", terminal_at=None,
        audit_metadata={"latest_exit_evaluation": evaluation}, observed_remaining_quantity=quantity,
        paper_account_id=ids["account"], live_trading_profile_id=ids["profile"],
        exchange_connection_id=ids["connection"], provider="kraken_spot", environment="production",
        product=product, buy_claim_id=ids["buy_claim"], buy_reconciliation_event_id=ids["reconciliation"],
        proof_eligible=proof, disqualification_reason=claim.disqualification_reason,
        active_sell_order_id=None, updated_at=now,
    )
    authority = SimpleNamespace(
        authority_id=ids["authority"], authority_version=1, authority_state="RESERVED", revoked_at=None,
        consumed_at=None, expires_at=now + timedelta(minutes=5), reserved_claim_id=ids["claim"],
        reserved_activation_id=ids["activation"], custody_id=ids["custody"], proof_eligible=proof,
        side="SELL", exposure_effect="REDUCE_ONLY", maximum_sell_quantity=quantity,
        reserved_order_id=None, updated_at=now, last_order_failure_at=None, last_order_failure_code=None,
        last_order_exception_class=None, last_order_failure_retryable=None,
    )
    package = SimpleNamespace(
        package_id=ids["package"], package_state="ACTIVATED", superseded_at=None,
        proposed_base_quantity=quantity, crypto_order_preview_id=ids["preview"], decision_record_id=ids["decision"],
    )
    activation = SimpleNamespace(activation_id=ids["activation"], package_id=ids["package"], activation_state="ACTIVE", expires_at=now + timedelta(minutes=2))
    preview = SimpleNamespace(crypto_order_preview_id=ids["preview"], side="SELL", status="PREVIEW_READY", risk_verdict="approved_for_preview", base_size=quantity)
    asset = SimpleNamespace(qty_step_size=Decimal(step), supports_fractional=True, min_order_notional=Decimal("5"))
    gets = {(subject.CryptoOrderPreview, ids["preview"]): preview}
    return now, quantity, claim, custody, authority, package, activation, asset, gets


@pytest.mark.asyncio
@pytest.mark.parametrize("proceeds", ["5.20", "7.20"])
@pytest.mark.parametrize("product", ["BTC-USD", "ETH-USD", "SOL-USD"])
async def test_valid_claim_constructs_one_nonsubmitted_canonical_sell_order(monkeypatch, proceeds, product):
    now, quantity, claim, custody, authority, package, activation, asset, gets = _rows(proceeds=proceeds, product=product)
    db = _Db([claim, custody, authority, package, activation, asset, None, None], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(quantity))
    result = await subject.construct_autonomous_exit_order(db=db, claim_id=claim.claim_id, now=now)
    order = next(row for row in db.added if isinstance(row, LiveCryptoOrder))
    assert result.order_id == order.live_crypto_order_id and result.idempotent is False
    assert order.side == "SELL" and order.exposure_effect == "REDUCE_ONLY"
    assert order.product_id == product
    assert order.requested_base_quantity == order.normalized_base_quantity == quantity
    assert order.expected_quote_proceeds == Decimal(proceeds)
    assert order.capital_deployment_amount == 0 and order.status == "PENDING_CONFIRMATION"
    assert order.provider_submission_connected is False and order.provider_order_id is None and order.submitted_at is None
    assert claim.claim_status == "EXECUTION_STARTED" and claim.live_order_id == order.live_crypto_order_id
    assert custody.active_sell_order_id == authority.reserved_order_id == order.live_crypto_order_id
    assert authority.authority_state == "RESERVED" and custody.custody_state == "EXIT_PENDING"
    assert order.safe_provider_response["provider_call_made"] is False


@pytest.mark.asyncio
async def test_exact_replay_returns_the_same_constructed_order(monkeypatch):
    now, quantity, claim, custody, authority, package, activation, asset, gets = _rows()
    order_id = uuid.uuid4()
    claim.live_order_id = order_id
    claim.claim_status = "EXECUTION_STARTED"
    custody.active_sell_order_id = order_id
    authority.reserved_order_id = order_id
    order = SimpleNamespace(
        live_crypto_order_id=order_id,
        execution_claim_id=claim.claim_id,
        status="PENDING_CONFIRMATION",
        provider_order_id=None,
        submitted_at=None,
        provider_submission_connected=False,
        construction_expires_at=claim.expires_at,
        requested_base_quantity=quantity,
        normalized_base_quantity=quantity,
        expected_quote_proceeds=Decimal("7.20"),
    )
    gets.update({
        (subject.LiveCryptoOrder, order_id): order,
        (subject.AutonomousPositionCustody, custody.custody_id): custody,
        (subject.AutonomousPositionExitAuthority, authority.authority_id): authority,
    })
    db = _Db([claim], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(quantity))

    result = await subject.construct_autonomous_exit_order(db=db, claim_id=claim.claim_id, now=now)

    assert result.order_id == order_id
    assert result.idempotent is True
    assert db.added == []


def test_provider_precision_rounds_down_only_and_rejects_dust():
    assert subject.normalize_provider_quantity(quantity=Decimal("0.000089"), step=Decimal("0.00001"), supports_fractional=True) == Decimal("0.00008")
    with pytest.raises(InvalidRequestError, match="dust or zero"):
        subject.normalize_provider_quantity(quantity=Decimal("0.000009"), step=Decimal("0.00001"), supports_fractional=True)
    with pytest.raises(InvalidRequestError, match="precision is unavailable"):
        subject.normalize_provider_quantity(quantity=Decimal("1"), step=None, supports_fractional=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,error", [
    (lambda c, u, a, p: setattr(c, "side", "BUY"), "provider-disconnected"),
    (lambda c, u, a, p: setattr(c, "exposure_effect", "INCREASE"), "provider-disconnected"),
    (lambda c, u, a, p: setattr(c, "capital_deployment_amount", Decimal("1")), "provider-disconnected"),
    (lambda c, u, a, p: setattr(a, "authority_state", "REVOKED"), "Continuing authority"),
    (lambda c, u, a, p: setattr(p, "package_state", "SUPERSEDED"), "package or activation"),
    (lambda c, u, a, p: setattr(u, "product", "ETH-USD"), "scope"),
    (lambda c, u, a, p: setattr(c, "claimed_base_quantity", Decimal("0.00009")), "changed, ambiguous, or excessive"),
])
async def test_invalid_claim_authority_package_quantity_and_scope_fail_closed(monkeypatch, mutation, error):
    now, quantity, claim, custody, authority, package, activation, asset, gets = _rows()
    mutation(claim, custody, authority, package)
    db = _Db([claim, custody, authority, package, activation], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(quantity))
    with pytest.raises(InvalidRequestError, match=error):
        await subject.construct_autonomous_exit_order(db=db, claim_id=claim.claim_id, now=now)
    assert not any(isinstance(row, LiveCryptoOrder) for row in db.added)


@pytest.mark.asyncio
async def test_proof_disqualified_order_stays_nonqualifying(monkeypatch):
    now, quantity, claim, custody, authority, package, activation, asset, gets = _rows(proof=False)
    db = _Db([claim, custody, authority, package, activation, asset, None, None], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(quantity))
    await subject.construct_autonomous_exit_order(db=db, claim_id=claim.claim_id, now=now)
    order = next(row for row in db.added if isinstance(row, LiveCryptoOrder))
    assert order.proof_eligible is False and order.disqualification_reason == "permanent_nonqualifying_lineage"
    assert order.safe_provider_response["autonomous_proof_sell_ready"] is False


@pytest.mark.asyncio
async def test_execution_sweeper_cannot_submit_disconnected_claim(monkeypatch):
    claim = SimpleNamespace(claim_id=uuid.uuid4(), package_id=uuid.uuid4(), claim_status="EXECUTION_STARTED", provider_submission_connected=False)
    async def forbidden(**_kwargs): raise AssertionError("preparation/submission must be unreachable")
    monkeypatch.setattr(execution, "prepare_autonomous_claimed_order", forbidden)
    await execution.advance_claimed_execution(db=SimpleNamespace(), claim=claim)
