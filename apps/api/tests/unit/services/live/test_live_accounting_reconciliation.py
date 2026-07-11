from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.accounting_reconciliation import (
    reconcile_live_order_and_fills,
    record_live_fill_reconciliation,
    record_live_order_reconciliation,
)
from app.services.live.contracts import (
    LiveFillReconciliationRequest,
    LiveOrderReconciliationRequest,
)


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, *, execution_events: list[LiveExecutionEvent]) -> None:
        self.execution_events = execution_events
        self.reconciliation_events: list[LiveReconciliationEvent] = []
        self.accounting_records: list[LiveAccountingRecord] = []
        self.live_orders: list[Any] = []
        self.live_profiles: list[Any] = []
        self.capital_campaigns: list[Any] = []
        self.commits = 0

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_reconciliation_events" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.reconciliation_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "max(live_reconciliation_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            seqs = [
                item.sequence_number
                for item in self.reconciliation_events
                if item.live_trading_profile_id == profile_id
            ]
            return max(seqs) if seqs else None

        if "FROM live_execution_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for event in self.execution_events:
                if event.id == event_id and event.live_trading_profile_id == profile_id:
                    return event
            return None

        if "FROM live_crypto_orders" in sql:
            order_id = params.get("live_crypto_order_id_1") or params.get("live_crypto_order_id_2")
            for order in self.live_orders:
                if order.live_crypto_order_id == order_id:
                    return order
            return None

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            for profile in self.live_profiles:
                if profile.id == profile_id:
                    return profile
            return self.live_profiles[0] if self.live_profiles else None

        if "FROM capital_campaigns" in sql:
            account_id = params.get("paper_account_id_1")
            rows = [item for item in self.capital_campaigns if item.paper_account_id == account_id]
            return rows[0] if rows else None

        return None

    async def scalars(self, statement: Any) -> _ScalarRows:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_accounting_records" in sql:
            rec_id = params.get("reconciliation_event_id_1")
            order_id = params.get("live_crypto_order_id_1")
            rows = self.accounting_records
            if rec_id is not None:
                rows = [item for item in rows if item.reconciliation_event_id == rec_id]
            if order_id is not None:
                rows = [item for item in rows if getattr(item, "live_crypto_order_id", None) == order_id]
            return _ScalarRows(rows)

        return _ScalarRows([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveExecutionEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.execution_events.append(obj)
            return

        if isinstance(obj, LiveReconciliationEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.reconciliation_events.append(obj)
            return

        if isinstance(obj, LiveAccountingRecord):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.accounting_records.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


def _execution_event(event_type: str = "execution_intent_created") -> LiveExecutionEvent:
    now = datetime.now(timezone.utc)
    return LiveExecutionEvent(
        id=uuid.uuid4(),
        idempotency_key=f"{event_type}-key",
        event_hash=f"{event_type}-hash",
        live_trading_profile_id=uuid.uuid4(),
        sequence_number=1,
        event_type=event_type,
        provider_name="paper-sim",
        risk_decision_id=uuid.uuid4(),
        approval_event_id=uuid.uuid4(),
        audit_correlation_id="audit-1",
        operating_mode="live",
        paper_default_mode=True,
        risk_authority_model="risk_engine_final",
        event_payload={"symbol": "AAPL", "side": "buy"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )


def _order_request(event: LiveExecutionEvent, **overrides: Any) -> LiveOrderReconciliationRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": event.live_trading_profile_id,
        "source_execution_event_id": event.id,
        "provider_name": "paper-sim",
        "provider_order_id": "provider-order-1",
        "client_order_id": "client-order-1",
        "reconciliation_status": "open",
        "requested_by": "operator",
        "provenance_metadata": {"ticket": "LIVE-96"},
        "idempotency_key": "order-rec-key-1",
    }
    payload.update(overrides)
    return LiveOrderReconciliationRequest(**payload)


def _fill_request(event: LiveExecutionEvent, **overrides: Any) -> LiveFillReconciliationRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": event.live_trading_profile_id,
        "source_execution_event_id": event.id,
        "provider_name": "paper-sim",
        "provider_order_id": "provider-order-1",
        "provider_fill_id": "provider-fill-1",
        "client_order_id": "client-order-1",
        "symbol": "AAPL",
        "side": "buy",
        "fill_quantity": "1.0",
        "cumulative_filled_quantity": "1.0",
        "order_quantity": "2.0",
        "fill_price": "200.00",
        "fee_amount": "1.25",
        "fee_currency": "USD",
        "requested_by": "operator",
        "provenance_metadata": {"ticket": "LIVE-96"},
        "idempotency_key": "fill-rec-key-1",
    }
    payload.update(overrides)
    return LiveFillReconciliationRequest(**payload)


@pytest.mark.asyncio
async def test_order_reconciliation_records_only_for_execution_intent_created() -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])

    result = await record_live_order_reconciliation(db=session, request=_order_request(source))

    assert result.accepted is True
    assert result.status == "recorded"
    assert len(session.reconciliation_events) == 1
    assert session.reconciliation_events[0].event_type == "order_reconciled"


@pytest.mark.asyncio
async def test_order_reconciliation_blocks_blocked_or_replayed_sources() -> None:
    blocked_source = _execution_event("execution_blocked")
    session = _FakeSession(execution_events=[blocked_source])

    blocked = await record_live_order_reconciliation(
        db=session,
        request=_order_request(blocked_source, idempotency_key="order-rec-key-2"),
    )

    assert blocked.accepted is False
    assert blocked.reason == "source_execution_event_not_reconcilable"
    assert len(session.reconciliation_events) == 0
    assert len(session.accounting_records) == 0


@pytest.mark.asyncio
async def test_fill_reconciliation_records_partial_fill_and_fee_attribution() -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])

    result = await record_live_fill_reconciliation(
        db=session,
        request=_fill_request(
            source,
            fill_quantity="0.5",
            cumulative_filled_quantity="0.5",
            order_quantity="1.0",
            idempotency_key="fill-rec-key-2",
        ),
    )

    assert result.accepted is True
    assert len(session.reconciliation_events) == 1
    event = session.reconciliation_events[0]
    assert event.reconciliation_status == "partially_filled"
    assert len(session.accounting_records) == 2
    types = {item.record_type for item in session.accounting_records}
    assert "partial_fill_accounting" in types
    assert "fee_attribution" in types


@pytest.mark.asyncio
async def test_fill_reconciliation_is_idempotent_and_replays_accounting_record_ids() -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])
    request = _fill_request(source, idempotency_key="fill-rec-key-3")

    first = await record_live_fill_reconciliation(db=session, request=request)
    second = await record_live_fill_reconciliation(db=session, request=request)

    assert first.status == "recorded"
    assert second.status == "replayed"
    assert len(session.reconciliation_events) == 1
    assert len(session.accounting_records) == 2


@pytest.mark.asyncio
async def test_fill_reconciliation_fee_and_net_cash_impact_for_buy_and_sell() -> None:
    buy_source = _execution_event("execution_intent_created")
    sell_source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[buy_source, sell_source])

    buy = await record_live_fill_reconciliation(
        db=session,
        request=_fill_request(buy_source, idempotency_key="fill-rec-key-4", side="buy"),
    )
    sell = await record_live_fill_reconciliation(
        db=session,
        request=_fill_request(sell_source, idempotency_key="fill-rec-key-5", side="sell"),
    )

    assert buy.accepted is True
    assert sell.accepted is True

    buy_fill = next(item for item in session.accounting_records if item.idempotency_key == "fill-rec-key-4:fill")
    sell_fill = next(item for item in session.accounting_records if item.idempotency_key == "fill-rec-key-5:fill")
    assert Decimal(str(buy_fill.net_cash_impact)) < Decimal("0")
    assert Decimal(str(sell_fill.net_cash_impact)) > Decimal("0")


@pytest.mark.asyncio
async def test_canonical_reconciliation_discovers_provider_order_and_persists_fill_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _execution_event("execution_intent_created")
    profile = type("Profile", (), {
        "id": source.live_trading_profile_id,
        "paper_account_id": uuid.uuid4(),
        "operating_mode": "live",
        "paper_default_mode": True,
        "risk_authority_model": "risk_engine_final",
    })()
    live_order = type("LiveOrder", (), {
        "live_crypto_order_id": uuid.uuid4(),
        "provider": "coinbase_advanced",
        "environment": "production",
        "exchange_connection_id": uuid.uuid4(),
        "provider_order_id": None,
        "client_order_id": "client-order-1",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {"live_trading_profile_id": str(source.live_trading_profile_id)},
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    campaign = type("Campaign", (), {"id": 42, "paper_account_id": profile.paper_account_id, "updated_at": datetime.now(timezone.utc)})()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]
    session.capital_campaigns = [campaign]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "100.00"}],
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-1",
                        "client_order_id": "client-order-1",
                        "product_id": "BTC-USD",
                        "status": "FILLED",
                        "completion_time": "2026-07-10T12:00:00Z",
                    }
                ]
            }, {}

        async def list_historical_fills(self, **_kwargs):
            return {
                "fills": [
                    {
                        "trade_id": "fill-1",
                        "price": "100000.00",
                        "size": "0.00005",
                        "commission": "0.01",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:01Z",
                    }
                ]
            }, {}

        async def get_historical_order(self, **_kwargs):
            return {"order": {}}, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.CoinbaseAdvancedClient", _Provider)

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["provider_order_id"] == "provider-order-1"
    assert isinstance(outcome["provider_fill_observed"], bool)
    assert len(session.reconciliation_events) >= 2
    assert len(session.accounting_records) >= 2


@pytest.mark.asyncio
async def test_canonical_reconciliation_handles_duplicate_provider_fill_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])

    first = await record_live_fill_reconciliation(
        db=session,
        request=_fill_request(source, idempotency_key="dup-fill-key", provider_fill_id="fill-dup"),
    )
    second = await record_live_fill_reconciliation(
        db=session,
        request=_fill_request(source, idempotency_key="dup-fill-key", provider_fill_id="fill-dup"),
    )

    assert first.status == "recorded"
    assert second.status == "replayed"
    assert len(session.accounting_records) == 2


@pytest.mark.asyncio
async def test_order_reconciliation_can_record_unresolved_balance_mismatch_status() -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])

    result = await record_live_order_reconciliation(
        db=session,
        request=_order_request(
            source,
            idempotency_key="order-rec-balance-mismatch",
            reconciliation_status="balance_mismatch",
            provider_order_id=None,
        ),
    )

    assert result.accepted is True
    assert session.reconciliation_events[0].reconciliation_status == "balance_mismatch"
