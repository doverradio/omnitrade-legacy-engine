from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.orchestration.continuous_pipeline_worker import WorkerConfig, run_orchestration_cycle
from app.services.strategies.base import Signal
from app.services.strategies.registry import StrategyLookupError


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if hasattr(obj, "id") and getattr(obj, "id") is None:
                setattr(obj, "id", uuid.uuid4())

    async def commit(self) -> None:
        self.commits += 1


class _FixedStrategy:
    def __init__(self, action: str) -> None:
        self._action = action

    def generate_signal(self, context) -> Signal:
        return Signal(
            action=self._action,
            strength=Decimal("0.60"),
            reason=f"{self._action} signal",
            indicators={"source": "test"},
            timestamp=context.candles[-1]["open_time"],
        )


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _config() -> WorkerConfig:
    return WorkerConfig(
        poll_interval_seconds=300,
        candle_interval="1m",
        candle_lookback_limit=120,
        default_order_quantity=Decimal("1"),
    )


def _asset() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        asset_class="crypto",
        symbol="BTCUSDT",
        exchange="binance_us",
    )


def _strategy_row() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), slug="ma_crossover", is_active=True)


def _disabled_strategy_row() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), slug="rsi_mean_reversion", is_active=False)


def _parameter_set() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), params={"fast_period": 10, "slow_period": 50})


def _candles(count: int) -> list[SimpleNamespace]:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    rows: list[SimpleNamespace] = []
    for index in range(count):
        open_time = now.replace(minute=index)
        rows.append(
            SimpleNamespace(
                open_time=open_time,
                close_time=open_time,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
        )
    return rows


async def _fake_ingestion_cycle(*args, **kwargs):
    return SimpleNamespace(successful_assets=1)


async def _fake_decision_ingestion(*args, **kwargs):
    return SimpleNamespace(inserted_records=1)


@pytest.mark.asyncio
async def test_new_buy_signal_reaches_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())
    orchestration_calls = {"count": 0}

    async def _fake_orchestrate(*args, **kwargs):
        orchestration_calls["count"] += 1
        return SimpleNamespace(execution_status="executed")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(account))
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _fake_orchestrate)
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("buy"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert orchestration_calls["count"] == 1
    assert stats.signals_created == 1
    assert stats.execution_candidates == 1
    assert stats.executions_attempted == 1
    assert stats.executions_skipped == 0


@pytest.mark.asyncio
async def test_one_enabled_strategy_generates_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 1
    generated_signals = [item for item in db.added if item.__class__.__name__ == "Signal"]
    assert len(generated_signals) == 1
    assert generated_signals[0].strategy_id == strategy.id


@pytest.mark.asyncio
async def test_two_enabled_strategies_each_generate_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy_a = _strategy_row()
    strategy_b = SimpleNamespace(id=uuid.uuid4(), slug="rsi_mean_reversion", is_active=True)
    parameter_set_a = _parameter_set()
    parameter_set_b = SimpleNamespace(id=uuid.uuid4(), params={"rsi_period": 14})

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy_a, strategy_b]))

    async def _load_parameter_set(*args, strategy_id, **kwargs):
        if strategy_id == strategy_a.id:
            return parameter_set_a
        return parameter_set_b

    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _load_parameter_set)
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 2
    generated_signals = [item for item in db.added if item.__class__.__name__ == "Signal"]
    strategy_ids = {item.strategy_id for item in generated_signals}
    assert strategy_ids == {strategy_a.id, strategy_b.id}


@pytest.mark.asyncio
async def test_disabled_strategy_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    enabled_strategy = _strategy_row()
    disabled_strategy = _disabled_strategy_row()
    parameter_set = _parameter_set()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([enabled_strategy, disabled_strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 1
    generated_signals = [item for item in db.added if item.__class__.__name__ == "Signal"]
    assert len(generated_signals) == 1
    assert generated_signals[0].strategy_id == enabled_strategy.id


@pytest.mark.asyncio
async def test_new_buy_signal_without_account_logs_skip_reason(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    orchestration_calls = {"count": 0}

    async def _fake_orchestrate(*args, **kwargs):
        orchestration_calls["count"] += 1
        return SimpleNamespace(execution_status="executed")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _fake_orchestrate)
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("buy"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert orchestration_calls["count"] == 0
    assert stats.signals_created == 1
    assert stats.execution_candidates == 1
    assert stats.executions_attempted == 0
    assert stats.executions_skipped == 1
    assert "paper_execution_skip reason=no_active_paper_account" in caplog.text


@pytest.mark.asyncio
async def test_new_hold_signal_logs_non_actionable_skip(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 1
    assert stats.execution_candidates == 0
    assert stats.executions_attempted == 0
    assert stats.executions_skipped == 1
    assert "paper_execution_skip reason=non_actionable_action" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "strategy_get", "parameter_set", "candles", "signal_exists", "expected_reason"),
    [
        (
            "unregistered_strategy",
            lambda slug: (_ for _ in ()).throw(StrategyLookupError("missing")),
            _parameter_set(),
            _candles(2),
            False,
            "paper_execution_skip reason=unregistered_strategy",
        ),
        (
            "missing_parameter_set",
            lambda slug: _FixedStrategy("buy"),
            None,
            _candles(2),
            False,
            "paper_execution_skip reason=missing_parameter_set",
        ),
        (
            "insufficient_candles",
            lambda slug: _FixedStrategy("buy"),
            _parameter_set(),
            _candles(1),
            False,
            "paper_execution_skip reason=insufficient_candles",
        ),
        (
            "duplicate_existing_signal",
            lambda slug: _FixedStrategy("buy"),
            _parameter_set(),
            _candles(2),
            True,
            "paper_execution_skip reason=duplicate_existing_signal",
        ),
    ],
)
async def test_worker_logs_early_continue_reasons(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case_name: str,
    strategy_get,
    parameter_set,
    candles,
    signal_exists: bool,
    expected_reason: str,
) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(candles))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(signal_exists))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", strategy_get)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.executions_attempted == 0
    assert expected_reason in caplog.text
