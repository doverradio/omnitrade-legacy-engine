from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.trade import Trade


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

    def all(self) -> list[Any]:
        return self._items


class _FakeSession:
    def __init__(self, *, accounts: list[PaperAccount], trades: list[Trade], assets: list[Asset], candles: list[Candle]) -> None:
        self.accounts = accounts
        self.trades = trades
        self.assets = assets
        self.candles = candles
        self.kill_switches: list[RiskKillSwitch] = []
        self.audit_logs: list[AuditLog] = []

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM paper_accounts" in sql and "ORDER BY" in sql:
            active_accounts = [account for account in self.accounts if account.is_active]
            if not active_accounts:
                return None
            return sorted(active_accounts, key=lambda item: item.created_at, reverse=True)[0]

        if "FROM paper_accounts" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((account for account in self.accounts if account.id == account_id), None)

        if "SELECT assets.symbol" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            asset = next((item for item in self.assets if item.id == asset_id), None)
            return asset.symbol if asset else None

        if "SELECT candles.close" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            candle_rows = [candle for candle in self.candles if candle.asset_id == asset_id]
            candle_rows.sort(key=lambda item: item.open_time, reverse=True)
            return candle_rows[0].close if candle_rows else None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM trades LEFT OUTER JOIN assets" in sql and "SELECT" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            rows = [trade for trade in self.trades if trade.paper_account_id == account_id and trade.is_paper]
            rows.sort(key=lambda item: item.executed_at, reverse=True)
            results = []
            for trade in rows:
                asset = next((item for item in self.assets if item.id == trade.asset_id), None)
                results.append((trade, asset.symbol if asset else None))
            return _ExecuteResult(results)

        if "FROM trades" in sql and "SELECT" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            rows = [trade for trade in self.trades if trade.paper_account_id == account_id]
            rows.sort(key=lambda item: item.executed_at)
            return _ExecuteResult(rows)

        if "DELETE FROM trades" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            self.trades = [trade for trade in self.trades if trade.paper_account_id != account_id]
            return _ExecuteResult([])

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, PaperAccount):
            if not obj.id:
                obj.id = uuid.uuid4()
            self.accounts.append(obj)
            return

        if isinstance(obj, RiskKillSwitch):
            self.kill_switches.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def refresh(self, obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_get_paper_account_returns_rollups() -> None:
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    account = PaperAccount(
        id=account_id,
        owner_user_id=uuid.uuid4(),
        name="Family Paper",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    trades = [
        Trade(
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.10"),
            price=Decimal("100"),
            fee=Decimal("0.10"),
            is_paper=True,
            execution_venue="internal_sim",
            executed_at=now,
        )
    ]
    assets = [
        Asset(
            id=asset_id,
            symbol="BTCUSDT",
            asset_class="crypto",
            exchange="binance_us",
            is_active=True,
            supports_fractional=True,
        )
    ]
    candles = [
        Candle(
            asset_id=asset_id,
            interval="1m",
            open_time=now,
            close_time=now,
            open=Decimal("101"),
            high=Decimal("101"),
            low=Decimal("101"),
            close=Decimal("101"),
            volume=Decimal("1"),
            source="binance_us",
        )
    ]

    with create_test_client(_FakeSession(accounts=[account], trades=trades, assets=assets, candles=candles)) as client:
        response = client.get("/paper/account")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(account_id)
    assert payload["current_cash_balance"] == "14.90"
    assert payload["equity"] == "25.00"
    assert payload["equity_return_usd"] == "0.00"
    assert payload["equity_return_pct"] == "0.00"
    assert payload["positions"][0]["symbol"] == "BTCUSDT"
    assert payload["positions"][0]["quantity"] == "0.10"


def test_create_paper_account_bootstraps_account_kill_switch() -> None:
    session = _FakeSession(accounts=[], trades=[], assets=[], candles=[])

    with create_test_client(session) as client:
        response = client.post(
            "/paper/account",
            json={
                "name": "Family Paper",
                "asset_class": "crypto",
                "starting_balance": "25",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    account_id = payload["id"]

    account_switches = [
        switch
        for switch in session.kill_switches
        if switch.scope == "account" and str(switch.paper_account_id) == account_id
    ]
    assert len(account_switches) == 1
    switch = account_switches[0]
    assert switch.engaged is False
    assert switch.rearm_required is False
    assert switch.changed_by == "system_bootstrap"
    assert switch.reason == "account_bootstrap_default"


def test_create_paper_account_rejects_starting_balance_below_25() -> None:
    session = _FakeSession(accounts=[], trades=[], assets=[], candles=[])

    with create_test_client(session) as client:
        response = client.post(
            "/paper/account",
            json={
                "name": "Too Small",
                "asset_class": "crypto",
                "starting_balance": "23.81",
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["message"] == "starting_balance must be at least 25"


def test_create_paper_account_rejects_starting_balance_of_5_and_0() -> None:
    session = _FakeSession(accounts=[], trades=[], assets=[], candles=[])

    with create_test_client(session) as client:
        five_response = client.post(
            "/paper/account",
            json={
                "name": "Five Dollars",
                "asset_class": "crypto",
                "starting_balance": "5",
            },
        )
        zero_response = client.post(
            "/paper/account",
            json={
                "name": "Zero Dollars",
                "asset_class": "crypto",
                "starting_balance": "0",
            },
        )

    assert five_response.status_code == 400
    assert zero_response.status_code == 400


def test_get_paper_account_not_found() -> None:
    with create_test_client(_FakeSession(accounts=[], trades=[], assets=[], candles=[])) as client:
        response = client.get("/paper/account")

    assert response.status_code == 404


def test_reset_paper_account_clears_positions() -> None:
    account_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=account_id,
        owner_user_id=uuid.uuid4(),
        name="Family Paper",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("14.9"),
        is_active=True,
        created_at=now,
    )
    trade = Trade(
        paper_account_id=account_id,
        asset_id=uuid.uuid4(),
        side="buy",
        quantity=Decimal("0.10"),
        price=Decimal("100"),
        fee=Decimal("0.10"),
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=now,
    )
    session = _FakeSession(accounts=[account], trades=[trade], assets=[], candles=[])

    with create_test_client(session) as client:
        response = client.post(
            "/paper/reset",
            json={"account_id": str(account_id), "confirm": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == str(account_id)
    assert payload["current_cash_balance"] == "25"
    assert payload["positions"] == []
    assert session.trades == []


def test_get_paper_trades_returns_paper_only_trade_history() -> None:
    account_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    asset_id = uuid.uuid4()

    account = PaperAccount(
        id=account_id,
        owner_user_id=uuid.uuid4(),
        name="Family Paper",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    paper_trade = Trade(
        id=uuid.uuid4(),
        paper_account_id=account_id,
        asset_id=asset_id,
        side="buy",
        quantity=Decimal("0.02"),
        price=Decimal("65010.00"),
        fee=Decimal("0.65"),
        is_paper=True,
        execution_venue="internal_sim",
        executed_at=now,
    )
    non_paper_trade = Trade(
        id=uuid.uuid4(),
        paper_account_id=account_id,
        asset_id=asset_id,
        side="sell",
        quantity=Decimal("0.01"),
        price=Decimal("65100.00"),
        fee=Decimal("0.60"),
        is_paper=False,
        execution_venue="internal_sim",
        executed_at=now,
    )
    asset = Asset(
        id=asset_id,
        symbol="BTCUSDT",
        asset_class="crypto",
        exchange="binance_us",
        is_active=True,
        supports_fractional=True,
    )

    with create_test_client(
        _FakeSession(accounts=[account], trades=[paper_trade, non_paper_trade], assets=[asset], candles=[])
    ) as client:
        response = client.get(f"/paper/trades?account_id={account_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == str(paper_trade.id)
    assert payload["items"][0]["fee"] == "0.65"
    assert payload["items"][0]["symbol"] == "BTCUSDT"


def test_get_paper_trades_rejects_invalid_time_range() -> None:
    account_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=account_id,
        owner_user_id=uuid.uuid4(),
        name="Family Paper",
        asset_class="crypto",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )

    with create_test_client(_FakeSession(accounts=[account], trades=[], assets=[], candles=[])) as client:
        response = client.get(
            f"/paper/trades?account_id={account_id}&start_time=2026-07-07T00:00:00%2B00:00&end_time=2026-07-06T00:00:00%2B00:00"
        )

    assert response.status_code == 400
