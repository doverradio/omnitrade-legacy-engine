from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.services.paper.alpaca_paper import AlpacaPaperOrderResult
from app.services.signals.execution_orchestrator import (
    SignalExecutionRequest,
    orchestrate_paper_signal_execution,
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


class _FakeSession:
    def __init__(self, *, accounts: list[PaperAccount], assets: list[Asset], trades: list[Trade]) -> None:
        self.accounts = accounts
        self.assets = assets
        self.trades = trades
        self.audit_logs: list[AuditLog] = []

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

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_orchestrator_prevents_duplicate_signal_execution() -> None:
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


@pytest.mark.asyncio
async def test_orchestrator_rejects_stock_to_internal_sim_path() -> None:
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

    monkeypatch.setattr(orchestrator_module, "AsyncHTTPClient", lambda: _NoopHttpClient())
    monkeypatch.setattr(orchestrator_module, "submit_alpaca_paper_order", fake_submit_alpaca_paper_order)

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
