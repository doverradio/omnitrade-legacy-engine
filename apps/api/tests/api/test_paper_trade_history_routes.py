from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _ScalarRows(self._rows)


class _TradeHistoryFakeSession:
    def __init__(self) -> None:
        self.account_a = uuid.uuid4()
        self.account_b = uuid.uuid4()
        self.asset_btc = uuid.uuid4()
        self.asset_eth = uuid.uuid4()
        self.signal_a = uuid.uuid4()
        self.signal_b = uuid.uuid4()
        self.strategy_a = uuid.uuid4()
        self.decision_a = uuid.uuid4()

        self.accounts = {
            self.account_a: SimpleNamespace(id=self.account_a, is_active=True, created_at=datetime(2026, 7, 9, tzinfo=timezone.utc)),
            self.account_b: SimpleNamespace(id=self.account_b, is_active=True, created_at=datetime(2026, 7, 9, 1, tzinfo=timezone.utc)),
        }

        self.trades = [
            SimpleNamespace(
                id=uuid.uuid4(),
                paper_account_id=self.account_a,
                signal_id=self.signal_a,
                asset_id=self.asset_btc,
                side="buy",
                quantity=Decimal("1"),
                price=Decimal("100"),
                fee=Decimal("1"),
                is_paper=True,
                executed_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
                created_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                paper_account_id=self.account_a,
                signal_id=self.signal_a,
                asset_id=self.asset_btc,
                side="sell",
                quantity=Decimal("1"),
                price=Decimal("110"),
                fee=Decimal("1"),
                is_paper=True,
                executed_at=datetime(2026, 7, 9, 10, 5, tzinfo=timezone.utc),
                created_at=datetime(2026, 7, 9, 10, 5, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                paper_account_id=self.account_b,
                signal_id=self.signal_b,
                asset_id=self.asset_eth,
                side="buy",
                quantity=Decimal("2"),
                price=Decimal("50"),
                fee=Decimal("0"),
                is_paper=True,
                executed_at=datetime(2026, 7, 9, 10, 10, tzinfo=timezone.utc),
                created_at=datetime(2026, 7, 9, 10, 10, tzinfo=timezone.utc),
            ),
        ]

        self.symbols = {
            self.asset_btc: "BTCUSD",
            self.asset_eth: "ETHUSD",
        }

    def _filtered_trades(self, statement) -> list:
        params = statement.compile().params
        account_filter = None
        for value in params.values():
            if isinstance(value, uuid.UUID) and value in self.accounts:
                account_filter = value
                break

        rows = [trade for trade in self.trades if trade.is_paper]
        if account_filter is not None:
            rows = [trade for trade in rows if trade.paper_account_id == account_filter]
        return rows

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params

        if "FROM paper_accounts" in sql:
            for value in params.values():
                if isinstance(value, uuid.UUID) and value in self.accounts:
                    return self.accounts[value]
            return sorted(self.accounts.values(), key=lambda item: item.created_at, reverse=True)[0]

        if "count" in sql and "FROM trades" in sql:
            return len(self._filtered_trades(statement))

        return None

    async def execute(self, statement):
        sql = str(statement)
        params = statement.compile().params

        if "FROM trades LEFT OUTER JOIN assets" in sql:
            rows = self._filtered_trades(statement)
            rows = sorted(rows, key=lambda item: (item.executed_at, item.id), reverse=True)
            limit = int(getattr(getattr(statement, "_limit_clause", None), "value", len(rows)))
            offset = int(getattr(getattr(statement, "_offset_clause", None), "value", 0))
            visible = rows[offset : offset + limit]
            return _Rows([(trade, self.symbols.get(trade.asset_id)) for trade in visible])

        if "FROM trades" in sql and "ORDER BY trades.executed_at ASC" in sql:
            rows = self._filtered_trades(statement)
            rows = sorted(rows, key=lambda item: (item.executed_at, item.id))
            return _Rows(rows)

        if "FROM signals" in sql and "strategy_id" in sql:
            return _Rows([(self.signal_a, self.strategy_a)])

        if "FROM decision_records" in sql:
            return _Rows(
                [
                    (
                        self.decision_a,
                        datetime(2026, 7, 9, 10, 20, tzinfo=timezone.utc),
                        {"signals": [str(self.signal_a)]},
                    )
                ]
            )

        return _Rows([])


def create_test_client(fake_session: _TradeHistoryFakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _TradeHistoryFakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_trade_history_empty_state() -> None:
    session = _TradeHistoryFakeSession()
    session.trades = []

    with create_test_client(session) as client:
        response = client.get("/paper/trade-history?limit=20&offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["has_more"] is False


def test_trade_history_populated_state_newest_first() -> None:
    session = _TradeHistoryFakeSession()

    with create_test_client(session) as client:
        response = client.get("/paper/trade-history?limit=20&offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["items"][0]["asset"] == "ETHUSD"
    assert payload["items"][0]["side"] == "buy"
    assert payload["items"][1]["side"] == "sell"
    assert payload["items"][1]["realized_pnl"] == "9"
    assert payload["items"][1]["strategy_id"] == str(session.strategy_a)
    assert payload["items"][1]["decision_record_id"] == str(session.decision_a)


def test_trade_history_pagination() -> None:
    session = _TradeHistoryFakeSession()

    with create_test_client(session) as client:
        response = client.get("/paper/trade-history?limit=1&offset=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["total"] == 3
    assert len(payload["items"]) == 1
    assert payload["items"][0]["side"] == "sell"
    assert payload["has_more"] is True


def test_trade_history_account_filter() -> None:
    session = _TradeHistoryFakeSession()

    with create_test_client(session) as client:
        response = client.get(f"/paper/trade-history?account_id={session.account_a}&limit=20&offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert all(item["paper_account_id"] == str(session.account_a) for item in payload["items"])
