from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.trade import Trade
from app.services.paper.alpaca_paper import AlpacaPaperOrderResult
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceResult,
    RiskEvaluationResult,
    RiskEvaluationStep,
)
from app.services.signals.execution_orchestrator import (
    SignalExecutionRequest,
    orchestrate_paper_signal_execution,
)


def _trusted_execution_risk_context(*, now: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        account_equity=Decimal("25"),
        start_of_day_equity=Decimal("25"),
        current_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("25"),
        max_drawdown_pct=Decimal("0.10"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"),
        evaluation_time=now,
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source="system_default_config",
        runtime_cooldown_state="unavailable_not_persisted",
        runtime_no_trade_zone_state="unavailable_not_persisted",
        start_of_day_equity_source="rolled_from_prior_last_equity",
        high_water_mark_equity_source="updated_from_current_equity_observation",
    )


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        *,
        accounts: list[PaperAccount],
        assets: list[Asset],
        trades: list[Trade],
        candles: list[Candle] | None = None,
        include_account_kill_switches: bool = True,
    ) -> None:
        self.accounts = accounts
        self.assets = assets
        self.trades = trades
        self.candles = candles or []
        self.audit_logs: list[AuditLog] = []
        self.risk_events: list[RiskEvent] = []
        self.kill_switches: list[RiskKillSwitch] = [
            RiskKillSwitch(scope="global", paper_account_id=None, engaged=False, rearm_required=False),
        ]
        if include_account_kill_switches:
            self.kill_switches.extend(
                [
                    RiskKillSwitch(scope="account", paper_account_id=account.id, engaged=False, rearm_required=False)
                    for account in accounts
                ]
            )
        self._in_transaction = False

    def begin(self) -> _BeginContext:
        return _BeginContext()

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM trades" in sql:
            uuid_values = [value for value in values if isinstance(value, uuid.UUID)]
            paper_account_id = uuid_values[0] if len(uuid_values) > 0 else None
            signal_id = uuid_values[1] if len(uuid_values) > 1 else None
            matches = [
                trade
                for trade in self.trades
                if trade.paper_account_id == paper_account_id and trade.signal_id == signal_id and trade.is_paper
            ]
            matches.sort(key=lambda item: item.executed_at, reverse=True)
            return matches[0] if matches else None

        if "FROM paper_accounts" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((item for item in self.accounts if item.id == account_id), None)

        if "FROM assets" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((item for item in self.assets if item.id == asset_id), None)

        if "FROM risk_kill_switches" in sql:
            scope = next((value for value in values if isinstance(value, str) and value in {"global", "account"}), None)
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            for switch in self.kill_switches:
                if switch.scope != scope:
                    continue
                if switch.paper_account_id != account_id:
                    continue
                return switch
            return None

        if "SELECT candles.close" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            rows = [candle for candle in self.candles if candle.asset_id == asset_id]
            rows.sort(key=lambda item: item.open_time, reverse=True)
            return rows[0].close if rows else None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM trades" in sql and "SELECT" in sql:
            paper_account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            asset_id = values[-1] if values else None
            rows = [
                trade
                for trade in self.trades
                if trade.paper_account_id == paper_account_id and trade.asset_id == asset_id
            ]
            rows.sort(key=lambda item: item.executed_at)
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, Trade):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.trades.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)
            return

        if isinstance(obj, RiskEvent):
            self.risk_events.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_orchestrator_prevents_duplicate_signal_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    signal_id = uuid.uuid4()
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("20"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    existing_trade = Trade(
        paper_account_id=account.id,
        signal_id=signal_id,
        asset_id=asset.id,
        side="buy",
        quantity=Decimal("0.01"),
        price=Decimal("100"),
        fee=Decimal("0.01"),
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=now,
    )
    session = _FakeSession(accounts=[account], assets=[asset], trades=[existing_trade])

    import app.services.signals.execution_orchestrator as orchestrator_module

    evaluate_calls = {"count": 0}
    persist_calls = {"count": 0}
    adapter_calls = {"count": 0}

    def fake_evaluate_signal_risk(*args, **kwargs):
        evaluate_calls["count"] += 1
        raise AssertionError("Risk engine must not run for duplicate requests")

    async def fake_persist_risk_decision(*args, **kwargs):
        persist_calls["count"] += 1
        raise AssertionError("Risk persistence must not run for duplicate requests")

    async def fake_execute_internal_crypto_fill(*args, **kwargs):
        adapter_calls["count"] += 1
        raise AssertionError("Execution adapter must not run for duplicate requests")

    monkeypatch.setattr(orchestrator_module, "evaluate_signal_risk", fake_evaluate_signal_risk)
    monkeypatch.setattr(orchestrator_module, "persist_risk_decision", fake_persist_risk_decision)
    monkeypatch.setattr(orchestrator_module, "execute_internal_crypto_fill", fake_execute_internal_crypto_fill)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=signal_id,
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.01"),
        ),
    )

    assert result.execution_status == "duplicate"
    assert result.trade_id == existing_trade.id
    assert evaluate_calls["count"] == 0
    assert persist_calls["count"] == 0
    assert adapter_calls["count"] == 0
    assert len(session.risk_events) == 0
    assert any(audit.action == "signal_execution_duplicate_skipped" for audit in session.audit_logs)


@pytest.mark.asyncio
async def test_orchestrator_rejects_stock_to_internal_sim_path(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    stock_asset = Asset(
        id=uuid.uuid4(),
        symbol="AAPL",
        asset_class="stock",
        exchange="alpaca",
        supports_fractional=True,
        is_active=True,
    )

    session = _FakeSession(accounts=[account], assets=[stock_asset], trades=[])

    import app.services.signals.execution_orchestrator as orchestrator_module

    async def fake_resolve_execution_risk_context(*_args, **_kwargs):
        return _trusted_execution_risk_context(now=now)

    monkeypatch.setattr(orchestrator_module, "resolve_execution_risk_context", fake_resolve_execution_risk_context)

    with pytest.raises(InvalidRequestError):
        await orchestrate_paper_signal_execution(
            db=session,
            request=SignalExecutionRequest(
                signal_id=uuid.uuid4(),
                paper_account_id=account.id,
                asset_id=stock_asset.id,
                side="buy",
                quantity=Decimal("0.5"),
            ),
        )

    assert any(audit.action == "signal_execution_failed" for audit in session.audit_logs)


@pytest.mark.asyncio
async def test_orchestrator_routes_stock_to_alpaca(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Stocks",
        asset_class="stock",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    stock_asset = Asset(
        id=uuid.uuid4(),
        symbol="AAPL",
        asset_class="stock",
        exchange="alpaca",
        supports_fractional=True,
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[stock_asset], trades=[])

    class _NoopHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_submit_alpaca_paper_order(*args, **kwargs):
        return AlpacaPaperOrderResult(
            broker_order_id="broker-order-1",
            status="filled",
            symbol="AAPL",
            side="buy",
            type="market",
            time_in_force="day",
            qty=Decimal("0.5"),
            filled_qty=Decimal("0.5"),
            filled_avg_price=Decimal("210.10"),
            submitted_at="2026-07-06T12:00:00Z",
            filled_at="2026-07-06T12:00:01Z",
        )

    import app.services.signals.execution_orchestrator as orchestrator_module

    async def fake_resolve_execution_risk_context(*_args, **_kwargs):
        return _trusted_execution_risk_context(now=now)

    monkeypatch.setattr(orchestrator_module, "AsyncHTTPClient", lambda: _NoopHttpClient())
    monkeypatch.setattr(orchestrator_module, "submit_alpaca_paper_order", fake_submit_alpaca_paper_order)
    monkeypatch.setattr(orchestrator_module, "resolve_execution_risk_context", fake_resolve_execution_risk_context)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=stock_asset.id,
            side="buy",
            quantity=Decimal("0.5"),
            client_order_id="coid-1",
        ),
    )

    assert result.execution_venue == "alpaca_paper"
    assert result.execution_status == "executed"
    assert result.is_paper is True
    assert result.broker_order_id == "broker-order-1"
    assert len(session.trades) == 1
    assert session.trades[0].signal_id is not None
    assert any(audit.action == "signal_execution_orchestrated" for audit in session.audit_logs)


@pytest.mark.asyncio
async def test_orchestrator_audits_failure_for_invalid_side() -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[asset], trades=[])

    with pytest.raises(InvalidRequestError):
        await orchestrate_paper_signal_execution(
            db=session,
            request=SignalExecutionRequest(
                signal_id=uuid.uuid4(),
                paper_account_id=account.id,
                asset_id=asset.id,
                side="hold",
                quantity=Decimal("0.1"),
            ),
        )

    assert any(audit.action == "signal_execution_failed" for audit in session.audit_logs)


@pytest.mark.asyncio
async def test_orchestrator_rejects_before_adapter_when_risk_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[asset], trades=[])

    import app.services.signals.execution_orchestrator as orchestrator_module

    evaluate_calls = {"count": 0}
    persist_calls = {"count": 0}

    def fake_evaluate_signal_risk(*args, **kwargs):
        evaluate_calls["count"] += 1
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="max_daily_loss_breached",
            approved_quantity=Decimal("0"),
            steps=[RiskEvaluationStep(step="daily_loss", status="reject", reason_code="max_daily_loss_breached")],
        )

    async def fake_persist_risk_decision(*args, **kwargs):
        persist_calls["count"] += 1
        return RiskDecisionPersistenceResult(
            risk_event_id=uuid.uuid4(),
            risk_event_action="blocked",
            risk_event_type="daily_loss_limit",
            risk_event_reason_code="max_daily_loss_breached",
            audit_written=False,
        )

    async def should_not_execute_adapter(*args, **kwargs):
        raise AssertionError("Execution adapter should not be called on risk rejection")

    monkeypatch.setattr(orchestrator_module, "evaluate_signal_risk", fake_evaluate_signal_risk)
    monkeypatch.setattr(orchestrator_module, "persist_risk_decision", fake_persist_risk_decision)
    monkeypatch.setattr(orchestrator_module, "execute_internal_crypto_fill", should_not_execute_adapter)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.02"),
        ),
    )

    assert result.execution_status == "rejected"
    assert result.execution_venue == "risk_engine"
    assert evaluate_calls["count"] == 1
    assert persist_calls["count"] == 1


@pytest.mark.asyncio
async def test_orchestrator_passes_resized_quantity_to_crypto_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[asset], trades=[])

    import app.services.signals.execution_orchestrator as orchestrator_module

    evaluate_calls = {"count": 0}
    persist_calls = {"count": 0}
    adapter_quantity = {"value": None}

    def fake_evaluate_signal_risk(*args, **kwargs):
        evaluate_calls["count"] += 1
        return RiskEvaluationResult(
            action=RiskDecisionAction.RESIZE,
            reason_code="position_resized_by_risk_engine",
            approved_quantity=Decimal("0.015"),
            steps=[RiskEvaluationStep(step="position_size", status="resize", reason_code="position_resized_by_risk_engine")],
        )

    async def fake_persist_risk_decision(*args, **kwargs):
        persist_calls["count"] += 1
        return RiskDecisionPersistenceResult(
            risk_event_id=uuid.uuid4(),
            risk_event_action="resized",
            risk_event_type="position_limit",
            risk_event_reason_code="position_resized_by_risk_engine",
            audit_written=False,
        )

    class _FillResult:
        def __init__(self, trade_id: uuid.UUID) -> None:
            self.trade_id = trade_id

    async def fake_execute_internal_crypto_fill(*args, **kwargs):
        adapter_quantity["value"] = kwargs["quantity"]
        return _FillResult(trade_id=uuid.uuid4())

    monkeypatch.setattr(orchestrator_module, "evaluate_signal_risk", fake_evaluate_signal_risk)
    monkeypatch.setattr(orchestrator_module, "persist_risk_decision", fake_persist_risk_decision)
    monkeypatch.setattr(orchestrator_module, "execute_internal_crypto_fill", fake_execute_internal_crypto_fill)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.02"),
        ),
    )

    assert result.execution_status == "executed"
    assert evaluate_calls["count"] == 1
    assert persist_calls["count"] == 1
    assert adapter_quantity["value"] == Decimal("0.015")


@pytest.mark.asyncio
async def test_orchestrator_persists_risk_inside_existing_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[asset], trades=[])
    session._in_transaction = True

    import app.services.signals.execution_orchestrator as orchestrator_module

    def fake_evaluate_signal_risk(*args, **kwargs):
        return RiskEvaluationResult(
            action=RiskDecisionAction.REJECT,
            reason_code="max_daily_loss_breached",
            approved_quantity=Decimal("0"),
            steps=[RiskEvaluationStep(step="daily_loss", status="reject", reason_code="max_daily_loss_breached")],
        )

    monkeypatch.setattr(orchestrator_module, "evaluate_signal_risk", fake_evaluate_signal_risk)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.02"),
        ),
    )

    assert result.execution_status == "rejected"
    assert len(session.risk_events) == 1


@pytest.mark.asyncio
async def test_orchestrator_rejects_with_unknown_account_kill_switch_when_row_missing() -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(
        accounts=[account],
        assets=[asset],
        trades=[],
        candles=[
            Candle(
                asset_id=asset.id,
                interval="1m",
                open_time=now,
                close_time=now,
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
                source="binance_us",
            )
        ],
        include_account_kill_switches=False,
    )

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.1"),
        ),
    )

    assert result.execution_status == "rejected"
    assert session.risk_events[-1].detail["reason_code"] == "account_kill_switch_state_unknown"


@pytest.mark.asyncio
async def test_orchestrator_with_bootstrapped_account_kill_switch_progresses_past_unknown_state() -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(
        accounts=[account],
        assets=[asset],
        trades=[],
        candles=[
            Candle(
                asset_id=asset.id,
                interval="1m",
                open_time=now,
                close_time=now,
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
                source="binance_us",
            )
        ],
    )

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="buy",
            quantity=Decimal("0.1"),
        ),
    )

    assert result.execution_status in {"executed", "pending", "rejected"}
    assert session.risk_events[-1].detail["reason_code"] != "account_kill_switch_state_unknown"


@pytest.mark.asyncio
async def test_orchestrator_rejects_sell_without_position_as_structured_execution_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Crypto",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        supports_fractional=True,
        qty_step_size=Decimal("0.00001"),
        min_order_notional=Decimal("1"),
        is_active=True,
    )
    session = _FakeSession(
        accounts=[account],
        assets=[asset],
        trades=[],
        candles=[
            Candle(
                asset_id=asset.id,
                interval="1m",
                open_time=now,
                close_time=now,
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
                source="binance_us",
            )
        ],
    )

    import app.services.signals.execution_orchestrator as orchestrator_module

    async def fake_resolve_execution_risk_context(*_args, **_kwargs):
        return _trusted_execution_risk_context(now=now)

    monkeypatch.setattr(orchestrator_module, "resolve_execution_risk_context", fake_resolve_execution_risk_context)

    result = await orchestrate_paper_signal_execution(
        db=session,
        request=SignalExecutionRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=account.id,
            asset_id=asset.id,
            side="sell",
            quantity=Decimal("0.1"),
        ),
    )

    assert result.execution_status == "rejected"
    assert result.outcome == "REJECTED"
    assert result.reason_code == "INSUFFICIENT_POSITION_QUANTITY"
    assert result.trade_id is None
    assert len(session.trades) == 0
    assert session.accounts[0].current_cash_balance == Decimal("25")
    assert any(audit.action == "signal_execution_rejected" for audit in session.audit_logs)
    assert any(event.detail.get("reason_code") == "INSUFFICIENT_POSITION_QUANTITY" for event in session.risk_events)
