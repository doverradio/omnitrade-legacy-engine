from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.position_lifecycle.source_adapter import load_position_snapshots


@dataclass
class _Profile:
    id: uuid.UUID
    paper_account_id: uuid.UUID


@dataclass
class _Record:
    live_trading_profile_id: uuid.UUID
    capital_campaign_id: int | None
    symbol: str
    side: str
    record_type: str
    provider_order_id: str
    provider_fill_id: str | None
    filled_quantity: Decimal
    gross_notional: Decimal
    fee_amount: Decimal
    recorded_at: datetime


@dataclass
class _Asset:
    id: uuid.UUID
    symbol: str
    asset_class: str
    exchange: str = "kraken"
    is_active: bool = True


@dataclass
class _Candle:
    id: int
    asset_id: uuid.UUID
    interval: str
    close_time: datetime
    close: Decimal
    source: str


class _Result:
    def __init__(self, rows=None, scalars=None, scalar_one_or_none=None):
        self._rows = rows or []
        self._scalars = scalars or []
        self._scalar_one_or_none = scalar_one_or_none

    def all(self):
        return self._rows

    def scalars(self):
        class _Scalars:
            def __init__(self, values):
                self._values = values

            def all(self):
                return self._values

        return _Scalars(self._scalars)

    def scalar_one_or_none(self):
        return self._scalar_one_or_none


class _ReadOnlyFakeSession:
    def __init__(self, *, rows, assets, candles):
        self._rows = rows
        self._assets = assets
        self._candles = candles
        self.execute_calls = 0
        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1
        sql = str(statement)
        params = statement.compile().params
        if "FROM live_accounting_records" in sql:
            rows = self._rows
            for key, value in params.items():
                if "paper_account_id" in key:
                    rows = [row for row in rows if row[1] == value]
                if "capital_campaign_id" in key:
                    rows = [row for row in rows if row[0].capital_campaign_id == value]
            return _Result(rows=rows)
        if "FROM assets" in sql:
            return _Result(scalars=self._assets)
        if "FROM candles" in sql:
            for key, value in params.items():
                if "asset_id" in key and value in self._candles:
                    return _Result(scalar_one_or_none=self._candles[value])
            return _Result(scalar_one_or_none=None)
        return _Result()

    def add(self, _item):
        self.add_calls += 1

    async def flush(self):
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1


def _setup_base(now: datetime):
    profile_id = uuid.uuid4()
    account_id = uuid.uuid4()
    profile = _Profile(id=profile_id, paper_account_id=account_id)

    btc_asset_id = uuid.uuid4()
    eth_asset_id = uuid.uuid4()
    btc_asset = _Asset(id=btc_asset_id, symbol="BTC", asset_class="crypto")
    eth_asset = _Asset(id=eth_asset_id, symbol="ETH", asset_class="crypto")

    candles = {
        btc_asset_id: _Candle(
            id=101,
            asset_id=btc_asset_id,
            interval="15m",
            close_time=now - timedelta(minutes=2),
            close=Decimal("65010"),
            source="kraken_spot",
        ),
        eth_asset_id: _Candle(
            id=202,
            asset_id=eth_asset_id,
            interval="15m",
            close_time=now - timedelta(minutes=3),
            close=Decimal("3400"),
            source="kraken_spot",
        ),
    }

    return profile, [btc_asset, eth_asset], candles


@pytest.mark.asyncio
async def test_one_buy_fill_creates_open_position() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (
            _Record(
                live_trading_profile_id=profile.id,
                capital_campaign_id=1,
                symbol="BTC-USD",
                side="buy",
                record_type="fill_accounting",
                provider_order_id="ord-1",
                provider_fill_id="fill-1",
                filled_quantity=Decimal("1"),
                gross_notional=Decimal("100"),
                fee_amount=Decimal("0.5"),
                recorded_at=now - timedelta(minutes=10),
            ),
            profile.paper_account_id,
        )
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.position_size == Decimal("1")
    assert snapshot.entry_price == Decimal("100")
    assert snapshot.provider_order_ids == ("ord-1",)
    assert snapshot.provider_fill_ids == ("fill-1",)


@pytest.mark.asyncio
async def test_multiple_buys_aggregate_weighted_entry() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (
            _Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)),
            profile.paper_account_id,
        ),
        (
            _Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-2", "fill-2", Decimal("2"), Decimal("260"), Decimal("2"), now - timedelta(minutes=19)),
            profile.paper_account_id,
        ),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    snapshot = snapshots[0]
    assert snapshot.position_size == Decimal("3")
    assert snapshot.entry_price == Decimal("120")


@pytest.mark.asyncio
async def test_partial_sell_reduces_open_quantity() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("2"), Decimal("200"), Decimal("2"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(profile.id, 1, "BTC-USD", "sell", "fill_accounting", "ord-3", "fill-3", Decimal("0.5"), Decimal("55"), Decimal("0.5"), now - timedelta(minutes=10)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].position_size == Decimal("1.5")


@pytest.mark.asyncio
async def test_complete_sell_closes_position() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(profile.id, 1, "BTC-USD", "sell", "fill_accounting", "ord-2", "fill-2", Decimal("1"), Decimal("102"), Decimal("1"), now - timedelta(minutes=10)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].position_size == Decimal("0")


@pytest.mark.asyncio
async def test_fees_allocated_once_no_double_count_from_fee_attribution_rows() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("2"), Decimal("200"), Decimal("2"), now - timedelta(minutes=20)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].accumulated_entry_and_carry_costs == Decimal("2")


@pytest.mark.asyncio
async def test_multiple_instruments_do_not_mix() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(profile.id, 1, "ETH-USD", "buy", "fill_accounting", "ord-2", "fill-2", Decimal("2"), Decimal("300"), Decimal("1"), now - timedelta(minutes=19)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert len(snapshots) == 2
    assert {item.symbol for item in snapshots} == {"BTC-USD", "ETH-USD"}


@pytest.mark.asyncio
async def test_multiple_accounts_do_not_mix() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    other_profile_id = uuid.uuid4()
    other_account_id = uuid.uuid4()
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(other_profile_id, 1, "BTC-USD", "buy", "fill_accounting", "ord-2", "fill-2", Decimal("3"), Decimal("300"), Decimal("3"), now - timedelta(minutes=19)), other_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=profile.paper_account_id, campaign_id=None)

    assert len(snapshots) == 1
    assert snapshots[0].account_id == profile.paper_account_id


@pytest.mark.asyncio
async def test_multiple_campaigns_do_not_mix() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(profile.id, 2, "BTC-USD", "buy", "fill_accounting", "ord-2", "fill-2", Decimal("3"), Decimal("300"), Decimal("3"), now - timedelta(minutes=19)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=1)

    assert len(snapshots) == 1
    assert snapshots[0].capital_campaign_id == 1


@pytest.mark.asyncio
async def test_duplicate_records_do_not_inflate_quantity() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    duplicate = _Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20))
    rows = [(duplicate, profile.paper_account_id), (duplicate, profile.paper_account_id)]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].position_size == Decimal("1")
    assert snapshots[0].accounting_record_count == 1


@pytest.mark.asyncio
async def test_dust_remaining_after_sell_is_visible_for_classifier() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("0.0100"), Decimal("600"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
        (_Record(profile.id, 1, "BTC-USD", "sell", "fill_accounting", "ord-2", "fill-2", Decimal("0.00995"), Decimal("598"), Decimal("1"), now - timedelta(minutes=10)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].position_size == Decimal("0.00005")


@pytest.mark.asyncio
async def test_short_positions_fail_closed() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "sell", "fill_accounting", "ord-2", "fill-2", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=10)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].position_size == Decimal("0")
    assert snapshots[0].fail_closed_reason == "net_short_not_supported"


@pytest.mark.asyncio
async def test_price_evidence_provenance_and_quality_flags() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    snapshot = snapshots[0]
    assert snapshot.market_data_source == "kraken_spot"
    assert snapshot.market_data_interval == "15m"
    assert snapshot.market_data_candle_id == 101
    assert snapshot.market_data_timestamp is not None
    assert snapshot.current_price is not None and snapshot.current_price > 0


@pytest.mark.asyncio
async def test_same_evidence_produces_deterministic_result() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    first = await load_position_snapshots(db=db, account_id=None, campaign_id=None)
    second = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert first == second


@pytest.mark.asyncio
async def test_reject_future_timestamp_and_non_positive_prices_fail_closed() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    btc_asset = assets[0]
    candles[btc_asset.id] = _Candle(
        id=999,
        asset_id=btc_asset.id,
        interval="15m",
        close_time=now + timedelta(minutes=2),
        close=Decimal("0"),
        source="kraken_spot",
    )
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].fail_closed_reason in {"market_data_timestamp_in_future", "market_price_non_positive"}


@pytest.mark.asyncio
async def test_fail_closed_when_asset_symbol_maps_to_multiple_exchanges() -> None:
    now = datetime.now(timezone.utc)
    profile, assets, candles = _setup_base(now)
    extra_asset_id = uuid.uuid4()
    assets.append(_Asset(id=extra_asset_id, symbol="BTC", asset_class="crypto", exchange="binance"))
    candles[extra_asset_id] = _Candle(
        id=303,
        asset_id=extra_asset_id,
        interval="15m",
        close_time=now - timedelta(minutes=1),
        close=Decimal("65020"),
        source="binance",
    )
    rows = [
        (_Record(profile.id, 1, "BTC-USD", "buy", "fill_accounting", "ord-1", "fill-1", Decimal("1"), Decimal("100"), Decimal("1"), now - timedelta(minutes=20)), profile.paper_account_id),
    ]
    db = _ReadOnlyFakeSession(rows=rows, assets=assets, candles=candles)

    snapshots = await load_position_snapshots(db=db, account_id=None, campaign_id=None)

    assert snapshots[0].fail_closed_reason == "asset_symbol_ambiguous_multi_exchange"
