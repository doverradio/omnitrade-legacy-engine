from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


class _EquityCurveFakeSession:
    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.account_id = uuid.uuid4()
        self.asset_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        self.account = SimpleNamespace(
            id=self.account_id,
            starting_balance=Decimal("1000"),
            is_active=True,
            created_at=now,
        )

        self.trades = []
        if not empty:
            self.trades = [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    paper_account_id=self.account_id,
                    signal_id=uuid.uuid4(),
                    asset_id=self.asset_id,
                    side="buy",
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    fee=Decimal("1"),
                    is_paper=True,
                    executed_at=now - timedelta(minutes=20),
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    paper_account_id=self.account_id,
                    signal_id=uuid.uuid4(),
                    asset_id=self.asset_id,
                    side="sell",
                    quantity=Decimal("1"),
                    price=Decimal("110"),
                    fee=Decimal("1"),
                    is_paper=True,
                    executed_at=now - timedelta(minutes=5),
                ),
            ]

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM paper_accounts" in sql:
            return self.account
        return None

    async def execute(self, statement):
        sql = str(statement)

        if "FROM trades" in sql and "ORDER BY trades.executed_at ASC" in sql:
            return _Rows(sorted(self.trades, key=lambda item: (item.executed_at, item.id)))

        return _Rows([])


def create_test_client(fake_session: _EquityCurveFakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _EquityCurveFakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_equity_curve_empty_account_returns_flat_line() -> None:
    session = _EquityCurveFakeSession(empty=True)

    with create_test_client(session) as client:
        response = client.get(f"/paper/equity-curve?account_id={session.account_id}&window_minutes=120&interval=15")

    assert response.status_code == 200
    payload = response.json()
    assert payload["starting_balance"] == "1000"
    assert payload["current_equity"] == "1000"
    assert payload["total_return_usd"] == "0"
    assert len(payload["points"]) == 2
    assert payload["points"][0]["equity"] == "1000"
    assert payload["points"][1]["equity"] == "1000"


def test_equity_curve_with_trades_returns_points() -> None:
    session = _EquityCurveFakeSession(empty=False)

    with create_test_client(session) as client:
        response = client.get(f"/paper/equity-curve?account_id={session.account_id}&window_minutes=120&interval=15")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["points"]) >= 2
    assert payload["current_equity"] == "1008"
    assert payload["total_return_usd"] == "8"
    assert payload["points"][-1]["trade_count_at_point"] == 2
