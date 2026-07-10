from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.services import profit_intelligence as service


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, accounts, trades_by_account):
        self._accounts = accounts
        self._trades_by_account = trades_by_account

    async def execute(self, statement):
        text = str(statement)
        if "FROM paper_accounts" in text:
            return _FakeResult(self._accounts)
        if "FROM trades" in text:
            account_id = None
            for item in self._accounts:
                if str(item.id) in text:
                    account_id = item.id
                    break
            return _FakeResult(self._trades_by_account.get(account_id, []))
        return _FakeResult([])


def _account(account_id: str, starting_balance: str = "25"):
    return SimpleNamespace(
        id=uuid.UUID(account_id),
        starting_balance=Decimal(starting_balance),
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    )


def _trade(*, account_id: uuid.UUID, executed_at: datetime, side: str = "buy", quantity: str = "1", price: str = "1", fee: str = "0"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        paper_account_id=account_id,
        asset_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        signal_id=None,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal(fee),
        executed_at=executed_at,
    )


@pytest.mark.asyncio
async def test_series_does_not_drop_no_trade_account_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)

    account_a = _account("11111111-1111-1111-1111-111111111111")
    account_b = _account("22222222-2222-2222-2222-222222222222")

    trades_by_account = {
        account_a.id: [
            _trade(account_id=account_a.id, executed_at=start_at + timedelta(minutes=5), side="buy", quantity="1", price="1", fee="0"),
            _trade(account_id=account_a.id, executed_at=start_at + timedelta(minutes=35), side="sell", quantity="1", price="1", fee="0"),
        ],
        account_b.id: [],
    }

    async def _strategy_map(**_kwargs):
        return {}

    async def _symbol_map(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): "BTC-USD"}

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_strategy_map", _strategy_map)
    monkeypatch.setattr(service, "_load_symbol_map", _symbol_map)
    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    state = await service._build_paper_profit_state(
        db=_FakeDb([account_a, account_b], trades_by_account),
        start_at=start_at,
        end_at=end_at,
        capital_pool_id=None,
        validation_run_id=None,
        strategy_id=None,
        symbol=None,
    )

    assert state.starting_equity == Decimal("50")
    assert state.ending_equity == Decimal("50")
    assert state.equity_series
    assert all((point.paper_equity or Decimal("0")) >= Decimal("50") for point in state.equity_series)
    assert (state.equity_series[-1].paper_equity or Decimal("0")) == state.ending_equity


@pytest.mark.asyncio
async def test_series_marks_opening_context_without_pre_start_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=2)
    account_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    trades = [
        _trade(account_id=account_id, executed_at=start_at - timedelta(minutes=10), side="buy", quantity="1", price="1", fee="0"),
        _trade(account_id=account_id, executed_at=start_at + timedelta(minutes=30), side="sell", quantity="1", price="1", fee="0"),
    ]

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    series = await service._build_equity_series(
        db=SimpleNamespace(),
        starting_balance=Decimal("25"),
        trades=trades,
        start_at=start_at,
        end_at=end_at,
    )

    assert series
    assert all(point.timestamp >= start_at for point in series)
    assert series[0].timestamp == start_at
    assert series[0].opening_context is True


@pytest.mark.asyncio
async def test_trade_exactly_at_boundary_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)
    account_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    boundary_trade = _trade(account_id=account_id, executed_at=start_at, side="buy", quantity="1", price="1", fee="0")

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    series = await service._build_equity_series(
        db=SimpleNamespace(),
        starting_balance=Decimal("25"),
        trades=[boundary_trade],
        start_at=start_at,
        end_at=end_at,
    )

    assert series
    first_point = series[0]
    assert first_point.source_event_count >= 1
    assert str(boundary_trade.id) in first_point.source_event_ids


@pytest.mark.asyncio
async def test_source_event_ids_are_bucket_local_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)
    account_id = uuid.UUID("55555555-5555-5555-5555-555555555555")

    many_same_bucket = [
        _trade(account_id=account_id, executed_at=start_at + timedelta(minutes=2), side="buy", quantity="1", price="1", fee="0")
        for _ in range(12)
    ]

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    series = await service._build_equity_series(
        db=SimpleNamespace(),
        starting_balance=Decimal("25"),
        trades=many_same_bucket,
        start_at=start_at,
        end_at=end_at,
    )

    assert series
    point_with_events = next(item for item in series if item.source_event_count > 0)
    assert point_with_events.source_event_count == 12
    assert len(point_with_events.source_event_ids) == 10
    assert point_with_events.source_event_ids_truncated is True


@pytest.mark.asyncio
async def test_capital_pool_filter_selects_single_account(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)

    account_a = _account("66666666-6666-6666-6666-666666666666")
    account_b = _account("77777777-7777-7777-7777-777777777777")

    async def _strategy_map(**_kwargs):
        return {}

    async def _symbol_map(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): "BTC-USD"}

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_strategy_map", _strategy_map)
    monkeypatch.setattr(service, "_load_symbol_map", _symbol_map)
    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    state = await service._build_paper_profit_state(
        db=_FakeDb([account_a, account_b], {account_a.id: [], account_b.id: []}),
        start_at=start_at,
        end_at=end_at,
        capital_pool_id=f"paper-account:{account_b.id}",
        validation_run_id=None,
        strategy_id=None,
        symbol=None,
    )

    assert state.starting_equity == Decimal("25")
    assert state.ending_equity == Decimal("25")


@pytest.mark.asyncio
async def test_validation_run_filter_fails_closed_without_account_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)
    account = _account("88888888-8888-8888-8888-888888888888")

    async def _strategy_map(**_kwargs):
        return {}

    async def _symbol_map(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): "BTC-USD"}

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_strategy_map", _strategy_map)
    monkeypatch.setattr(service, "_load_symbol_map", _symbol_map)
    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    state = await service._build_paper_profit_state(
        db=_FakeDb([account], {account.id: []}),
        start_at=start_at,
        end_at=end_at,
        capital_pool_id=None,
        validation_run_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        strategy_id=None,
        symbol=None,
    )

    assert state.starting_equity == Decimal("0")
    assert state.ending_equity == Decimal("0")
    assert state.source_counts["paper_accounts"] == 0


@pytest.mark.asyncio
async def test_validation_run_mapping_filters_accounts_without_cross_run_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)

    account_a = _account("aaaaaaaa-1111-1111-1111-111111111111")
    account_b = _account("bbbbbbbb-2222-2222-2222-222222222222")
    run_id = uuid.UUID("12345678-1234-1234-1234-123456789012")

    async def _strategy_map(**_kwargs):
        return {}

    async def _symbol_map(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): "BTC-USD"}

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_strategy_map", _strategy_map)
    monkeypatch.setattr(service, "_load_symbol_map", _symbol_map)
    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    class _ScopeDb(_FakeDb):
        async def execute(self, statement):
            text = str(statement)
            if "validation_run_paper_accounts" in text:
                return _FakeResult([account_a.id])
            return await super().execute(statement)

    state = await service._build_paper_profit_state(
        db=_ScopeDb([account_a, account_b], {account_a.id: [], account_b.id: []}),
        start_at=start_at,
        end_at=end_at,
        capital_pool_id=None,
        validation_run_id=run_id,
        strategy_id=None,
        symbol=None,
    )

    assert state.starting_equity == Decimal("25")
    assert state.ending_equity == Decimal("25")
    assert state.source_counts["paper_accounts"] == 1


@pytest.mark.asyncio
async def test_validation_run_capital_pool_filter_uses_same_mapping_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    start_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    end_at = start_at + timedelta(hours=1)

    account_a = _account("cccccccc-3333-3333-3333-333333333333")
    account_b = _account("dddddddd-4444-4444-4444-444444444444")
    run_id = uuid.UUID("87654321-4321-4321-4321-210987654321")

    async def _strategy_map(**_kwargs):
        return {}

    async def _symbol_map(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): "BTC-USD"}

    async def _prices(**_kwargs):
        return {uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"): Decimal("1")}

    monkeypatch.setattr(service, "_load_strategy_map", _strategy_map)
    monkeypatch.setattr(service, "_load_symbol_map", _symbol_map)
    monkeypatch.setattr(service, "_load_prices_as_of", _prices)

    class _ScopeDb(_FakeDb):
        async def execute(self, statement):
            text = str(statement)
            if "validation_run_paper_accounts" in text:
                return _FakeResult([account_b.id])
            return await super().execute(statement)

    state = await service._build_paper_profit_state(
        db=_ScopeDb([account_a, account_b], {account_a.id: [], account_b.id: []}),
        start_at=start_at,
        end_at=end_at,
        capital_pool_id=f"validation-run:{run_id}",
        validation_run_id=None,
        strategy_id=None,
        symbol=None,
    )

    assert state.starting_equity == Decimal("25")
    assert state.ending_equity == Decimal("25")
    assert state.source_counts["paper_accounts"] == 1
