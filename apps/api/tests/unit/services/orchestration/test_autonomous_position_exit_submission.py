from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import (
    ExchangeOrderSubmissionResult, ExchangeProviderAmbiguousResponse,
    ExchangeProviderOrder, ExchangeProviderRejection,
)
from app.services.orchestration import autonomous_position_exit_submission as subject


class _Db:
    def __init__(self, rows, gets=None, fail_flush_at=None):
        self.rows = list(rows); self.gets = gets or {}; self.added = []
        self.flushes = 0; self.commits = 0; self.fail_flush_at = fail_flush_at
    async def scalar(self, _stmt): return self.rows.pop(0)
    async def get(self, model, identity): return self.gets.get((model, identity))
    def add(self, row): self.added.append(row)
    async def flush(self):
        self.flushes += 1
        if self.flushes == self.fail_flush_at: raise RuntimeError("simulated persistence failure")
    async def commit(self): self.commits += 1


class _Provider:
    def __init__(self, submission=None, recovered=None, error=None):
        self.submission = submission; self.recovered = recovered; self.error = error
        self.submits = []; self.lookups = []
    async def submit_order(self, **kwargs):
        self.submits.append(kwargs)
        if self.error: raise self.error
        return self.submission
    async def lookup_order(self, **kwargs):
        self.lookups.append(kwargs)
        if self.error: raise self.error
        return self.recovered


async def _value(value): return value


def _case(*, proof=True, proceeds="7.20"):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc); qty = Decimal("0.00008")
    ids = {name: uuid.uuid4() for name in ("order", "claim", "custody", "authority", "package", "activation", "preview", "risk", "account", "profile", "connection", "buy", "recon")}
    evaluation = {"evaluated_at": now.isoformat(), "disposition": "EXIT_RECOMMENDED", "price_fresh": True}
    digest = subject._digest(evaluation); disq = None if proof else "permanent_nonqualifying_lineage"
    claim = SimpleNamespace(
        claim_id=ids["claim"], claim_status="EXECUTION_STARTED", live_order_id=ids["order"],
        expires_at=now + timedelta(minutes=2), reconciliation_state=None, provider_submission_connected=False,
        profile_id=ids["profile"], account_id=ids["account"], connection_id=ids["connection"],
        provider="kraken_spot", environment="production", product="BTC-USD", custody_id=ids["custody"],
        package_id=ids["package"], activation_id=ids["activation"], preview_id=ids["preview"],
        claimed_base_quantity=qty, originating_buy_claim_id=ids["buy"],
        originating_reconciliation_event_id=ids["recon"], proof_eligible=proof,
        disqualification_reason=disq, evaluation_integrity_hash=digest, last_error_code=None, updated_at=now,
    )
    order = SimpleNamespace(
        live_crypto_order_id=ids["order"], execution_claim_id=ids["claim"], custody_id=ids["custody"],
        exit_authority_id=ids["authority"], status="PENDING_CONFIRMATION", provider_order_id=None,
        provider_status=None, submitted_at=None, acknowledged_at=None, provider_submission_connected=False,
        side="SELL", exposure_effect="REDUCE_ONLY", capital_deployment_amount=Decimal("0"),
        requested_base_quantity=qty, normalized_base_quantity=qty, maximum_authorized_base_quantity=qty,
        expected_quote_proceeds=Decimal(proceeds), evaluation_integrity_hash=digest,
        exchange_connection_id=ids["connection"], provider="kraken_spot", environment="production",
        product_id="BTC-USD", originating_buy_claim_id=ids["buy"],
        originating_reconciliation_event_id=ids["recon"], proof_eligible=proof,
        disqualification_reason=disq, crypto_order_preview_id=ids["preview"], risk_event_id=ids["risk"],
        client_order_id=f"autonomous-exit:{ids['claim']}:v1", order_type="market",
        safe_provider_response={"provider_call_made": False}, failure_code=None, failure_reason=None, updated_at=now,
    )
    custody = SimpleNamespace(
        custody_id=ids["custody"], custody_state="EXIT_PENDING", terminal_at=None,
        active_sell_order_id=ids["order"], active_sell_claim_id=ids["claim"],
        observed_remaining_quantity=qty, audit_metadata={"latest_exit_evaluation": evaluation},
        paper_account_id=ids["account"], live_trading_profile_id=ids["profile"],
        exchange_connection_id=ids["connection"], provider="kraken_spot", environment="production",
        product="BTC-USD", buy_claim_id=ids["buy"], buy_reconciliation_event_id=ids["recon"],
        proof_eligible=proof, disqualification_reason=disq,
    )
    authority = SimpleNamespace(
        authority_state="RESERVED", revoked_at=None, consumed_at=None, expires_at=now + timedelta(minutes=5),
        reserved_order_id=ids["order"], reserved_claim_id=ids["claim"], reserved_activation_id=ids["activation"],
        custody_id=ids["custody"], proof_eligible=proof, maximum_sell_quantity=qty,
    )
    connection = SimpleNamespace(exchange_connection_id=ids["connection"], balances=[{"currency": "USD", "available": "100"}])
    package = SimpleNamespace(package_id=ids["package"], package_state="ACTIVATED", superseded_at=None,
                              proposed_base_quantity=qty, crypto_order_preview_id=ids["preview"])
    activation = SimpleNamespace(activation_state="ACTIVE", package_id=ids["package"], expires_at=now + timedelta(minutes=2))
    preview = SimpleNamespace(crypto_order_preview_id=ids["preview"], side="SELL", estimated_base_size=qty)
    asset = SimpleNamespace(qty_step_size=Decimal("0.00001"), supports_fractional=True, min_order_notional=Decimal("5"))
    rows = [order, claim, custody, authority, connection, package, activation, asset, None]
    gets = {(subject.CryptoOrderPreview, ids["preview"]): preview}
    return now, qty, order, claim, custody, authority, rows, gets


def _enable(monkeypatch, enabled=True):
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(autonomous_position_exit_submission_enabled=enabled))
    monkeypatch.setattr(subject, "require_provider_capabilities", lambda **_kw: None)


@pytest.mark.asyncio
async def test_disabled_gate_makes_provider_contact_unreachable(monkeypatch):
    _enable(monkeypatch, False); provider = _Provider()
    with pytest.raises(PermissionError, match="disabled"):
        await subject.submit_autonomous_exit_order(db=_Db([]), order_id=uuid.uuid4(), provider_override=provider)
    assert provider.submits == [] and provider.lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize("proceeds", ["5.20", "7.20"])
async def test_enabled_valid_sell_uses_canonical_provider_once_and_stops_before_reconciliation(monkeypatch, proceeds):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case(proceeds=proceeds)
    provider_order = ExchangeProviderOrder("KRAKEN-1", order.client_order_id, "BTC-USD", "SELL", "OPEN", now, now, {})
    provider = _Provider(ExchangeOrderSubmissionResult("success", provider_order, None, None))
    db = _Db(rows, gets); monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(qty))
    result = await subject.submit_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now,
                                                        provider_override=provider, credentials_override={})
    assert result.status == "ACKNOWLEDGED" and result.provider_order_id == "KRAKEN-1"
    assert len(provider.submits) == 1 and provider.submits[0]["request"].base_size == qty
    assert provider.submits[0]["request"].quote_size is None
    assert order.submitted_at == now and order.provider_submission_connected is True
    assert claim.claim_status == "SUBMISSION_PENDING" and authority.authority_state == "RESERVED"
    assert custody.custody_state == "EXIT_PENDING" and order.capital_deployment_amount == 0
    assert order.safe_provider_response["provider_call_made"] is True


@pytest.mark.asyncio
async def test_timeout_enters_recovery_and_retry_only_looks_up_by_client_identity(monkeypatch):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case()
    provider = _Provider(error=TimeoutError("unknown")); db = _Db(rows, gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(qty))
    first = await subject.submit_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now,
                                                       provider_override=provider, credentials_override={})
    assert first.status == "RECONCILIATION_REQUIRED" and len(provider.submits) == 1
    provider.error = None
    provider.recovered = ExchangeProviderOrder("KRAKEN-RECOVERED", order.client_order_id, "BTC-USD", "SELL", "OPEN", now, now, {})
    replay_db = _Db([order, claim, custody, authority, SimpleNamespace(exchange_connection_id=order.exchange_connection_id, balances=[{"currency": "USD", "available": "100"}])])
    replay = await subject.submit_autonomous_exit_order(db=replay_db, order_id=order.live_crypto_order_id, now=now,
                                                        provider_override=provider, credentials_override={})
    assert replay.recovered is True and replay.provider_order_id == "KRAKEN-RECOVERED"
    assert len(provider.submits) == 1 and len(provider.lookups) == 1
    assert provider.lookups[0]["client_order_id"] == order.client_order_id


@pytest.mark.asyncio
async def test_acknowledged_replay_returns_same_provider_order_without_contact(monkeypatch):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case()
    order.status = "ACKNOWLEDGED"; order.provider_order_id = "KRAKEN-EXISTING"
    provider = _Provider()
    result = await subject.submit_autonomous_exit_order(
        db=_Db(rows[:5], gets), order_id=order.live_crypto_order_id, now=now,
        provider_override=provider, credentials_override={},
    )
    assert result.provider_order_id == "KRAKEN-EXISTING" and result.provider_call_made is False
    assert provider.submits == [] and provider.lookups == []


@pytest.mark.asyncio
async def test_post_provider_persistence_failure_never_claims_provider_was_not_called(monkeypatch):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case()
    provider_order = ExchangeProviderOrder("KRAKEN-1", order.client_order_id, "BTC-USD", "SELL", "OPEN", now, now, {})
    provider = _Provider(ExchangeOrderSubmissionResult("success", provider_order, None, None))
    db = _Db(rows, gets, fail_flush_at=2)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(qty))
    with pytest.raises(RuntimeError, match="persistence failure"):
        await subject.submit_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now,
                                                   provider_override=provider, credentials_override={})
    assert len(provider.submits) == 1
    assert order.safe_provider_response["provider_call_made"] is True
    assert db.commits == 1  # SUBMISSION_PENDING was durable before provider contact.


@pytest.mark.asyncio
async def test_explicit_rejection_and_nonqualifying_proof_remain_truthful(monkeypatch):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case(proof=False)
    rejection = ExchangeProviderRejection("insufficient_funds", "rejected", False)
    provider = _Provider(ExchangeOrderSubmissionResult("rejected", None, rejection, None))
    db = _Db(rows, gets); monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(qty))
    result = await subject.submit_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now,
                                                        provider_override=provider, credentials_override={})
    assert result.status == "REJECTED" and result.provider_order_id is None
    assert claim.claim_status == "CANCELLED" and order.proof_eligible is False
    assert order.disqualification_reason == "permanent_nonqualifying_lineage"
    assert custody.custody_state == "EXIT_PENDING" and authority.authority_state == "RESERVED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda o, c, u, a: setattr(o, "side", "BUY"),
    lambda o, c, u, a: setattr(o, "exposure_effect", "INCREASE"),
    lambda o, c, u, a: setattr(o, "capital_deployment_amount", Decimal("1")),
    lambda o, c, u, a: setattr(c, "expires_at", datetime(2026, 8, 1, 11, tzinfo=timezone.utc)),
    lambda o, c, u, a: setattr(a, "revoked_at", datetime(2026, 8, 1, 11, tzinfo=timezone.utc)),
    lambda o, c, u, a: setattr(u, "product", "ETH-USD"),
    lambda o, c, u, a: setattr(o, "normalized_base_quantity", Decimal("0.00009")),
])
async def test_invalid_or_cross_scope_input_fails_before_provider_contact(monkeypatch, mutation):
    _enable(monkeypatch); now, qty, order, claim, custody, authority, rows, gets = _case()
    mutation(order, claim, custody, authority); provider = _Provider(); db = _Db(rows, gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _value(qty))
    with pytest.raises(InvalidRequestError):
        await subject.submit_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now,
                                                   provider_override=provider, credentials_override={})
    assert provider.submits == [] and provider.lookups == []
