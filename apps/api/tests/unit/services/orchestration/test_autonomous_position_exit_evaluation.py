from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.audit_log import AuditLog
from app.services.orchestration import autonomous_position_exit_evaluation as subject


class _Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class _DB:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.flushes = 0
    async def scalars(self, _statement): return _Rows([] if self.row is None else [self.row])
    async def scalar(self, _statement): return SimpleNamespace(status="EXPIRED")
    async def get(self, model, _identity):
        name = model.__name__
        values = {
            "LiveTradingProfile": SimpleNamespace(paper_account_id=self.row.paper_account_id),
            "PaperAccount": SimpleNamespace(id=self.row.paper_account_id),
            "ExchangeConnection": SimpleNamespace(provider=self.row.provider, environment=self.row.environment),
            "AutonomousExecutionClaim": SimpleNamespace(
                live_order_id=self.row.buy_live_order_id,
                profile_id=self.row.live_trading_profile_id, account_id=self.row.paper_account_id,
                connection_id=self.row.exchange_connection_id, provider=self.row.provider,
                environment=self.row.environment, product=self.row.product,
            ),
            "LiveCryptoOrder": SimpleNamespace(
                live_crypto_order_id=self.row.buy_live_order_id,
                exchange_connection_id=self.row.exchange_connection_id, provider=self.row.provider,
                environment=self.row.environment, product_id=self.row.product,
            ),
            "LiveReconciliationEvent": SimpleNamespace(
                live_crypto_order_id=self.row.buy_live_order_id, reconciliation_status="filled",
            ),
            "AutonomousCapitalMandate": SimpleNamespace(status="EXPIRED"),
        }
        return values.get(name)
    def add(self, value): self.added.append(value)
    async def flush(self): self.flushes += 1


def _row(*, proof_eligible=True):
    return SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", next_exit_evaluation_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        latest_exit_evaluation_at=None, observed_remaining_quantity=Decimal("0.00008"),
        audit_metadata={}, live_trading_profile_id=uuid.uuid4(), product="BTC-USD",
        paper_account_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
        buy_claim_id=uuid.uuid4(), buy_live_order_id=uuid.uuid4(),
        buy_reconciliation_event_id=uuid.uuid4(), runtime_campaign_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(), provider="kraken_spot", environment="production",
        active_sell_claim_id=None, active_sell_order_id=None,
        proof_eligible=proof_eligible,
        disqualification_reason=None if proof_eligible else "operator_assisted",
        continuing_exit_authority_state="UNARMED",
    )


def _snapshot(*, now, price=Decimal("70000"), opened_hours=2):
    return SimpleNamespace(
        live_trading_profile_id=None, symbol="BTC-USD", position_size=Decimal("0.00008"),
        asset_class="crypto", current_price=price, entry_price=Decimal("60000"),
        accumulated_entry_and_carry_costs=Decimal("0.02"),
        opened_at=now - timedelta(hours=opened_hours), market_data_timestamp=now,
    )


def _policy():
    return SimpleNamespace(
        policy_id="crypto-default", policy_version="1",
        stop_loss_price=None, stop_loss_percent=Decimal("0.03"), max_hold_minutes=60,
        estimated_exit_fee_rate=Decimal("0.0026"), estimated_slippage_rate=Decimal("0.001"),
        minimum_net_profit_to_exit=Decimal("2"), dust_threshold=Decimal("5"),
    )


@pytest.mark.asyncio
async def test_due_active_custody_is_evaluated_and_persisted_after_restart(monkeypatch):
    row = _row(proof_eligible=False)
    db = _DB(row)
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(subject, "_evaluate_one", lambda **_kwargs: _async({
        "authoritative_remaining_quantity": "0.00008", "disposition": "HOLD",
        "reason_codes": ["exit_conditions_not_satisfied"],
    }))

    result = await subject.evaluate_due_custodies(db=db, now=now, limit=10)

    assert result.discovered == result.evaluated == 1
    assert row.latest_exit_evaluation_at == now
    assert row.next_exit_evaluation_at == now + subject.EVALUATION_CADENCE
    assert row.proof_eligible is False and row.custody_state == "ACTIVE"
    assert row.audit_metadata["latest_exit_evaluation"]["disposition"] == "HOLD"
    assert any(isinstance(item, AuditLog) for item in db.added)


@pytest.mark.asyncio
async def test_dust_does_not_hide_stop_loss_and_maximum_hold(monkeypatch):
    row = _row()
    db = _DB(row)
    now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    snapshot = _snapshot(now=now, price=Decimal("50000"), opened_hours=2)
    snapshot.live_trading_profile_id = row.live_trading_profile_id
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(subject, "load_position_snapshots", lambda **_kwargs: _async([snapshot]))
    monkeypatch.setattr(subject, "resolve_lifecycle_policy", lambda **_kwargs: _policy())
    monkeypatch.setattr(subject, "evaluate_position_lifecycle", lambda **_kwargs: SimpleNamespace(
        market_data_stale=False, current_market_value=Decimal("4"),
        expected_net_realized_pnl_if_sold_now=Decimal("-0.03"), dust_indicator=True,
    ))

    result = await subject._evaluate_one(db=db, row=row, now=now)

    assert result["dust"] is True
    assert result["stop_loss_triggered"] is True
    assert result["maximum_hold_exceeded"] is True
    assert result["mandatory_safety_exit"] is True
    assert result["disposition"] == "EXIT_RECOMMENDED"
    assert result["entry_authority_expired"] is True


@pytest.mark.asyncio
async def test_profitable_exit_signal_is_reported_independently(monkeypatch):
    row = _row()
    db = _DB(row)
    now = datetime.now(timezone.utc)
    snapshot = _snapshot(now=now, price=Decimal("90000"), opened_hours=0)
    snapshot.live_trading_profile_id = row.live_trading_profile_id
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(subject, "load_position_snapshots", lambda **_kwargs: _async([snapshot]))
    monkeypatch.setattr(subject, "resolve_lifecycle_policy", lambda **_kwargs: _policy())
    monkeypatch.setattr(subject, "evaluate_position_lifecycle", lambda **_kwargs: SimpleNamespace(
        market_data_stale=False, current_market_value=Decimal("7.2"),
        expected_net_realized_pnl_if_sold_now=Decimal("2.1"), dust_indicator=False,
    ))
    result = await subject._evaluate_one(db=db, row=row, now=now)
    assert result["profitable_exit"] is True
    assert result["stop_loss_triggered"] is False
    assert result["maximum_hold_exceeded"] is False
    assert result["disposition"] == "EXIT_RECOMMENDED"
    assert "minimum_net_profit_satisfied" in result["reason_codes"]


@pytest.mark.asyncio
async def test_stale_price_fails_closed(monkeypatch):
    row = _row()
    db = _DB(row)
    now = datetime.now(timezone.utc)
    snapshot = _snapshot(now=now)
    snapshot.live_trading_profile_id = row.live_trading_profile_id
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(subject, "load_position_snapshots", lambda **_kwargs: _async([snapshot]))
    monkeypatch.setattr(subject, "resolve_lifecycle_policy", lambda **_kwargs: _policy())
    monkeypatch.setattr(subject, "evaluate_position_lifecycle", lambda **_kwargs: SimpleNamespace(
        market_data_stale=True, current_market_value=Decimal("5.6"),
        expected_net_realized_pnl_if_sold_now=Decimal("0.7"), dust_indicator=False,
    ))
    result = await subject._evaluate_one(db=db, row=row, now=now)
    assert result["disposition"] == "BLOCKED"
    assert "market_evidence_stale_or_missing" in result["reason_codes"]


@pytest.mark.asyncio
async def test_ambiguous_snapshot_fails_closed(monkeypatch):
    row = _row()
    db = _DB(row)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(subject, "load_position_snapshots", lambda **_kwargs: _async([]))
    result = await subject._evaluate_one(db=db, row=row, now=datetime.now(timezone.utc))
    assert result["disposition"] == "BLOCKED"
    assert "position_snapshot_ambiguous" in result["reason_codes"]


@pytest.mark.asyncio
async def test_unresolved_sell_reference_blocks_evaluation_advancement(monkeypatch):
    row = _row()
    row.active_sell_claim_id = uuid.uuid4()
    db = _DB(row)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(subject, "load_position_snapshots", lambda **_kwargs: _async([]))
    result = await subject._evaluate_one(db=db, row=row, now=datetime.now(timezone.utc))
    assert result["disposition"] == "BLOCKED"
    assert "unresolved_sell_execution_reference" in result["reason_codes"]


@pytest.mark.asyncio
async def test_zero_quantity_is_closed_candidate_without_terminalizing(monkeypatch):
    row = _row()
    db = _DB(row)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0")))
    result = await subject._evaluate_one(db=db, row=row, now=datetime.now(timezone.utc))
    assert result["disposition"] == "CLOSED_CANDIDATE"
    assert row.custody_state == "ACTIVE"


async def _async(value):
    return value
