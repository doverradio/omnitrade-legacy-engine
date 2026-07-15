from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.live import venue_commissioning as vc
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient


class _FakeDb:
    def __init__(self) -> None:
        self.connection = None
        self.profile = None
        self.run = None
        self.open_live_order = None
        self.global_switch = None
        self.account_switch = None
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM exchange_connections" in sql:
            return self.connection
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM risk_kill_switches" in sql and "scope = :scope_1" in sql:
            return self.global_switch
        if "FROM risk_kill_switches" in sql and "scope = :scope_1" not in sql:
            return self.account_switch
        if "FROM live_crypto_orders" in sql:
            return self.open_live_order
        if "FROM venue_commissioning_runs" in sql:
            return self.run
        return None

    async def scalars(self, statement):
        sql = str(statement)
        if "FROM live_trading_profiles" in sql:
            return [self.profile] if self.profile is not None else []
        if "FROM venue_commissioning_runs" in sql:
            return [self.run] if self.run is not None else []
        return []

    def add(self, item):
        self.added.append(item)
        if item.__class__.__name__ == "VenueCommissioningRun":
            self.run = item

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


class _ProviderStub:
    def __init__(self, *, preview_success: bool = True) -> None:
        self.preview_success = preview_success

    async def fetch_product(self, **_kwargs):
        return SimpleNamespace(available=True, trading_enabled=True)

    async def preview_market_order(self, **_kwargs):
        return SimpleNamespace(
            success=self.preview_success,
            exchange_response_summary={"pair_decimals": 1, "lot_decimals": 8},
            best_ask=Decimal("100000"),
        )


@pytest.mark.asyncio
async def test_readiness_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.connection = SimpleNamespace(
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        balances=[{"currency": "USD", "available": "10.00"}, {"currency": "BTC", "available": "0"}],
    )
    db.profile = SimpleNamespace(paper_account_id=uuid.uuid4(), provenance_metadata={"provider": "kraken_spot", "exchange_environment": "production"})

    monkeypatch.setattr(vc, "get_settings", lambda: SimpleNamespace(venue_commissioning_enabled=True))
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: _ProviderStub(preview_success=True))
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    result = await vc.evaluate_readiness(
        db=db,
        config=vc.CommissioningConfig(
            provider="kraken_spot",
            product_id="BTC-USD",
            environment="production",
            amount=Decimal("5.00"),
            hold_minutes=30,
        ),
    )

    assert result.would_activate_safely is True
    assert db.commits == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_readiness_blocks_amount_above_max(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(vc, "get_settings", lambda: SimpleNamespace(venue_commissioning_enabled=True))

    result = await vc.evaluate_readiness(
        db=db,
        config=vc.CommissioningConfig(
            provider="kraken_spot",
            product_id="BTC-USD",
            environment="production",
            amount=Decimal("5.01"),
            hold_minutes=30,
        ),
    )

    assert result.would_activate_safely is False
    assert result.exact_blocker in {"scope_mismatch", "invalid_credentials"}


@pytest.mark.asyncio
async def test_readiness_blocks_when_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.connection = SimpleNamespace(
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        credentials_encrypted="{}",
        balances=[{"currency": "USD", "available": "10.00"}],
    )
    monkeypatch.setattr(vc, "get_settings", lambda: SimpleNamespace(venue_commissioning_enabled=False))
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: _ProviderStub(preview_success=True))
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    result = await vc.evaluate_readiness(
        db=db,
        config=vc.CommissioningConfig(
            provider="kraken_spot",
            product_id="BTC-USD",
            environment="production",
            amount=Decimal("5.00"),
            hold_minutes=30,
        ),
    )

    assert result.would_activate_safely is False
    assert result.exact_blocker == "venue_commissioning_gate_disabled"


@pytest.mark.asyncio
async def test_activation_does_not_submit_order(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()

    async def _ready(**_kwargs):
        return vc.ReadinessResult(would_activate_safely=True, exact_blocker=None, checks=[], existing_active_run="NONE")

    async def _no_submit(**_kwargs):
        raise AssertionError("activation must not submit")

    monkeypatch.setattr(vc, "evaluate_readiness", _ready)
    monkeypatch.setattr(vc, "_submit_order", _no_submit)

    run = await vc.activate_run(
        db=db,
        actor="operator:human",
        config=vc.CommissioningConfig(
            provider="kraken_spot",
            product_id="BTC-USD",
            environment="production",
            amount=Decimal("5.00"),
            hold_minutes=30,
        ),
        confirm=True,
    )

    assert run.status == "ACTIVE"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_activation_requires_confirm() -> None:
    db = _FakeDb()
    with pytest.raises(PermissionError):
        await vc.activate_run(
            db=db,
            actor="operator:human",
            config=vc.CommissioningConfig(
                provider="kraken_spot",
                product_id="BTC-USD",
                environment="production",
                amount=Decimal("5.00"),
                hold_minutes=30,
            ),
            confirm=False,
        )


@pytest.mark.asyncio
async def test_start_prevents_duplicate_buy() -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="ACTIVE",
        buy_client_order_id="already-present",
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
    )

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "BUY_RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_start_after_buy_acceptance_does_not_resubmit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="ACTIVE",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        buy_client_order_id="kff-accepted-buy",
        buy_provider_order_id="ord-1",
        buy_submitted_at=datetime.now(timezone.utc),
        buy_idempotency_key="kff-accepted-buy",
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
    )

    async def _submit(*_args, **_kwargs):
        raise AssertionError("start must not submit a second BUY")

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(vc, "_submit_order", _submit)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "BUY_RECONCILIATION_REQUIRED"


def test_invalid_transition_is_rejected() -> None:
    run = SimpleNamespace(status="ACTIVE")

    with pytest.raises(RuntimeError, match="invalid_transition"):
        vc._transition(run, "COMPLETED")


@pytest.mark.asyncio
async def test_resume_skips_active_not_started_run() -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="BUY_RECONCILIATION_REQUIRED",
        activated_at=datetime.now(timezone.utc),
        started_at=None,
    )

    processed = await vc.resume_runs(db=db, actor="orchestration_worker", limit=10)

    assert processed == 0


@pytest.mark.asyncio
async def test_resume_processes_started_run() -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="BUY_RECONCILIATION_REQUIRED",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        activated_at=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
        buy_client_order_id="kff-buy",
        buy_provider_order_id="ord-1",
        buy_idempotency_key="kff-buy",
        buy_submitted_at=now - timedelta(minutes=1),
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by="operator:human",
        updated_at=now,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
        buy_filled_quote_usd=None,
        buy_fee_usd=None,
        buy_avg_price_usd=None,
        buy_filled_at=None,
        hold_started_at=None,
        hold_due_at=None,
        sell_provider_order_id=None,
        sell_idempotency_key=None,
        sell_submitted_at=None,
        sell_requested_base_btc=None,
        sell_filled_base_btc=None,
        sell_filled_quote_usd=None,
        sell_fee_usd=None,
        sell_avg_price_usd=None,
        sell_filled_at=None,
        gross_pnl_usd=None,
        total_fees_usd=None,
        net_realized_pnl_usd=None,
        dust_base_btc=None,
        ledger_matches_kraken=False,
        completed_at=None,
        execution_purpose="VENUE_COMMISSIONING",
    )

    fill = SimpleNamespace(size=Decimal("0.00005"), price=Decimal("100000"), fee=SimpleNamespace(amount=Decimal("0.01")), occurred_at=now)

    async def _reconcile(**_kwargs):
        return "FILLED", SimpleNamespace(provider_order_id="ord-1"), [fill]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)
    try:
        processed = await vc.resume_runs(db=db, actor="orchestration_worker", limit=10)
    finally:
        monkeypatch.undo()

    assert processed == 1
    assert db.run.status in {"HOLDING", "SELL_DUE", "SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED"}


@pytest.mark.asyncio
async def test_resume_ignores_completed_run() -> None:
    class _CompletedDb(_FakeDb):
        async def scalars(self, statement):
            _ = statement
            return []

    db = _CompletedDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="COMPLETED",
        activated_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
    )

    processed = await vc.resume_runs(db=db, actor="orchestration_worker", limit=10)

    assert processed == 0


@pytest.mark.asyncio
async def test_start_ambiguous_buy_enters_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="ACTIVE",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        buy_client_order_id=None,
        buy_provider_order_id=None,
        buy_idempotency_key=None,
        buy_submitted_at=None,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
    )

    async def _submit(**_kwargs):
        return "AMBIGUOUS", None, None, {}

    monkeypatch.setattr(vc, "_submit_order", _submit)

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status in {"BUY_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED"}
    assert run.buy_submitted_at is not None


def test_client_order_id_is_deterministic_uuid5_for_buy_and_sell() -> None:
    run_id = uuid.UUID("64e28ade-5705-4493-8e53-c3d86f28da97")

    buy_one = vc._build_client_order_id(run_id=run_id, side="BUY")
    buy_two = vc._build_client_order_id(run_id=run_id, side="BUY")
    sell_one = vc._build_client_order_id(run_id=run_id, side="SELL")
    sell_two = vc._build_client_order_id(run_id=run_id, side="SELL")

    buy_uuid = uuid.UUID(buy_one)
    sell_uuid = uuid.UUID(sell_one)

    assert str(buy_uuid) == buy_one
    assert str(sell_uuid) == sell_one
    assert buy_uuid.version == 5
    assert sell_uuid.version == 5
    assert buy_one == buy_two
    assert sell_one == sell_two
    assert buy_one != sell_one


@pytest.mark.asyncio
async def test_start_replay_keeps_existing_client_order_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    run_id = uuid.uuid4()
    seeded_id = vc._build_client_order_id(run_id=run_id, side="BUY")
    db.run = SimpleNamespace(
        commissioning_run_id=run_id,
        status="ACTIVE",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        buy_client_order_id=seeded_id,
        buy_provider_order_id=None,
        buy_submitted_at=datetime.now(timezone.utc),
        buy_idempotency_key=seeded_id,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
    )

    async def _submit(*_args, **_kwargs):
        raise AssertionError("start replay must not submit a second BUY")

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(vc, "_submit_order", _submit)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    run = await vc.start_run(db=db, actor="operator:human", run_id=run_id, confirm=True)

    assert run.buy_client_order_id == seeded_id


@pytest.mark.asyncio
async def test_start_submit_payload_uses_staged_uuid_and_validate_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    run_id = uuid.uuid4()
    db.connection = SimpleNamespace(provider="kraken_spot", environment="production")
    db.run = SimpleNamespace(
        commissioning_run_id=run_id,
        status="ACTIVE",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        buy_client_order_id=None,
        buy_provider_order_id=None,
        buy_idempotency_key=None,
        buy_submitted_at=None,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
        buy_filled_quote_usd=None,
        buy_fee_usd=None,
        buy_avg_price_usd=None,
        buy_filled_at=None,
        hold_started_at=None,
        hold_due_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        sell_provider_order_id=None,
        sell_idempotency_key=None,
        sell_submitted_at=None,
        sell_requested_base_btc=None,
    )

    client = KrakenSpotClient()
    captured_paths: list[str] = []
    captured_add_order_payload: dict[str, str] = {}

    async def _public(*, path, **_kwargs):
        if path == "/public/AssetPairs":
            return {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "altname": "XBTUSD",
                        "wsname": "XBT/USD",
                        "base": "BTC",
                        "quote": "USD",
                        "status": "online",
                        "pair_decimals": 1,
                        "lot_decimals": 8,
                        "ordermin": "0.00005",
                        "costmin": "0.5",
                    }
                },
            }
        if path == "/public/Ticker":
            return {"error": [], "result": {"XXBTZUSD": {"a": ["50000.0", "1", "1"], "b": ["49999.0", "1", "1"]}}}
        raise AssertionError(f"unexpected public path {path}")

    async def _private(*, path, payload, **_kwargs):
        captured_paths.append(path)
        if path != "/private/AddOrder":
            raise AssertionError(f"unexpected private path {path}")
        captured_add_order_payload.clear()
        captured_add_order_payload.update(payload)
        return {"error": [], "result": {"txid": ["O-1"], "descr": {"order": "buy market"}}}

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    run = await vc.start_run(db=db, actor="operator:human", run_id=run_id, confirm=True)

    expected_validate_payload = {
        "ordertype": "market",
        "type": "buy",
        "pair": "XBTUSD",
        "volume": "5.0",
        "oflags": "fciq,viqc",
        "cl_ord_id": run.buy_client_order_id,
        "validate": "true",
    }
    expected_live_payload = {key: value for key, value in expected_validate_payload.items() if key != "validate"}

    assert run.buy_client_order_id == vc._build_client_order_id(run_id=run_id, side="BUY")
    assert run.buy_client_order_id.startswith("kff-") is False
    assert captured_paths == ["/private/AddOrder"]
    assert captured_add_order_payload == expected_live_payload
    assert "validate" not in captured_add_order_payload
    assert "timeinforce" not in captured_add_order_payload


@pytest.mark.asyncio
async def test_reconcile_uses_stored_buy_client_order_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    run_id = uuid.uuid4()
    buy_id = vc._build_client_order_id(run_id=run_id, side="BUY")
    db.connection = SimpleNamespace(provider="kraken_spot", environment="production")
    run = SimpleNamespace(
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        buy_provider_order_id="provider-order-1",
        sell_provider_order_id=None,
        buy_client_order_id=buy_id,
        sell_client_order_id=None,
    )

    captured_lookup: dict[str, object] = {}

    class _LookupProvider:
        async def lookup_order(self, **kwargs):
            captured_lookup.update(kwargs)
            return SimpleNamespace(provider_order_id="provider-order-1", status="OPEN")

        async def list_fills(self, **_kwargs):
            return []

    provider = _LookupProvider()
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    status, order, fills = await vc._reconcile_order(db=db, run=run, side="BUY")

    assert status == "OPEN"
    assert order is not None
    assert fills == []
    assert captured_lookup.get("client_order_id") == buy_id


@pytest.mark.asyncio
async def test_resume_with_lookup_missing_and_fill_present_advances_buy_to_holding(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    buy_id = vc._build_client_order_id(run_id=run_id, side="BUY")
    db.connection = SimpleNamespace(provider="kraken_spot", environment="production")
    db.run = SimpleNamespace(
        commissioning_run_id=run_id,
        status="BUY_RECONCILIATION_REQUIRED",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        activated_at=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
        buy_client_order_id=buy_id,
        buy_provider_order_id="OS6BOV-CAHMZ-55ASP4",
        buy_idempotency_key=buy_id,
        buy_submitted_at=now - timedelta(minutes=1),
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by="operator:human",
        updated_at=now,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
        buy_filled_quote_usd=None,
        buy_fee_usd=None,
        buy_avg_price_usd=None,
        buy_filled_at=None,
        hold_started_at=None,
        hold_due_at=None,
        sell_provider_order_id=None,
        sell_idempotency_key=None,
        sell_submitted_at=None,
        sell_requested_base_btc=None,
        sell_filled_base_btc=None,
        sell_filled_quote_usd=None,
        sell_fee_usd=None,
        sell_avg_price_usd=None,
        sell_filled_at=None,
        gross_pnl_usd=None,
        total_fees_usd=None,
        net_realized_pnl_usd=None,
        dust_base_btc=None,
        ledger_matches_kraken=False,
        completed_at=None,
        execution_purpose="VENUE_COMMISSIONING",
    )

    captured: dict[str, object] = {"lookup_calls": 0, "fill_calls": 0}

    class _ProviderStubForResume:
        def supports_capability(self, _cap: str) -> bool:
            return True

        async def lookup_order(self, **kwargs):
            captured["lookup_calls"] = int(captured.get("lookup_calls", 0)) + 1
            captured["lookup_kwargs"] = kwargs
            return None

        async def list_fills(self, **kwargs):
            captured["fill_calls"] = int(captured.get("fill_calls", 0)) + 1
            captured["fill_kwargs"] = kwargs
            return [
                SimpleNamespace(
                    size=Decimal("0.00007717"),
                    price=Decimal("64788.10"),
                    fee=SimpleNamespace(amount=Decimal("0.04")),
                    occurred_at=now,
                )
            ]

    provider = _ProviderStubForResume()
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    processed = await vc.resume_runs(db=db, actor="orchestration_worker", limit=10)

    assert processed == 1
    assert captured["lookup_calls"] == 1
    assert captured["fill_calls"] == 1
    assert db.run.status == "HOLDING"
    assert db.run.buy_filled_at == now
    assert db.run.buy_filled_base_btc == Decimal("0.00007717")
    assert db.run.buy_filled_quote_usd == Decimal("4.99")
    assert db.run.buy_avg_price_usd == Decimal("64788.10")
    assert db.run.buy_fee_usd == Decimal("0.04")
    assert db.run.hold_started_at == now
    assert db.run.hold_due_at == now + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_buy_fill_transitions_to_holding(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="BUY_RECONCILIATION_REQUIRED",
        buy_client_order_id="kff-buy",
        buy_provider_order_id="ord-1",
        buy_idempotency_key="kff-buy",
        buy_submitted_at=now,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
        buy_filled_quote_usd=None,
        buy_fee_usd=None,
        buy_avg_price_usd=None,
        buy_filled_at=None,
    )

    fill = SimpleNamespace(size=Decimal("0.00005"), price=Decimal("100000"), fee=SimpleNamespace(amount=Decimal("0.01")), occurred_at=now)

    async def _reconcile(**_kwargs):
        return "FILLED", SimpleNamespace(provider_order_id="ord-1"), [fill]

    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status in {"HOLDING", "SELL_DUE", "SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED"}
    assert run.buy_filled_base_btc is not None


@pytest.mark.asyncio
async def test_buy_fills_without_lookup_order_still_transition_to_holding(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="BUY_RECONCILIATION_REQUIRED",
        buy_client_order_id=vc._build_client_order_id(run_id=uuid.uuid4(), side="BUY"),
        buy_provider_order_id="OS6BOV-CAHMZ-55ASP4",
        buy_idempotency_key="reconcile-buy-id",
        buy_submitted_at=now,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
        buy_filled_base_btc=None,
        buy_filled_quote_usd=None,
        buy_fee_usd=None,
        buy_avg_price_usd=None,
        buy_filled_at=None,
        hold_started_at=None,
        hold_due_at=None,
    )

    fill = SimpleNamespace(size=Decimal("0.00007717"), price=Decimal("64788.10"), fee=SimpleNamespace(amount=Decimal("0.04")), occurred_at=now)

    async def _reconcile(**_kwargs):
        return "UNKNOWN", None, [fill]

    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "HOLDING"
    assert run.buy_filled_at == now
    assert run.buy_filled_base_btc == Decimal("0.00007717")
    assert run.buy_filled_quote_usd == Decimal("4.99")
    assert run.buy_avg_price_usd == Decimal("64788.10")
    assert run.buy_fee_usd == Decimal("0.04")
    assert run.hold_started_at == now
    assert run.hold_due_at == now + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_sell_requires_reconciled_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="SELL_DUE",
        buy_filled_base_btc=None,
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=None,
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=None,
    )

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_revoke_before_buy_sets_revoked() -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="ACTIVE",
        revoked_by=None,
        revoked_reason=None,
        updated_at=None,
    )

    run = await vc.revoke_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "REVOKED"
    assert run.revoked_by == "operator:human"


@pytest.mark.asyncio
async def test_revoke_after_buy_requires_manual_review() -> None:
    db = _FakeDb()
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="BUY_SUBMISSION_PENDING",
        manual_intervention_required=False,
        revoked_by=None,
        revoked_reason=None,
        updated_at=None,
    )

    run = await vc.revoke_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "MANUAL_REVIEW_REQUIRED"
    assert run.manual_intervention_required is True


@pytest.mark.asyncio
async def test_completed_run_has_fee_adjusted_pnl(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.run = SimpleNamespace(
        commissioning_run_id=uuid.uuid4(),
        status="SELL_RECONCILIATION_REQUIRED",
        buy_filled_base_btc=Decimal("0.00005"),
        buy_filled_quote_usd=Decimal("5.00"),
        buy_fee_usd=Decimal("0.01"),
        buy_requested_quote_usd=Decimal("5.00"),
        hold_minutes=30,
        state_payload={},
        started_by=None,
        started_at=now - timedelta(minutes=31),
        updated_at=None,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id="kff-sell",
        sell_provider_order_id="ord-2",
        sell_requested_base_btc=Decimal("0.00005"),
        sell_filled_base_btc=None,
        sell_filled_quote_usd=None,
        sell_fee_usd=None,
        sell_avg_price_usd=None,
        sell_filled_at=None,
        gross_pnl_usd=None,
        total_fees_usd=None,
        net_realized_pnl_usd=None,
        dust_base_btc=None,
        ledger_matches_kraken=False,
        completed_at=None,
        hold_due_at=now - timedelta(minutes=1),
    )

    fill = SimpleNamespace(size=Decimal("0.00005"), price=Decimal("100200"), fee=SimpleNamespace(amount=Decimal("0.01")), occurred_at=now)

    async def _reconcile(**_kwargs):
        return "FILLED", SimpleNamespace(provider_order_id="ord-2"), [fill]

    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    run = await vc.start_run(db=db, actor="operator:human", run_id=db.run.commissioning_run_id, confirm=True)

    assert run.status == "COMPLETED"
    assert run.net_realized_pnl_usd is not None
    assert run.total_fees_usd == Decimal("0.02")


@pytest.mark.asyncio
async def test_resume_with_sell_lookup_missing_and_fill_present_completes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    buy_id = vc._build_client_order_id(run_id=run_id, side="BUY")
    sell_id = vc._build_client_order_id(run_id=run_id, side="SELL")
    db.connection = SimpleNamespace(provider="kraken_spot", environment="production")
    db.run = SimpleNamespace(
        commissioning_run_id=run_id,
        status="SELL_RECONCILIATION_REQUIRED",
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        activated_at=now - timedelta(minutes=31),
        started_at=now - timedelta(minutes=31),
        buy_client_order_id=buy_id,
        buy_provider_order_id="OS6BOV-CAHMZ-55ASP4",
        buy_idempotency_key=buy_id,
        buy_submitted_at=now - timedelta(minutes=31),
        buy_requested_quote_usd=Decimal("5.00"),
        buy_filled_base_btc=Decimal("0.00007717"),
        buy_filled_quote_usd=Decimal("4.99"),
        buy_fee_usd=Decimal("0.04"),
        buy_avg_price_usd=Decimal("64788.10"),
        buy_filled_at=now - timedelta(minutes=31),
        hold_minutes=30,
        hold_started_at=now - timedelta(minutes=31),
        hold_due_at=now - timedelta(minutes=1),
        state_payload={},
        started_by="operator:human",
        updated_at=now,
        duplicate_orders_detected=False,
        manual_intervention_required=False,
        sell_client_order_id=sell_id,
        sell_provider_order_id="ORJADU-6RIVB-RCVQME",
        sell_idempotency_key=sell_id,
        sell_submitted_at=now - timedelta(minutes=1),
        sell_requested_base_btc=Decimal("0.00007717"),
        sell_filled_base_btc=None,
        sell_filled_quote_usd=None,
        sell_fee_usd=None,
        sell_avg_price_usd=None,
        sell_filled_at=None,
        gross_pnl_usd=None,
        total_fees_usd=None,
        net_realized_pnl_usd=None,
        dust_base_btc=None,
        ledger_matches_kraken=False,
        completed_at=None,
        execution_purpose="VENUE_COMMISSIONING",
    )

    captured: dict[str, object] = {"lookup_calls": 0, "fill_calls": 0}

    class _ProviderStubForSellResume:
        def supports_capability(self, _cap: str) -> bool:
            return True

        async def lookup_order(self, **kwargs):
            captured["lookup_calls"] = int(captured.get("lookup_calls", 0)) + 1
            captured["lookup_kwargs"] = kwargs
            return None

        async def list_fills(self, **kwargs):
            captured["fill_calls"] = int(captured.get("fill_calls", 0)) + 1
            captured["fill_kwargs"] = kwargs
            return [
                SimpleNamespace(
                    size=Decimal("0.00007717"),
                    price=Decimal("64700.00"),
                    fee=SimpleNamespace(amount=Decimal("0.03")),
                    occurred_at=now,
                )
            ]

    provider = _ProviderStubForSellResume()
    monkeypatch.setattr(vc, "get_exchange_provider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda *_args, **_kwargs: {})

    processed = await vc.resume_runs(db=db, actor="orchestration_worker", limit=10)

    assert processed == 1
    assert captured["lookup_calls"] == 1
    assert captured["fill_calls"] == 1
    assert db.run.status == "COMPLETED"
    assert db.run.sell_filled_at == now
    assert db.run.sell_filled_base_btc == Decimal("0.00007717")
    assert db.run.sell_filled_quote_usd == Decimal("4.99")
    assert db.run.sell_avg_price_usd == Decimal("64700.00")
    assert db.run.sell_fee_usd == Decimal("0.03")
    assert db.run.gross_pnl_usd == Decimal("0.00")
    assert db.run.total_fees_usd == Decimal("0.07")
    assert db.run.net_realized_pnl_usd == Decimal("-0.07")
    assert db.run.dust_base_btc == Decimal("0.00000000")
    assert db.run.ledger_matches_kraken is True
    assert db.run.completed_at is not None
