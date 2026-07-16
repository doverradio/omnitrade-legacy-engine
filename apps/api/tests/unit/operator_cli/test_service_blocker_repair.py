from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.operator_cli.service as service


class _Tx:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db
        self._definition_before = None
        self._added_before = 0

    async def __aenter__(self) -> "_Tx":
        await self._db._tx_lock.acquire()
        self._definition_before = (
            self._db.definition.maximum_position_size,
            self._db.definition.maximum_total_exposure,
            self._db.definition.updated_at,
        )
        self._added_before = len(self._db.added)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._db.definition.maximum_position_size = self._definition_before[0]
            self._db.definition.maximum_total_exposure = self._definition_before[1]
            self._db.definition.updated_at = self._definition_before[2]
            del self._db.added[self._added_before :]
            await self._db.rollback()
        self._db._tx_lock.release()
        _ = exc, tb
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.connection = SimpleNamespace(
            exchange_connection_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            updated_at=datetime.now(timezone.utc),
        )
        self.definition = SimpleNamespace(
            campaign_id=uuid4(),
            version=1,
            maximum_position_size=Decimal("10"),
            maximum_total_exposure=Decimal("12"),
            minimum_position_size=Decimal("5"),
            maximum_open_positions=2,
            deployed_capital=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        self.runtime = SimpleNamespace(
            id=77,
            uuid=self.definition.campaign_id,
            definition_version=1,
        )
        self.active_package_count = 0
        self.active_activation_count = 0
        self.non_compliant_activation_count = 0
        self.open_live_order_count = 0
        self.unresolved_reconciliation_count = 0
        self.audits: list[SimpleNamespace] = []
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self._tx_lock = asyncio.Lock()

    def begin(self) -> _Tx:
        return _Tx(self)

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        sql = str(statement)
        if entity is not None:
            if entity.__name__ == "ExchangeConnection":
                return self.connection
            if entity.__name__ == "CapitalCampaignDefinition":
                return self.definition
            if entity.__name__ == "CapitalCampaign":
                return self.runtime
            if entity.__name__ == "AuditLog":
                return self.audits[-1] if self.audits else None
        if "FROM canonical_preview_packages" in sql:
            return self.active_package_count
        if "FROM canonical_proving_activations" in sql and "no_leverage IS false" in sql:
            return self.non_compliant_activation_count
        if "FROM canonical_proving_activations" in sql and "activation_state" in sql:
            return self.active_activation_count
        if "FROM live_crypto_orders" in sql:
            return self.open_live_order_count
        if "FROM live_reconciliation_events" in sql:
            return self.unresolved_reconciliation_count
        return None

    def add(self, obj) -> None:
        self.added.append(obj)
        if obj.__class__.__name__ == "AuditLog":
            self.audits.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            await self._db.rollback()
        return False


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _ExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarResult(self._values)


class _GatherDb:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.now = now
        self.campaign_id = uuid4()
        self.paper_account_id = uuid4()
        self.profile_id = uuid4()
        self.saw_btc_symbol_filter = False

    async def scalar(self, statement):
        sql = str(statement)
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

        if "FROM exchange_connections" in sql:
            return SimpleNamespace(status="connected", last_readiness_verdict="READY_FOR_OPERATOR_REVIEW", last_successful_sync_at=self.now, total_equity_usd=Decimal("62.10"))
        if "FROM capital_campaigns" in sql and "capital_campaign_definitions" not in sql:
            return SimpleNamespace(id=2, uuid=self.campaign_id, definition_version=1, paper_account_id=self.paper_account_id, realized_profit=Decimal("0"), fees=Decimal("0"), starting_capital=Decimal("100"), current_equity=Decimal("100"))
        if "FROM capital_campaign_definitions" in sql:
            return SimpleNamespace(maximum_open_positions=1, minimum_position_size=Decimal("5"), maximum_position_size=Decimal("5"), maximum_total_exposure=Decimal("5"))
        if "FROM assets" in sql:
            self.saw_btc_symbol_filter = "'BTC'" in compiled
            return SimpleNamespace(id=uuid4())
        if "FROM candles" in sql and "SELECT candles.created_at" not in sql:
            return SimpleNamespace(close_time=self.now - timedelta(minutes=18), interval="15m")
        if "SELECT candles.created_at" in sql:
            return self.now - timedelta(minutes=5)
        if "FROM autonomous_cycle_runs" in sql:
            return SimpleNamespace(
                cycle_id=uuid4(),
                state="COMPLETE",
                termination_stage="hold_no_package_created",
                proposed_action="HOLD",
                failure_reason=None,
                cycle_context={
                    "authoritative_composition": {
                        "selected_decision": {
                            "decision_record_id": str(uuid4()),
                            "strategy_identity": "ma_crossover@1.0.0",
                            "strategy_version": "ma_crossover@1.0.0",
                        }
                    }
                },
            )
        if "FROM canonical_preview_packages" in sql:
            return None
        if "FROM canonical_proving_activations" in sql:
            return None
        if "count(*)" in sql:
            return 0
        return None

    async def get(self, model, key):
        name = getattr(model, "__name__", "")
        if name == "PaperAccount":
            return SimpleNamespace(id=key, is_active=True, current_cash_balance=Decimal("23.7205"))
        if name == "LiveTradingProfile":
            return SimpleNamespace(id=key, paper_account_id=self.paper_account_id)
        return None

    async def execute(self, _statement):
        return _ExecuteResult([])


class _GatherSessionContext:
    def __init__(self, db: _GatherDb) -> None:
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        return False


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_refresh(**kwargs):
        assert kwargs["exchange_connection_id"] == db.connection.exchange_connection_id
        return SimpleNamespace(
            exchange_connection_id=db.connection.exchange_connection_id,
            provider="kraken_spot",
            environment="production",
            status="connected",
            readiness=SimpleNamespace(verdict="READY_FOR_OPERATOR_REVIEW", checked_at=datetime.now(timezone.utc)),
            total_equity_usd=Decimal("62.10"),
            last_successful_sync_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(service, "_refresh_exchange_balances", _fake_refresh)

    payload = await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")
    assert payload["provider"] == "kraken_spot"
    assert payload["invariants"]["no_order_submission"] is True


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_stale_to_fresh_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    refreshed_at = datetime.now(timezone.utc)

    async def _fake_refresh(**_kwargs):
        return SimpleNamespace(
            exchange_connection_id=db.connection.exchange_connection_id,
            provider="kraken_spot",
            environment="production",
            status="connected",
            readiness=SimpleNamespace(verdict="READY_FOR_OPERATOR_REVIEW", checked_at=refreshed_at),
            total_equity_usd=Decimal("63.50"),
            last_successful_sync_at=refreshed_at,
        )

    monkeypatch.setattr(service, "_refresh_exchange_balances", _fake_refresh)

    payload = await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")
    assert payload["readiness_verdict"] == "READY_FOR_OPERATOR_REVIEW"
    assert payload["last_successful_sync_at"] == refreshed_at.isoformat()


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_provider_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _boom(**_kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(service, "_refresh_exchange_balances", _boom)

    with pytest.raises(RuntimeError, match="provider failure"):
        await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")

    assert db.rollbacks == 1


def test_refresh_provider_balance_evidence_contains_no_order_path_calls() -> None:
    source = service.refresh_provider_balance_evidence.__code__.co_names
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source
    assert "execute_internal_crypto_fill" not in source


@pytest.mark.asyncio
async def test_proving_cap_preview_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_preview(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
    )

    assert payload["ready"] is True
    assert payload["before"]["maximum_open_positions"] == 2
    assert payload["proposed"]["maximum_open_positions"] == 1
    assert payload["proposed"]["maximum_position_size"] == "5"
    assert payload["proposed"]["maximum_total_exposure"] == "5"
    assert db.commits == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_proving_cap_execute_requires_confirm_and_idempotency_key() -> None:
    with pytest.raises(PermissionError, match="confirm=true"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=uuid4(),
            campaign_version=1,
            actor="operator:human",
            confirm=False,
            idempotency_key="key-1",
        )
    with pytest.raises(PermissionError, match="idempotency_key"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=uuid4(),
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_applies_only_cap_fields_and_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_execute(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
        actor="operator:human",
        confirm=True,
        idempotency_key="cap-1",
    )

    assert payload["changed"] is True
    assert payload["before"]["maximum_open_positions"] == 2
    assert payload["after"]["maximum_open_positions"] == 1
    assert db.definition.maximum_position_size == Decimal("5")
    assert db.definition.maximum_total_exposure == Decimal("5")
    assert db.definition.minimum_position_size == Decimal("5")
    assert db.definition.maximum_open_positions == 1
    assert any(getattr(item, "action", "") == "capital_campaign.proving_cap_transition" for item in db.added)


@pytest.mark.asyncio
async def test_proving_cap_execute_exact_retry_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    existing = SimpleNamespace(after_state={"idempotency_key": "cap-1", "maximum_position_size": "5"})
    db.audits.append(existing)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_execute(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
        actor="operator:human",
        confirm=True,
        idempotency_key="cap-1",
    )

    assert payload["idempotent"] is True
    assert payload["changed"] is False


@pytest.mark.asyncio
async def test_proving_cap_execute_conflicting_retry_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    existing = SimpleNamespace(after_state={"idempotency_key": "cap-1", "maximum_position_size": "5"})
    db.audits.append(existing)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(PermissionError, match="conflicting retry blocked"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-2",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_blockers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.active_package_count = 1
    db.active_activation_count = 1
    db.open_live_order_count = 1
    db.unresolved_reconciliation_count = 1
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(PermissionError, match="proving cap transition prerequisites failed"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-blocked",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_rollback_restores_old_values_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    def _boom_add(obj):
        if getattr(obj, "action", "") == "capital_campaign.proving_cap_transition":
            raise RuntimeError("audit write failed")
        db.added.append(obj)

    monkeypatch.setattr(db, "add", _boom_add)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-rollback",
        )

    assert db.definition.maximum_position_size == Decimal("10")
    assert db.definition.maximum_total_exposure == Decimal("12")


@pytest.mark.asyncio
async def test_proving_cap_execute_concurrent_attempts_single_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _call():
        return await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-race",
        )

    first, second = await asyncio.gather(_call(), _call())

    changed_count = sum(1 for item in [first, second] if item["changed"])
    idempotent_count = sum(1 for item in [first, second] if item["idempotent"])
    assert changed_count == 1
    assert idempotent_count == 1


def test_proving_cap_execute_contains_no_order_path_calls() -> None:
    source = service.canonical_proving_cap_transition_execute.__code__.co_names
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source
    assert "execute_internal_crypto_fill" not in source


def _build_profit_evidence(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "now": now,
        "campaign_id": uuid4(),
        "campaign_version": 1,
        "paper_account_id": uuid4(),
        "connection": SimpleNamespace(status="connected", last_readiness_verdict="READY_FOR_OPERATOR_REVIEW", last_successful_sync_at=now),
        "runtime": SimpleNamespace(uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), realized_profit=Decimal("0"), fees=Decimal("0")),
        "definition": SimpleNamespace(maximum_open_positions=1, minimum_position_size=Decimal("5"), maximum_position_size=Decimal("5"), maximum_total_exposure=Decimal("5")),
        "paper_account": SimpleNamespace(is_active=True, current_cash_balance=Decimal("23.7205")),
        "profile": SimpleNamespace(paper_account_id=uuid4()),
        "latest_candle": SimpleNamespace(close_time=now - timedelta(minutes=18), interval="15m"),
        "latest_ingestion_candle_at": now - timedelta(minutes=5),
        "latest_cycle": SimpleNamespace(
            cycle_id=uuid4(),
            state="COMPLETE",
            termination_stage="hold_no_package_created",
            proposed_action="HOLD",
            failure_reason=None,
            cycle_context={"authoritative_composition": {"selected_decision": {"decision_record_id": str(uuid4()), "strategy_identity": "ma_crossover@1.0.0", "strategy_version": "ma_crossover@1.0.0", "reason": "strategy_hold_signal"}}},
        ),
        "ready_package": None,
        "approval_event": None,
        "activation": None,
        "unresolved_reconciliation_count": 0,
        "unknown_reconciliation_count": 0,
        "open_live_order_count": 0,
        "buy_submitted": False,
        "buy_fill_reconciled": False,
        "sell_submitted": False,
        "sell_fill_reconciled": False,
        "autonomous_buy_provenance": False,
        "autonomous_sell_provenance": False,
        "position_open": False,
        "dry_run_passed": False,
        "provider_equity": "62.10",
        "paper_liquid_cash": Decimal("23.7205"),
        "provider_readiness_verdict": "READY_FOR_OPERATOR_REVIEW",
        "provider_balance_synced_at": now,
        "starting_reconciled_usd": Decimal("100.00"),
        "ending_reconciled_usd": Decimal("100.00"),
        "realized_gross_profit": Decimal("0"),
        "fees": Decimal("0"),
        "realized_net_profit": Decimal("0"),
    }
    base["runtime"].uuid = base["campaign_id"]
    base["runtime"].paper_account_id = base["paper_account_id"]
    base["profile"].paper_account_id = base["paper_account_id"]
    base.update(overrides)
    return base


def test_first_profit_status_hold_reports_waiting_for_executable_signal() -> None:
    payload = service._derive_first_autonomous_profit_status(_build_profit_evidence())
    assert payload["status"] == "WAITING_FOR_EXECUTABLE_SIGNAL"
    assert payload["completion_percent"] == 99.6


def test_first_profit_status_hold_is_not_blocked() -> None:
    payload = service._derive_first_autonomous_profit_status(_build_profit_evidence())
    assert payload["status"] != "BLOCKED"


def test_first_profit_status_stale_market_data() -> None:
    evidence = _build_profit_evidence(latest_candle=SimpleNamespace(close_time=datetime.now(timezone.utc) - timedelta(minutes=21), interval="15m"))
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["status"] == "WAITING_FOR_FRESH_MARKET_DATA"
    assert payload["completion_percent"] < 99.6


def test_first_profit_status_ready_package_available() -> None:
    evidence = _build_profit_evidence(ready_package=SimpleNamespace(package_id=uuid4()))
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["status"] == "READY_PACKAGE_AVAILABLE"
    assert payload["completion_percent"] == 99.7


def test_first_profit_status_authorized_package_anchor() -> None:
    evidence = _build_profit_evidence(
        ready_package=SimpleNamespace(package_id=uuid4()),
        approval_event=SimpleNamespace(id=uuid4()),
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] == 99.75


def test_first_profit_status_activation_anchor() -> None:
    evidence = _build_profit_evidence(
        approval_event=SimpleNamespace(id=uuid4()),
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] == 99.85


def test_first_profit_status_reconciled_buy_anchor() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=True,
        position_open=True,
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] == 99.9


def test_first_profit_status_closed_position_preserves_stage_8_credit() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=True,
        sell_submitted=True,
        autonomous_sell_provenance=True,
        position_open=False,
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    checkpoint = next(item for item in payload["checkpoints"] if item["name"] == "open_live_btc_position_exists")
    assert checkpoint["state"] == "COMPLETED_HISTORICALLY"


def test_first_profit_status_missing_safety_evidence_is_blocked() -> None:
    evidence = _build_profit_evidence(connection=None)
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["status"] == "BLOCKED"


def test_first_profit_status_open_position_reports_position_open() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        position_open=True,
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=True,
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["status"] == "POSITION_OPEN"


def test_first_profit_status_negative_net_not_100_percent() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=True,
        sell_submitted=True,
        sell_fill_reconciled=True,
        autonomous_sell_provenance=True,
        starting_reconciled_usd=Decimal("100.00"),
        ending_reconciled_usd=Decimal("99.90"),
        fees=Decimal("1"),
        realized_net_profit=Decimal("-0.10"),
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] == 99.97
    assert payload["status"] == "VERIFYING_NET_PROFIT"


def test_first_profit_status_positive_net_is_100_percent() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=True,
        sell_submitted=True,
        sell_fill_reconciled=True,
        autonomous_sell_provenance=True,
        starting_reconciled_usd=Decimal("100.00"),
        ending_reconciled_usd=Decimal("101.00"),
        fees=Decimal("0.10"),
        realized_net_profit=Decimal("0.25"),
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] == 100
    assert payload["status"] == "FIRST_AUTONOMOUS_NET_PROFIT_COMPLETE"


def test_first_profit_status_zero_or_negative_net_never_100() -> None:
    for realized in (Decimal("0"), Decimal("-0.01")):
        evidence = _build_profit_evidence(
            activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
            buy_submitted=True,
            buy_fill_reconciled=True,
            autonomous_buy_provenance=True,
            sell_submitted=True,
            sell_fill_reconciled=True,
            autonomous_sell_provenance=True,
            starting_reconciled_usd=Decimal("100.00"),
            ending_reconciled_usd=Decimal("100.00"),
            fees=Decimal("0.10"),
            realized_net_profit=realized,
        )
        payload = service._derive_first_autonomous_profit_status(evidence)
        assert payload["completion_percent"] != 100


def test_first_profit_status_missing_autonomous_provenance_never_100() -> None:
    evidence = _build_profit_evidence(
        activation=SimpleNamespace(activation_id=uuid4(), dry_run_live_crypto_order_id=uuid4()),
        buy_submitted=True,
        buy_fill_reconciled=True,
        autonomous_buy_provenance=False,
        sell_submitted=True,
        sell_fill_reconciled=True,
        autonomous_sell_provenance=False,
        starting_reconciled_usd=Decimal("100.00"),
        ending_reconciled_usd=Decimal("101.00"),
        fees=Decimal("0.10"),
        realized_net_profit=Decimal("0.50"),
    )
    payload = service._derive_first_autonomous_profit_status(evidence)
    assert payload["completion_percent"] != 100


def test_first_profit_status_contains_read_only_invariants() -> None:
    source = service.first_autonomous_profit_status.__code__.co_names
    assert "commit" not in source
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source


@pytest.mark.asyncio
async def test_first_profit_status_real_gather_path_resolves_btc_symbol_without_nameerror(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _GatherDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _GatherSessionContext(db))

    payload = await service.first_autonomous_profit_status(
        campaign_id=db.campaign_id,
        campaign_version=1,
        runtime_campaign_id=2,
        paper_account_id=db.paper_account_id,
        live_trading_profile_id=db.profile_id,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
    )

    assert db.saw_btc_symbol_filter is True
    assert payload["completion_percent"] == 99.6
    assert payload["status"] == "WAITING_FOR_EXECUTABLE_SIGNAL"
    assert payload["invariants"]["read_only"] is True
    assert payload["invariants"]["no_provider_order_submission"] is True
