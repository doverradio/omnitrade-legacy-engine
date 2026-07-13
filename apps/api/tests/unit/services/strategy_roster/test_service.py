from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.strategy_roster.contracts import StrategyRosterRequest
from app.services.strategy_roster.registry import ENABLED_PHASE1_ROSTER
from app.services.strategy_roster.service import run_strategy_roster_for_candle


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, *, candles: list[SimpleNamespace], strategy_rows: list[SimpleNamespace], parameter_sets: dict[uuid.UUID, dict[str, object]] | None = None):
        self.candles = candles
        self.strategy_rows = strategy_rows
        self.parameter_sets = parameter_sets or {}
        self.runs_by_key: dict[str, StrategyRosterRun] = {}
        self.proposals: list[StrategyRosterProposal] = []

    def add(self, item):
        if isinstance(item, StrategyRosterRun):
            if getattr(item, "roster_run_id", None) is None:
                item.roster_run_id = uuid.uuid4()
            self.runs_by_key[item.idempotency_key] = item
            return
        if isinstance(item, StrategyRosterProposal):
            if getattr(item, "proposal_id", None) is None:
                item.proposal_id = uuid.uuid4()
            self.proposals.append(item)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params

        if "FROM strategy_roster_runs" in sql and "idempotency_key" in sql:
            key = next((v for v in params.values() if isinstance(v, str) and len(v) == 64), None)
            if key is not None:
                return self.runs_by_key.get(key)
            return None

        if "FROM parameter_sets" in sql:
            strategy_id = next((v for v in params.values() if isinstance(v, uuid.UUID)), None)
            if strategy_id is None:
                return None
            params_payload = self.parameter_sets.get(strategy_id)
            if params_payload is None:
                return None
            return SimpleNamespace(id=uuid.uuid4(), params=params_payload)

        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM strategies" in sql:
            return _Result(self.strategy_rows)
        if "FROM candles" in sql:
            return _Result(self.candles)
        return _Result([])


class _ExplodingStrategy:
    default_params = {"lookback": 3, "threshold_pct": 1}

    def generate_signal(self, context):
        raise RuntimeError("boom")


def _candles(*, count: int, close_base: Decimal = Decimal("100"), close_step: Decimal = Decimal("1"), close_time: datetime | None = None):
    if close_time is None:
        close_time = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    rows: list[SimpleNamespace] = []
    start = close_time - timedelta(minutes=15 * (count - 1))
    for idx in range(count):
        open_time = start + timedelta(minutes=15 * idx)
        candle_close = open_time + timedelta(minutes=15)
        close = close_base + (close_step * Decimal(idx))
        rows.append(
            SimpleNamespace(
                asset_id=uuid.uuid4(),
                interval="15m",
                open_time=open_time,
                close_time=candle_close,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1"),
            )
        )
    asset_id = uuid.uuid4()
    for row in rows:
        row.asset_id = asset_id
    return rows


def _strategy_rows() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id=uuid.uuid4(), slug=slug, module_version="1.0.0")
        for slug in ENABLED_PHASE1_ROSTER
    ]


def _request(candles: list[SimpleNamespace]) -> StrategyRosterRequest:
    latest = candles[-1]
    return StrategyRosterRequest(
        asset_id=latest.asset_id,
        provider="kraken_spot",
        product_id="BTC-USD",
        interval="15m",
        candle_open_time=latest.open_time,
        candle_close_time=latest.close_time,
        trigger="kraken_btc_15m_candle_close",
        scheduled_cycle_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_roster_creates_one_run_and_one_proposal_per_strategy() -> None:
    candles = _candles(count=80)
    strategy_rows = _strategy_rows()
    db = _FakeDb(candles=candles, strategy_rows=strategy_rows)

    result = await run_strategy_roster_for_candle(db=db, request=_request(candles))

    assert result.replayed is False
    assert len(db.runs_by_key) == 1
    assert len(db.proposals) == len(ENABLED_PHASE1_ROSTER)


@pytest.mark.asyncio
async def test_roster_same_candle_replays_without_duplicates() -> None:
    candles = _candles(count=80)
    strategy_rows = _strategy_rows()
    db = _FakeDb(candles=candles, strategy_rows=strategy_rows)
    request = _request(candles)

    first = await run_strategy_roster_for_candle(db=db, request=request)
    second = await run_strategy_roster_for_candle(db=db, request=request)

    assert first.replayed is False
    assert second.replayed is True
    assert len(db.runs_by_key) == 1
    assert len(db.proposals) == len(ENABLED_PHASE1_ROSTER)


@pytest.mark.asyncio
async def test_roster_new_candle_creates_new_run() -> None:
    candles_a = _candles(count=80)
    candles_b = _candles(count=81)
    strategy_rows = _strategy_rows()
    db = _FakeDb(candles=candles_a, strategy_rows=strategy_rows)

    await run_strategy_roster_for_candle(db=db, request=_request(candles_a))
    db.candles = candles_b
    await run_strategy_roster_for_candle(db=db, request=_request(candles_b))

    assert len(db.runs_by_key) == 2
    assert len(db.proposals) == len(ENABLED_PHASE1_ROSTER) * 2


@pytest.mark.asyncio
async def test_roster_insufficient_history_fails_closed() -> None:
    candles = _candles(count=2)
    strategy_rows = _strategy_rows()
    db = _FakeDb(candles=candles, strategy_rows=strategy_rows)

    await run_strategy_roster_for_candle(db=db, request=_request(candles))

    assert len(db.proposals) == len(ENABLED_PHASE1_ROSTER)
    assert all(item.action == "HOLD" for item in db.proposals)
    assert all(item.evaluation_status in {"INSUFFICIENT_CONTEXT", "FAILED"} for item in db.proposals)


@pytest.mark.asyncio
async def test_roster_strategy_exception_does_not_block_other_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = _candles(count=80)
    strategy_rows = _strategy_rows()
    db = _FakeDb(candles=candles, strategy_rows=strategy_rows)

    import app.services.strategy_roster.service as roster_module

    original_get = roster_module.strategy_registry.get

    def _patched_get(slug: str):
        if slug == "momentum":
            return _ExplodingStrategy()
        return original_get(slug)

    monkeypatch.setattr(roster_module.strategy_registry, "get", _patched_get)

    await run_strategy_roster_for_candle(db=db, request=_request(candles))

    assert len(db.proposals) == len(ENABLED_PHASE1_ROSTER)
    failing = [item for item in db.proposals if item.strategy_slug == "momentum"]
    assert len(failing) == 1
    assert failing[0].evaluation_status == "FAILED"

    non_failing = [item for item in db.proposals if item.strategy_slug != "momentum"]
    assert all(item.evaluation_status in {"EVALUATED", "INSUFFICIENT_CONTEXT"} for item in non_failing)
