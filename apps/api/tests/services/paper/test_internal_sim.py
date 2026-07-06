from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.services.paper.internal_sim import execute_internal_crypto_fill


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
    def __init__(
        self,
        *,
        accounts: list[PaperAccount],
        assets: list[Asset],
        candles: list[Candle],
        trades: list[Trade] | None = None,
    ) -> None:
        self.accounts = accounts
        self.assets = assets
        self.candles = candles
        self.trades = trades or []
        self.audit_logs: list[AuditLog] = []

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

        if "FROM trades" in sql and "SELECT" in sql:
            account_id = next((value for value in values if isinstance(value, uuid.UUID)), None)
            asset_id = values[-1] if values else None
            rows = [
                trade
                for trade in self.trades
                if trade.paper_account_id == account_id and trade.asset_id == asset_id
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
async def test_execute_internal_crypto_fill_buy_records_paper_trade_and_audit() -> None:
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    session = _FakeSession(
        accounts=[
            PaperAccount(
                id=account_id,
                owner_user_id=uuid.uuid4(),
                name="Family Paper",
                asset_class="crypto",
                starting_balance=Decimal("25"),
                current_cash_balance=Decimal("25"),
                is_active=True,
                created_at=now,
            )
        ],
        assets=[
            Asset(
                id=asset_id,
                symbol="BTCUSDT",
                asset_class="crypto",
                exchange="binance_us",
                supports_fractional=True,
                qty_step_size=Decimal("0.00001"),
                min_order_notional=Decimal("1"),
                is_active=True,
            )
        ],
        candles=[
            Candle(
                asset_id=asset_id,
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

    result = await execute_internal_crypto_fill(
        db=session,
        paper_account_id=account_id,
        asset_id=asset_id,
        side="buy",
        quantity=Decimal("0.100009"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        actor="system",
        executed_at=now,
    )

    assert result.quantity == Decimal("0.10000")
    assert result.reference_price == Decimal("100")
    assert result.executed_price == Decimal("100.0500")
    assert result.fee_paid == Decimal("0.010005000")
    assert result.slippage_cost == Decimal("0.0050000")
    assert result.execution_venue == "internal_sim"

    assert len(session.trades) == 1
    trade = session.trades[0]
    assert trade.is_paper is True
    assert trade.execution_venue == "internal_sim"
    assert trade.side == "buy"

    assert session.accounts[0].current_cash_balance == result.cash_after

    assert len(session.audit_logs) == 1
    audit_entry = session.audit_logs[0]
    assert audit_entry.action == "paper_trade_simulated"
    assert audit_entry.after_state is not None
    assert audit_entry.after_state["slippage_bps"] == "5"
    assert audit_entry.after_state["fee_bps"] == "10"


@pytest.mark.asyncio
async def test_execute_internal_crypto_fill_rejects_insufficient_cash() -> None:
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    session = _FakeSession(
        accounts=[
            PaperAccount(
                id=account_id,
                owner_user_id=uuid.uuid4(),
                name="Tiny Paper",
                asset_class="crypto",
                starting_balance=Decimal("25"),
                current_cash_balance=Decimal("1"),
                is_active=True,
                created_at=now,
            )
        ],
        assets=[
            Asset(
                id=asset_id,
                symbol="BTCUSDT",
                asset_class="crypto",
                exchange="binance_us",
                supports_fractional=True,
                qty_step_size=Decimal("0.00001"),
                min_order_notional=Decimal("1"),
                is_active=True,
            )
        ],
        candles=[
            Candle(
                asset_id=asset_id,
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

    with pytest.raises(InvalidRequestError) as exc_info:
        await execute_internal_crypto_fill(
            db=session,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.02"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            actor="system",
            executed_at=now,
        )

    assert "Insufficient paper cash balance" in str(exc_info.value)


@pytest.mark.asyncio
async def test_execute_internal_crypto_fill_sell_requires_existing_position() -> None:
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    session = _FakeSession(
        accounts=[
            PaperAccount(
                id=account_id,
                owner_user_id=uuid.uuid4(),
                name="Family Paper",
                asset_class="crypto",
                starting_balance=Decimal("25"),
                current_cash_balance=Decimal("25"),
                is_active=True,
                created_at=now,
            )
        ],
        assets=[
            Asset(
                id=asset_id,
                symbol="BTCUSDT",
                asset_class="crypto",
                exchange="binance_us",
                supports_fractional=True,
                qty_step_size=Decimal("0.00001"),
                min_order_notional=Decimal("1"),
                is_active=True,
            )
        ],
        candles=[
            Candle(
                asset_id=asset_id,
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
        trades=[
            Trade(
                paper_account_id=account_id,
                asset_id=asset_id,
                side="buy",
                quantity=Decimal("0.01"),
                price=Decimal("100"),
                fee=Decimal("0.01"),
                is_paper=True,
                execution_venue="internal_sim",
                executed_at=now,
            )
        ],
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        await execute_internal_crypto_fill(
            db=session,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="sell",
            quantity=Decimal("0.02"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            actor="system",
            executed_at=now,
        )

    assert "Insufficient position quantity for sell" in str(exc_info.value)
