from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.api.routes import paper as paper_route_module
from app.db.session import get_db
from app.main import create_app
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.services.paper.alpaca_paper import AlpacaPaperOrderResult


class _FakeSession:
    def __init__(self, *, accounts: list[PaperAccount], assets: list[Asset]) -> None:
        self.accounts = accounts
        self.assets = assets
        self.trades: list[Trade] = []
        self.audit_log_rows: list[AuditLog] = []

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        if "FROM paper_accounts" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((account for account in self.accounts if account.id == account_id), None)

        if "FROM assets" in sql:
            asset_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            return next((asset for asset in self.assets if asset.id == asset_id), None)

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, Trade):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.trades.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_log_rows.append(obj)

    async def commit(self) -> None:
        return None


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _sample_order_result() -> AlpacaPaperOrderResult:
    return AlpacaPaperOrderResult(
        broker_order_id="alpaca-order-1",
        status="filled",
        symbol="AAPL",
        side="buy",
        type="market",
        time_in_force="day",
        qty=Decimal("0.5"),
        filled_qty=Decimal("0.5"),
        filled_avg_price=Decimal("210.1"),
        submitted_at="2026-07-06T12:00:00Z",
        filled_at="2026-07-06T12:00:01Z",
    )


def test_submit_stock_paper_order_persists_paper_trade_and_audit(monkeypatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Stock Paper",
        asset_class="stock",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="AAPL",
        asset_class="stock",
        exchange="alpaca",
        supports_fractional=True,
        is_active=True,
    )
    session = _FakeSession(accounts=[account], assets=[asset])

    async def fake_submit_alpaca_paper_order(*args, **kwargs):
        return _sample_order_result()

    monkeypatch.setattr(paper_route_module, "submit_alpaca_paper_order", fake_submit_alpaca_paper_order)

    with create_test_client(session) as client:
        response = client.post(
            "/paper/orders/alpaca",
            json={
                "account_id": str(account.id),
                "asset_id": str(asset.id),
                "side": "buy",
                "quantity": "0.5",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["is_paper"] is True
    assert payload["execution_venue"] == "alpaca_paper"
    assert payload["broker_order_id"] == "alpaca-order-1"

    assert len(session.trades) == 1
    assert session.trades[0].is_paper is True
    assert session.trades[0].execution_venue == "alpaca_paper"
    assert len(session.audit_log_rows) == 1


def test_submit_stock_paper_order_rejects_fractional_when_asset_not_supported() -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Stock Paper",
        asset_class="stock",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="MSFT",
        asset_class="stock",
        exchange="alpaca",
        supports_fractional=False,
        is_active=True,
    )

    with create_test_client(_FakeSession(accounts=[account], assets=[asset])) as client:
        response = client.post(
            "/paper/orders/alpaca",
            json={
                "account_id": str(account.id),
                "asset_id": str(asset.id),
                "side": "buy",
                "quantity": "0.5",
            },
        )

    assert response.status_code == 400


def test_get_stock_paper_order_status(monkeypatch) -> None:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    account = PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="Family Stock Paper",
        asset_class="stock",
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        is_active=True,
        created_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="AAPL",
        asset_class="stock",
        exchange="alpaca",
        supports_fractional=True,
        is_active=True,
    )

    async def fake_get_alpaca_paper_order(*args, **kwargs):
        return _sample_order_result()

    monkeypatch.setattr(paper_route_module, "get_alpaca_paper_order", fake_get_alpaca_paper_order)

    with create_test_client(_FakeSession(accounts=[account], assets=[asset])) as client:
        response = client.get(
            f"/paper/orders/alpaca/alpaca-order-1?account_id={account.id}&asset_id={asset.id}"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["broker_order_id"] == "alpaca-order-1"
    assert payload["is_paper"] is True
    assert payload["execution_venue"] == "alpaca_paper"
