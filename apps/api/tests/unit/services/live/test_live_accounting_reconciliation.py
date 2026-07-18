from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import InvalidRequestError

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
    """Mirrors real AsyncSession semantics: begin() while already begun raises."""

    def __init__(self, session: "_FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "_BeginContext":
        self._session._in_transaction = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._session._in_transaction = False


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
        self._in_transaction = False
        self.flush_fail_after: int | None = None
        self._flush_calls = 0

    def in_transaction(self) -> bool:
        return self._in_transaction

    def begin(self) -> _BeginContext:
        if self._in_transaction:
            raise InvalidRequestError("A transaction is already begun on this Session.")
        return _BeginContext(self)

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_reconciliation_events" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.reconciliation_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "FROM live_reconciliation_events" in sql and "id_1" in params:
            rec_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.reconciliation_events:
                if item.id == rec_id and item.live_trading_profile_id == profile_id:
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
            campaign_id = params.get("id_1")
            if campaign_id is not None:
                rows = [item for item in self.capital_campaigns if item.id == campaign_id]
                return rows[0] if rows else None
            account_id = params.get("paper_account_id_1")
            rows = [item for item in self.capital_campaigns if item.paper_account_id == account_id]
            return rows[0] if rows else None

        return None

    async def scalars(self, statement: Any) -> _ScalarRows:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_accounting_records" in sql:
            if "capital_campaign_id" in sql and "reconciliation_event_id" not in sql:
                order_id = params.get("live_crypto_order_id_1")
                campaign_ids = [
                    item.capital_campaign_id
                    for item in self.accounting_records
                    if getattr(item, "live_crypto_order_id", None) == order_id and getattr(item, "capital_campaign_id", None) is not None
                ]
                return _ScalarRows(campaign_ids)

            rec_id = params.get("reconciliation_event_id_1")
            order_id = params.get("live_crypto_order_id_1")
            rows = self.accounting_records
            if rec_id is not None:
                rows = [item for item in rows if item.reconciliation_event_id == rec_id]
            if order_id is not None:
                rows = [item for item in rows if getattr(item, "live_crypto_order_id", None) == order_id]
            return _ScalarRows(rows)

        if "FROM live_reconciliation_events" in sql and "capital_campaign_id" in sql:
            order_id = params.get("live_crypto_order_id_1")
            campaign_ids = [
                item.capital_campaign_id
                for item in self.reconciliation_events
                if getattr(item, "live_crypto_order_id", None) == order_id and getattr(item, "capital_campaign_id", None) is not None
            ]
            return _ScalarRows(campaign_ids)

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
        self._flush_calls += 1
        if self.flush_fail_after is not None and self._flush_calls > self.flush_fail_after:
            raise RuntimeError("simulated persistence failure")
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


def _build_reconciliation_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[_FakeSession, Any]:
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

    return session, live_order


class _NoSubmitProvider:
    """Base for reconciliation-path provider fakes: any submission attempt fails the test."""

    async def create_order(self, **_kwargs):
        raise AssertionError("reconciliation must never submit an order (AddOrder-equivalent)")

    async def add_order(self, **_kwargs):
        raise AssertionError("reconciliation must never submit an order (AddOrder-equivalent)")


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
                        "price": "90000.00",
                        "size": "0.00002",
                        "commission": "0.004",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:01Z",
                    },
                    {
                        "trade_id": "fill-2",
                        "price": "110000.00",
                        "size": "0.00003",
                        "commission": "0.006",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:02Z",
                    }
                ]
            }, {}

        async def get_historical_order(self, **_kwargs):
            return {"order": {}}, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["provider_order_id"] == "provider-order-1"
    assert outcome["provider_fill_observed"] is True
    assert outcome["reconciliation_status"] == "FILLED"
    assert outcome["filled_quantity"] == "0.00005"
    assert outcome["provider_fees"] == "0.010"
    assert outcome["safe_provider_response"]["weighted_average_fill_price"] == "102000.00"
    assert len(session.reconciliation_events) >= 3
    assert len(session.accounting_records) == 4


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


@pytest.mark.asyncio
async def test_canonical_reconciliation_marks_unresolved_when_balance_materially_mismatched(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-balance-mismatch",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
                "balances": [{"currency": "USD", "available": "95.10"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.01"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-balance-mismatch",
                        "client_order_id": "client-order-balance-mismatch",
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
                        "trade_id": "fill-balance-mismatch",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["accounting_completion_status"] == "unresolved"
    assert outcome["balance_mismatch_state"] == "material_mismatch"


@pytest.mark.asyncio
async def test_canonical_reconciliation_accepts_tolerated_rounding_balance_difference(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-rounding",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "94.989"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.02"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-rounding",
                        "client_order_id": "client-order-rounding",
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
                        "trade_id": "fill-rounding",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["balance_mismatch_state"] == "tolerated"
    assert outcome["accounting_completion_status"] == "complete"


@pytest.mark.asyncio
async def test_canonical_reconciliation_flags_campaign_correlation_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-campaign-mismatch",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "capital_campaign_id": 999,
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "95.00"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.01"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-campaign-mismatch",
                        "client_order_id": "client-order-campaign-mismatch",
                        "product_id": "BTC-USD",
                        "status": "CANCELLED",
                        "completion_time": "2026-07-10T12:00:00Z",
                    }
                ]
            }, {}

        async def list_historical_fills(self, **_kwargs):
            return {"fills": []}, {}

        async def get_historical_order(self, **_kwargs):
            return {"order": {}}, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["campaign_correlation_status"] == "mismatch"
    assert outcome["accounting_completion_status"] == "unresolved"


@pytest.mark.asyncio
async def test_canonical_reconciliation_resolves_verified_campaign_persisted_at_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the campaign-attribution gap: once submit() persists the
    authoritative capital_campaign_id (see live_crypto_orders.submit), reconciliation
    must resolve it to 'verified' rather than 'uncategorized', and must stamp that
    campaign id onto the reconciliation/accounting evidence it writes."""
    source = _execution_event("execution_intent_created")
    paper_account_id = uuid.uuid4()
    profile = type("Profile", (), {
        "id": source.live_trading_profile_id,
        "paper_account_id": paper_account_id,
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
        "client_order_id": "client-order-campaign-verified",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "capital_campaign_id": 501,
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]
    session.capital_campaigns = [
        type("Campaign", (), {"id": 501, "paper_account_id": paper_account_id})()
    ]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "94.995"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.01"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-campaign-verified",
                        "client_order_id": "client-order-campaign-verified",
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
                        "order_id": "provider-order-campaign-verified",
                        "product_id": "BTC-USD",
                        "size": "0.0001",
                        "price": "50000",
                        "commission": "0.005",
                        "commission_currency": "USD",
                        "completion_time": "2026-07-10T12:00:00Z",
                    }
                ]
            }, {}

        async def get_historical_order(self, **_kwargs):
            return {
                "order": {
                    "order_id": "provider-order-campaign-verified",
                    "client_order_id": "client-order-campaign-verified",
                    "product_id": "BTC-USD",
                    "status": "FILLED",
                    "completion_time": "2026-07-10T12:00:00Z",
                }
            }, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["campaign_correlation_status"] == "verified"
    assert outcome["accounting_completion_status"] == "complete"
    assert all(event.capital_campaign_id == 501 for event in session.reconciliation_events)
    assert all(record.capital_campaign_id == 501 for record in session.accounting_records)

    replay_outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )
    assert replay_outcome["campaign_correlation_status"] == "verified"
    assert replay_outcome["accounting_completion_status"] == "complete"


@pytest.mark.asyncio
async def test_canonical_reconciliation_audit_failure_marks_unresolved_and_retry_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-audit-retry",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "94.99"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.02"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-audit-retry",
                        "client_order_id": "client-order-audit-retry",
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
                        "trade_id": "fill-audit-retry",
                        "price": "100000.00",
                        "size": "0.00005",
                        "commission": "0.01",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:01Z",
                    }
                ]
            }, {}

        async def get_historical_order(self, **_kwargs):
            return {
                "order": {
                    "order_id": "provider-order-audit-retry",
                    "client_order_id": "client-order-audit-retry",
                    "product_id": "BTC-USD",
                    "status": "FILLED",
                    "completion_time": "2026-07-10T12:00:00Z",
                }
            }, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    call_count = {"audit": 0}

    async def _audit_once_then_ok(*_args, **_kwargs):
        call_count["audit"] += 1
        if call_count["audit"] == 1:
            raise RuntimeError("audit persistence unavailable")
        return None

    monkeypatch.setattr("app.services.live.accounting_reconciliation.record_live_audit_evidence", _audit_once_then_ok)

    first = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )
    second = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert first["accounting_completion_status"] == "unresolved"
    assert second["accounting_completion_status"] == "complete"
    assert len(session.accounting_records) == 2
    unresolved_events = [
        item for item in session.reconciliation_events
        if isinstance(item.provenance, dict) and item.provenance.get("reason") == "audit_persistence_failure"
    ]
    assert len(unresolved_events) == 1


@pytest.mark.asyncio
async def test_canonical_reconciliation_marks_unresolved_when_balance_observation_stale(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-stale-balance",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "94.99"}],
            "last_verified_at": "2026-07-10T11:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T11:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T11:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.02"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-stale-balance",
                        "client_order_id": "client-order-stale-balance",
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
                        "trade_id": "fill-stale-balance",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["balance_mismatch_state"] == "stale"
    assert outcome["accounting_completion_status"] == "unresolved"


@pytest.mark.asyncio
async def test_canonical_reconciliation_marks_unresolved_when_balance_missing(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-missing-balance",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.02"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-missing-balance",
                        "client_order_id": "client-order-missing-balance",
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
                        "trade_id": "fill-missing-balance",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["balance_mismatch_state"] == "missing"
    assert outcome["accounting_completion_status"] == "unresolved"


@pytest.mark.asyncio
async def test_canonical_reconciliation_marks_mismatch_when_campaign_ids_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _execution_event("execution_intent_created")
    profile = type("Profile", (), {
        "id": source.live_trading_profile_id,
        "paper_account_id": uuid.uuid4(),
        "operating_mode": "live",
        "paper_default_mode": True,
        "risk_authority_model": "risk_engine_final",
    })()
    live_order_id = uuid.uuid4()
    live_order = type("LiveOrder", (), {
        "live_crypto_order_id": live_order_id,
        "provider": "coinbase_advanced",
        "environment": "production",
        "exchange_connection_id": uuid.uuid4(),
        "provider_order_id": None,
        "client_order_id": "client-order-campaign-conflict",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "capital_campaign_id": 11,
            "usd_available_before_submit": "100.00",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]
    session.accounting_records = [
        LiveAccountingRecord(
            id=uuid.uuid4(),
            idempotency_key="existing-campaign-row",
            live_trading_profile_id=source.live_trading_profile_id,
            live_crypto_order_id=live_order_id,
            capital_campaign_id=10,
            reconciliation_event_id=uuid.uuid4(),
            source_execution_event_id=source.id,
            source_execution_event_type="execution_intent_created",
            record_type="fill_accounting",
            provider_order_id="provider-order-existing",
            provider_fill_id="fill-existing",
            symbol="BTC-USD",
            side="buy",
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("100000"),
            gross_notional=Decimal("1000"),
            fee_amount=Decimal("0"),
            fee_currency="USD",
            net_cash_impact=Decimal("-1000"),
            provenance={"source": "test"},
            recorded_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
    ]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "95.00"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.01"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-campaign-conflict",
                        "client_order_id": "client-order-campaign-conflict",
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
                        "trade_id": "fill-campaign-conflict",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["campaign_correlation_status"] == "mismatch"
    assert outcome["accounting_completion_status"] == "unresolved"


@pytest.mark.asyncio
async def test_canonical_reconciliation_profit_cycle_consistency_for_buy_fill_is_non_realizing(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "client_order_id": "client-order-profit-cycle",
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quote_size": Decimal("5.00"),
        "status": "RECONCILIATION_REQUIRED",
        "risk_event_id": uuid.uuid4(),
        "safe_provider_response": {
            "live_trading_profile_id": str(source.live_trading_profile_id),
            "usd_available_before_submit": "100.00",
            "preview_estimated_fee": "0.01",
        },
        "audit_correlation_id": uuid.uuid4(),
        "provider_status": None,
        "updated_at": datetime.now(timezone.utc),
        "filled_at": None,
        "cancelled_at": None,
        "failure_code": None,
        "failure_reason": None,
        "acknowledged_at": None,
    })()
    session = _FakeSession(execution_events=[source])
    session.live_orders = [live_order]
    session.live_profiles = [profile]

    async def _load_exchange_connection(*_args, **_kwargs):
        return type("Conn", (), {
            "exchange_connection_id": live_order.exchange_connection_id,
            "balances": [{"currency": "USD", "available": "94.99"}],
            "last_verified_at": "2026-07-10T12:00:05+00:00",
            "last_successful_sync_at": "2026-07-10T12:00:05+00:00",
            "last_heartbeat_at": "2026-07-10T12:00:05+00:00",
        })()

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation._utcnow",
        lambda: datetime(2026, 7, 10, 12, 0, 10, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "live_crypto_balance_max_age_seconds": 30,
                "live_crypto_accounting_balance_tolerance_usd": Decimal("0.02"),
            },
        )(),
    )

    class _Provider:
        async def list_historical_orders(self, **_kwargs):
            return {
                "orders": [
                    {
                        "order_id": "provider-order-profit-cycle",
                        "client_order_id": "client-order-profit-cycle",
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
                        "trade_id": "fill-profit-cycle",
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

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    consistency = live_order.safe_provider_response["reconciliation"]["profit_cycle_consistency"]
    assert consistency["buy_fill_realized_profit"] == "0"
    assert consistency["distributable_profit_created"] is False
    assert consistency["fees_reflected"] is True


@pytest.mark.asyncio
async def test_reconciliation_persists_atomically_when_session_already_has_active_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the production incident: the request session already owns a
    transaction (SQLAlchemy autobegin from earlier queries) before
    reconcile_live_order_and_fills ever runs. Every nested persistence helper
    (record_live_order_reconciliation, record_live_fill_reconciliation,
    record_live_audit_evidence) must join that transaction rather than call
    db.begin() again, or this raises InvalidRequestError just like production did.
    """
    session, live_order = _build_reconciliation_fixture(monkeypatch)
    session._in_transaction = True

    class _Provider(_NoSubmitProvider):
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
                        "price": "90000.00",
                        "size": "0.00002",
                        "commission": "0.004",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:01Z",
                    },
                    {
                        "trade_id": "fill-2",
                        "price": "110000.00",
                        "size": "0.00003",
                        "commission": "0.006",
                        "commission_currency": "USD",
                        "created_at": "2026-07-10T12:00:02Z",
                    },
                ]
            }, {}

        async def get_historical_order(self, **_kwargs):
            return {"order": {}}, {}

    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: _Provider())

    outcome = await reconcile_live_order_and_fills(
        db=session,
        live_crypto_order_id=live_order.live_crypto_order_id,
        operator_identity="operator:human",
    )

    assert outcome["reconciliation_status"] == "FILLED"
    assert outcome["provider_fill_observed"] is True
    assert len(session.reconciliation_events) >= 3
    assert len(session.accounting_records) == 4
    assert session.commits == 0
    assert session._in_transaction is True


@pytest.mark.asyncio
async def test_reconciliation_provider_lookup_failure_leaves_no_partial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, live_order = _build_reconciliation_fixture(monkeypatch)

    class _FailingProvider(_NoSubmitProvider):
        async def list_historical_orders(self, **_kwargs):
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_exchange_provider",
        lambda *_args, **_kwargs: _FailingProvider(),
    )

    with pytest.raises(RuntimeError, match="provider unreachable"):
        await reconcile_live_order_and_fills(
            db=session,
            live_crypto_order_id=live_order.live_crypto_order_id,
            operator_identity="operator:human",
        )

    assert session.reconciliation_events == []
    assert session.accounting_records == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_fill_reconciliation_persistence_failure_propagates_without_commit() -> None:
    source = _execution_event("execution_intent_created")
    session = _FakeSession(execution_events=[source])
    session.flush_fail_after = 0

    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        await record_live_fill_reconciliation(
            db=session,
            request=_fill_request(source, idempotency_key="fill-rec-key-persistence-failure"),
        )

    assert session.commits == 0
