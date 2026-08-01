from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.services.orchestration import autonomous_position_exit_reconciliation as subject


class _Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class _Db:
    def __init__(self, scalar_rows, collection_rows=()):
        self.scalar_rows = list(scalar_rows); self.collection_rows = list(collection_rows)
        self.added = []; self.flushes = 0
    async def scalar(self, _stmt): return self.scalar_rows.pop(0)
    async def scalars(self, _stmt): return _Rows(self.collection_rows.pop(0))
    async def get(self, _model, _identity): return None
    def add(self, row): self.added.append(row)
    async def flush(self): self.flushes += 1
    @asynccontextmanager
    async def begin_nested(self): yield


def _accounting(*, order_id, profile_id, provider_order_id, fill_id, side, qty, price,
                record_type="fill_accounting", fee="0.10", timestamp=None):
    return SimpleNamespace(
        live_crypto_order_id=order_id, live_trading_profile_id=profile_id,
        provider_order_id=provider_order_id, provider_fill_id=fill_id,
        side=side, symbol="BTC-USD", filled_quantity=Decimal(qty), fill_price=Decimal(price),
        gross_notional=Decimal(qty) * Decimal(price), fee_amount=Decimal(fee), fee_currency="USD",
        record_type=record_type, provider_fill_timestamp=timestamp or datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )


def _case(*, proof=True, sell_qty="0.00008", buy_price="62500", sell_price="70000", sell_fee="0.10", buy_fee="0.05"):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc); qty = Decimal("0.00008")
    ids = {name: uuid.uuid4() for name in ("order", "claim", "custody", "authority", "package", "activation", "preview", "profile", "account", "connection", "campaign", "buy", "buy_order", "buy_recon", "sell_recon")}
    disq = None if proof else "permanent_nonqualifying_lineage"
    order = SimpleNamespace(
        live_crypto_order_id=ids["order"], execution_claim_id=ids["claim"], custody_id=ids["custody"],
        exit_authority_id=ids["authority"], status="ACKNOWLEDGED", side="SELL", exposure_effect="REDUCE_ONLY",
        capital_deployment_amount=Decimal("0"), provider_submission_connected=True, submitted_at=now,
        provider_order_id="KRAKEN-SELL", normalized_base_quantity=qty, requested_base_quantity=qty,
        maximum_authorized_base_quantity=qty, crypto_order_preview_id=ids["preview"], proof_eligible=proof,
        disqualification_reason=disq, safe_provider_response={"live_trading_profile_id": str(ids["profile"]), "usd_available_before_submit": "100"},
    )
    claim = SimpleNamespace(
        claim_id=ids["claim"], live_order_id=ids["order"], custody_id=ids["custody"], package_id=ids["package"],
        activation_id=ids["activation"], campaign_id=ids["campaign"], campaign_version=1,
        account_id=ids["account"], profile_id=ids["profile"], connection_id=ids["connection"],
        provider="kraken_spot", environment="production", product="BTC-USD",
        originating_buy_claim_id=ids["buy"], originating_reconciliation_event_id=ids["buy_recon"],
        proof_eligible=proof, disqualification_reason=disq, claim_status="SUBMISSION_PENDING",
        reconciliation_state=None, completed_at=None, updated_at=now,
    )
    custody = SimpleNamespace(
        custody_id=ids["custody"], active_sell_order_id=ids["order"], active_sell_claim_id=ids["claim"],
        exit_reconciliation_event_id=None, custody_state="EXIT_PENDING", terminal_at=None,
        paper_account_id=ids["account"], live_trading_profile_id=ids["profile"], exchange_connection_id=ids["connection"],
        provider="kraken_spot", environment="production", product="BTC-USD", buy_claim_id=ids["buy"],
        buy_reconciliation_event_id=ids["buy_recon"], buy_live_order_id=ids["buy_order"], proof_eligible=proof,
        disqualification_reason=disq, observed_remaining_quantity=qty, continuing_exit_authority_state="RESERVED",
        autonomous_proof_sell_verified=False, realized_sold_quantity=None, realized_gross_sell_proceeds=None,
        realized_sell_fees=None, realized_net_sell_proceeds=None, allocated_buy_cost_basis=None,
        allocated_buy_fees=None, realized_net_profit=None, realized_return=None, residual_dust_quantity=None,
        exit_reconciled_at=None, updated_at=now,
    )
    authority = SimpleNamespace(
        reserved_order_id=ids["order"], reserved_claim_id=ids["claim"], authority_state="RESERVED",
        consumed_at=None, maximum_sell_quantity=qty, updated_at=now,
    )
    package = SimpleNamespace(
        package_id=ids["package"], crypto_order_preview_id=ids["preview"], campaign_id=ids["campaign"],
        campaign_version=1, paper_account_id=ids["account"], live_trading_profile_id=ids["profile"],
        provider="kraken_spot", environment="production", product="BTC-USD", side="SELL",
    )
    activation = SimpleNamespace(package_id=ids["package"])
    reconciliation = SimpleNamespace(id=ids["sell_recon"], reconciliation_status="filled")
    sell_fill = _accounting(order_id=ids["order"], profile_id=ids["profile"], provider_order_id="KRAKEN-SELL",
                            fill_id="SELL-FILL-1", side="sell", qty=sell_qty, price=sell_price)
    sell_fee_row = _accounting(order_id=ids["order"], profile_id=ids["profile"], provider_order_id="KRAKEN-SELL",
                               fill_id="SELL-FILL-1", side="sell", qty=sell_qty, price=sell_price,
                               record_type="fee_attribution", fee=sell_fee)
    buy_fill = _accounting(order_id=ids["buy_order"], profile_id=ids["profile"], provider_order_id="KRAKEN-BUY",
                           fill_id="BUY-FILL-1", side="buy", qty="0.00008", price=buy_price)
    buy_fee_row = _accounting(order_id=ids["buy_order"], profile_id=ids["profile"], provider_order_id="KRAKEN-BUY",
                              fill_id="BUY-FILL-1", side="buy", qty="0.00008", price=buy_price,
                              record_type="fee_attribution", fee=buy_fee)
    return now, qty, order, claim, custody, authority, package, activation, reconciliation, [sell_fill, sell_fee_row], [buy_fill, buy_fee_row]


def _canonical(status, *, completion="unresolved", balance="ok"):
    async def call(**kwargs):
        return {"reconciliation_status": status, "accounting_completion_status": completion,
                "balance_mismatch_state": balance}
    return call


def _ownership(monkeypatch, *values):
    queue = list(values)
    async def read(**_kwargs): return queue.pop(0)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", read)


@pytest.mark.asyncio
async def test_acknowledged_without_fill_does_not_close_or_consume(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0")
    sell = []
    db = _Db([order, claim, custody, authority, package, activation, None], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("ACKNOWLEDGED"))
    _ownership(monkeypatch, qty, qty)
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.filled_quantity == 0 and result.terminal is False
    assert custody.custody_state == "EXIT_PENDING" and authority.authority_state == "RESERVED"
    assert claim.claim_status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_unknown_provider_outcome_remains_recovery_required_without_replacement(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0")
    order.status = "UNKNOWN"; sell = []
    db = _Db([order, claim, custody, authority, package, activation, None], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("UNKNOWN", balance="missing"))
    _ownership(monkeypatch, qty, qty)
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.terminal is False and claim.claim_status == "RECONCILIATION_REQUIRED"
    assert custody.active_sell_order_id == order.live_crypto_order_id
    assert authority.authority_state == "RESERVED" and custody.custody_state == "EXIT_PENDING"


@pytest.mark.asyncio
async def test_partial_fill_reduces_only_accounted_quantity_and_remains_supervised(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0.00003")
    remaining = Decimal("0.00005")
    db = _Db([order, claim, custody, authority, package, activation, None], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("PARTIALLY_FILLED"))
    _ownership(monkeypatch, qty, remaining)
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.filled_quantity == Decimal("0.00003") and result.remaining_quantity == remaining
    assert custody.custody_state == "EXIT_PENDING" and authority.authority_state == "RESERVED"
    assert custody.autonomous_proof_sell_verified is False


@pytest.mark.asyncio
async def test_multiple_partial_fills_aggregate_once_by_distinct_provider_identity(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0.00003")
    second_fill = _accounting(order_id=order.live_crypto_order_id, profile_id=claim.profile_id,
                              provider_order_id=order.provider_order_id, fill_id="SELL-FILL-2",
                              side="sell", qty="0.00002", price="71000")
    second_fee = _accounting(order_id=order.live_crypto_order_id, profile_id=claim.profile_id,
                             provider_order_id=order.provider_order_id, fill_id="SELL-FILL-2",
                             side="sell", qty="0.00002", price="71000",
                             record_type="fee_attribution", fee="0.04")
    sell.extend([second_fill, second_fee])
    remaining = Decimal("0.00003")
    db = _Db([order, claim, custody, authority, package, activation, None], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("PARTIALLY_FILLED"))
    _ownership(monkeypatch, qty, remaining)
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.filled_quantity == Decimal("0.00005") and result.remaining_quantity == remaining
    assert result.sell_fees == Decimal("0.14")


@pytest.mark.asyncio
async def test_partial_fill_replay_does_not_reduce_custody_twice(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0.00003")
    custody.realized_sold_quantity = Decimal("0.00003")
    custody.observed_remaining_quantity = Decimal("0.00005")
    db = _Db([order, claim, custody, authority, package, activation, None], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("PARTIALLY_FILLED"))
    _ownership(monkeypatch, Decimal("0.00005"), Decimal("0.00005"))
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.filled_quantity == Decimal("0.00003")
    assert result.remaining_quantity == Decimal("0.00005")


@pytest.mark.asyncio
async def test_later_terminal_fill_applies_only_new_delta_after_prior_partial(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case()
    custody.realized_sold_quantity = Decimal("0.00003")
    custody.observed_remaining_quantity = Decimal("0.00005")
    db = _Db([order, claim, custody, authority, package, activation, reconciliation], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("FILLED", completion="complete"))
    _ownership(monkeypatch, Decimal("0.00005"), Decimal("0"))
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.filled_quantity == qty and result.remaining_quantity == 0
    assert result.terminal is True and authority.authority_state == "CONSUMED"


@pytest.mark.asyncio
async def test_terminal_full_fill_uses_actual_fees_closes_and_consumes_once(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case()
    db = _Db([order, claim, custody, authority, package, activation, reconciliation], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("FILLED", completion="complete"))
    _ownership(monkeypatch, qty, Decimal("0"))
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    # 5.60 gross - 0.10 SELL fee - 5.00 BUY cost - 0.05 BUY fee = 0.45.
    assert result.gross_proceeds == Decimal("5.60000")
    assert result.realized_net_profit == Decimal("0.45000") and result.proof_sell_verified is True
    assert custody.custody_state == "CLOSED" and custody.observed_remaining_quantity == 0
    assert authority.authority_state == "CONSUMED" and claim.claim_status == "COMPLETED"
    assert custody.exit_reconciliation_event_id == reconciliation.id
    assert any(isinstance(row, AuditLog) for row in db.added)


@pytest.mark.asyncio
async def test_positive_prefee_but_negative_after_all_fees_never_qualifies(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(
        sell_price="65000", sell_fee="0.15", buy_fee="0.10",
    )
    db = _Db([order, claim, custody, authority, package, activation, reconciliation], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("FILLED", completion="complete"))
    _ownership(monkeypatch, qty, Decimal("0"))
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.realized_net_profit < 0 and result.proof_sell_verified is False
    assert custody.custody_state == "CLOSED"


@pytest.mark.asyncio
async def test_permanent_disqualification_survives_profitable_terminal_fill(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(proof=False)
    db = _Db([order, claim, custody, authority, package, activation, reconciliation], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("FILLED", completion="complete"))
    _ownership(monkeypatch, qty, Decimal("0"))
    result = await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert result.realized_net_profit > 0 and result.proof_sell_verified is False
    assert custody.disqualification_reason == "permanent_nonqualifying_lineage"


@pytest.mark.asyncio
async def test_cancellation_blocks_for_governed_recovery_without_consuming_inventory(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0")
    db = _Db([order, claim, custody, authority, package, activation, None], [[], buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("CANCELLED"))
    _ownership(monkeypatch, qty, qty)
    await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)
    assert custody.custody_state == "BLOCKED" and custody.observed_remaining_quantity == qty
    assert authority.authority_state == "BLOCKED" and authority.consumed_at is None
    assert claim.claim_status == "CANCELLED" and custody.active_sell_order_id is None


@pytest.mark.asyncio
async def test_explicit_rejection_without_provider_order_is_governed_without_lookup(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case(sell_qty="0")
    order.status = "REJECTED"; order.provider_order_id = None
    async def forbidden(**_kwargs): raise AssertionError("explicit rejection must not trigger provider lookup")
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", forbidden)
    _ownership(monkeypatch, qty)
    result = await subject.reconcile_autonomous_exit_order(
        db=_Db([order, claim, custody, authority, package, activation]),
        order_id=order.live_crypto_order_id, now=now,
    )
    assert result.filled_quantity == 0 and result.remaining_quantity == qty
    assert custody.custody_state == authority.authority_state == "BLOCKED"
    assert claim.claim_status == "CANCELLED" and custody.active_sell_order_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda o, c, u, a, rows: setattr(o, "side", "BUY"),
    lambda o, c, u, a, rows: setattr(o, "exposure_effect", "INCREASE"),
    lambda o, c, u, a, rows: setattr(o, "capital_deployment_amount", Decimal("1")),
    lambda o, c, u, a, rows: setattr(o, "normalized_base_quantity", Decimal("0.00009")),
    lambda o, c, u, a, rows: setattr(u, "product", "ETH-USD"),
    lambda o, c, u, a, rows: setattr(rows[0], "provider_order_id", "OTHER"),
    lambda o, c, u, a, rows: setattr(rows[0], "filled_quantity", Decimal("0.00009")),
])
async def test_invalid_excessive_mismatched_or_cross_scope_fill_fails_closed(monkeypatch, mutation):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case()
    mutation(order, claim, custody, authority, sell)
    db = _Db([order, claim, custody, authority, package, activation], [sell, buy])
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", _canonical("PARTIALLY_FILLED"))
    _ownership(monkeypatch, qty, Decimal("0"))
    with pytest.raises(InvalidRequestError):
        await subject.reconcile_autonomous_exit_order(db=db, order_id=order.live_crypto_order_id, now=now)


@pytest.mark.asyncio
async def test_closed_replay_returns_same_result_without_provider_reconciliation(monkeypatch):
    now, qty, order, claim, custody, authority, package, activation, reconciliation, sell, buy = _case()
    custody.exit_reconciliation_event_id = reconciliation.id; custody.custody_state = "CLOSED"
    custody.observed_remaining_quantity = Decimal("0"); custody.realized_sold_quantity = qty
    custody.realized_gross_sell_proceeds = Decimal("5.6"); custody.realized_sell_fees = Decimal("0.1")
    custody.realized_net_sell_proceeds = Decimal("5.5"); custody.realized_net_profit = Decimal("0.45")
    custody.autonomous_proof_sell_verified = True
    async def forbidden(**_kwargs): raise AssertionError("provider reconciliation must not replay")
    monkeypatch.setattr(subject, "reconcile_live_order_and_fills", forbidden)
    result = await subject.reconcile_autonomous_exit_order(
        db=_Db([order, claim, custody, authority]), order_id=order.live_crypto_order_id, now=now,
    )
    assert result.idempotent is True and result.proof_sell_verified is True
