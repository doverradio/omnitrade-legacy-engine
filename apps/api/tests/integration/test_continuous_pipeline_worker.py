from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import PendingRollbackError

from app.services.orchestration.continuous_pipeline_worker import WorkerConfig, run_orchestration_cycle
from app.services.strategies.base import Signal
from app.services.strategies.registry import StrategyLookupError


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if hasattr(obj, "id") and getattr(obj, "id") is None:
                setattr(obj, "id", uuid.uuid4())

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _RecoveryAwareDB(_FakeDB):
    def __init__(self) -> None:
        super().__init__()
        self.pending: list[object] = []
        self.committed: list[object] = []
        self.failed_transaction = False
        self.snapshot_writes = 0

    def add(self, obj: object) -> None:
        if self.failed_transaction:
            raise PendingRollbackError("research transaction pending rollback", None, None)
        self.added.append(obj)
        self.pending.append(obj)

    async def flush(self) -> None:
        if self.failed_transaction:
            raise PendingRollbackError("research transaction pending rollback", None, None)
        await super().flush()

    async def commit(self) -> None:
        if self.failed_transaction:
            raise PendingRollbackError("research transaction pending rollback", None, None)
        self.commits += 1
        self.committed.extend(self.pending)
        self.pending.clear()


class _ResumeCapableDB(_FakeDB):
    async def scalar(self, *_args, **_kwargs):
        return None

    async def scalars(self, *_args, **_kwargs):
        return []

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.failed_transaction = False
        self.pending.clear()


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


@pytest.mark.asyncio
async def test_worker_records_research_cycle_started_in_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setitem(worker_module.venue_commissioning_service, "resume_runs", _async_return(0))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=True,
                reason=None,
                campaign_id=uuid.uuid4(),
                candidates_generated=2,
                candidates_evaluated=2,
                descendants_generated=1,
                champion="Deterministic Champion",
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.research_cycles_started == 1
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_worker_invokes_commissioning_resume_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ResumeCapableDB()
    resume_calls = {"count": 0}

    async def _resume_runs(*, db, actor, limit):
        assert actor == "orchestration_worker"
        assert limit == 10
        assert db is not None
        resume_calls["count"] += 1
        return 1

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setitem(worker_module.venue_commissioning_service, "resume_runs", _resume_runs)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert resume_calls["count"] == 1


@pytest.mark.asyncio
async def test_worker_isolates_commissioning_resume_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()

    async def _resume_fail(*_args, **_kwargs):
        raise RuntimeError("resume failed")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setitem(worker_module.venue_commissioning_service, "resume_runs", _resume_fail)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1


@pytest.mark.asyncio
async def test_worker_isolates_research_cycle_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()

    async def _raise_research(*_args, **_kwargs):
        raise RuntimeError("research failure")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _raise_research)
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert stats.research_cycles_started == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_research_failure_triggers_rollback_and_later_operation_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _RecoveryAwareDB()

    async def _raise_research(*_args, **_kwargs):
        db.add(SimpleNamespace(__class__=SimpleNamespace(__name__="ResearchLaboratoryRun"), kind="research_parent"))
        db.add(SimpleNamespace(__class__=SimpleNamespace(__name__="ResearchAgentActivity"), kind="research_child"))
        db.failed_transaction = True
        raise RuntimeError("forced research persistence failure")

    async def _snapshot_after_failure(*, db):
        db.add(SimpleNamespace(kind="snapshot_record"))
        db.snapshot_writes += 1
        return SimpleNamespace(snapshot_id=uuid.uuid4())

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _raise_research)
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _snapshot_after_failure)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.research_cycles_started == 0
    assert db.rollbacks == 1
    assert db.snapshot_writes == 1
    assert all(getattr(item, "kind", None) != "research_parent" for item in db.committed)
    assert all(getattr(item, "kind", None) != "research_child" for item in db.committed)
    assert any(getattr(item, "kind", None) == "snapshot_record" for item in db.committed)


@pytest.mark.asyncio
async def test_previously_committed_work_remains_intact_after_research_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _RecoveryAwareDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())

    async def _fake_orchestrate(*args, **kwargs):
        return SimpleNamespace(execution_status="executed", outcome="EXECUTED")

    async def _raise_research(*_args, **_kwargs):
        db.add(SimpleNamespace(kind="research_parent"))
        db.failed_transaction = True
        raise RuntimeError("forced research persistence failure")

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
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _raise_research)
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 1
    assert db.rollbacks == 1
    assert any(item.__class__.__name__ == "Signal" for item in db.committed)
    assert not any(getattr(item, "kind", None) == "research_parent" for item in db.committed)


@pytest.mark.asyncio
async def test_repeated_research_failures_do_not_corrupt_worker_session(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _RecoveryAwareDB()

    async def _raise_research(*_args, **_kwargs):
        db.add(SimpleNamespace(kind="research_parent"))
        db.failed_transaction = True
        raise RuntimeError("forced research persistence failure")

    async def _snapshot_after_failure(*, db):
        db.add(SimpleNamespace(kind="snapshot_record"))
        db.snapshot_writes += 1
        return SimpleNamespace(snapshot_id=uuid.uuid4())

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _raise_research)
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _snapshot_after_failure)

    first = await run_orchestration_cycle(db=db, client=object(), config=_config())
    second = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert first.research_cycles_started == 0
    assert second.research_cycles_started == 0
    assert db.rollbacks == 2
    assert db.snapshot_writes == 2


@pytest.mark.asyncio
async def test_research_disabled_mode_leaves_non_research_work_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()

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
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="research_disabled",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert stats.signals_created == 1
    assert stats.research_cycles_started == 0
    assert any(
        item.__class__.__name__ == "AuditLog" and getattr(item, "action", None) == "research_cycle_disabled"
        for item in db.added
    )


@pytest.mark.asyncio
async def test_worker_continues_after_structured_execution_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    assets = [_asset(), _asset()]
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())
    execution_calls = {"count": 0}

    async def _fake_orchestrate(*args, **kwargs):
        execution_calls["count"] += 1
        if execution_calls["count"] == 1:
            return SimpleNamespace(
                execution_status="rejected",
                outcome="REJECTED",
                reason_code="INSUFFICIENT_POSITION_QUANTITY",
                reason_text="Insufficient position quantity for sell",
                reason_details={"held_quantity": "0"},
            )
        return SimpleNamespace(
            execution_status="executed",
            outcome="EXECUTED",
            reason_code=None,
            reason_text=None,
            reason_details=None,
        )

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return(assets))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(account))
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _fake_orchestrate)
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("sell"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert execution_calls["count"] == 2
    assert stats.signals_created == 2
    assert stats.execution_candidates == 2
    assert stats.executions_attempted == 2
    assert stats.executions_rejected == 1
    assert stats.executions_failed == 0


@pytest.mark.asyncio
async def test_worker_triggers_one_autonomous_cycle_for_latest_kraken_btc_candle(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    mandate_id = uuid.uuid4()
    candle_close = datetime(2026, 7, 9, 12, 15, tzinfo=timezone.utc)
    captured: dict[str, object] = {}

    async def _capture_cycle(*, db, request):
        captured["request"] = request
        return SimpleNamespace(
            cycle_id=uuid.uuid4(),
            state="COMPLETE",
            replayed=False,
            idempotency_key="cycle-idem",
        )

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_single_active_kraken_mandate", _async_return(SimpleNamespace(mandate_id=mandate_id)))
    monkeypatch.setattr(
        worker_module,
        "_load_latest_kraken_btc_15m_candle",
        _async_return(SimpleNamespace(close_time=candle_close)),
    )
    monkeypatch.setattr(worker_module, "run_autonomous_preview_cycle", _capture_cycle)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    request = captured["request"]
    assert request.mandate_id == mandate_id
    assert request.actor == "orchestration_worker"
    assert request.product_id == "BTC-USD"
    assert request.strategy_interval == "15m"
    assert request.trigger == "kraken_btc_15m_candle_close"
    assert request.idempotency_seed == "kraken-btc-15m-close:2026-07-09T12:15:00+00:00"


@pytest.mark.asyncio
async def test_worker_skips_autonomous_cycle_when_no_active_kraken_mandate(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    called = {"count": 0}

    async def _capture_cycle(*, db, request):
        called["count"] += 1
        return SimpleNamespace(cycle_id=uuid.uuid4(), state="COMPLETE", replayed=False, idempotency_key="cycle-idem")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_single_active_kraken_mandate", _async_return(None))
    monkeypatch.setattr(worker_module, "run_autonomous_preview_cycle", _capture_cycle)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_worker_rolls_back_and_continues_when_autonomous_cycle_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()

    async def _raise_cycle(*, db, request):
        raise RuntimeError("autonomous cycle failure")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_single_active_kraken_mandate", _async_return(SimpleNamespace(mandate_id=uuid.uuid4())))
    monkeypatch.setattr(
        worker_module,
        "_load_latest_kraken_btc_15m_candle",
        _async_return(
            SimpleNamespace(
                asset_id=uuid.uuid4(),
                open_time=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                close_time=datetime(2026, 7, 9, 12, 15, tzinfo=timezone.utc),
            )
        ),
    )
    monkeypatch.setattr(worker_module, "run_autonomous_preview_cycle", _raise_cycle)
    monkeypatch.setattr(
        worker_module,
        "run_strategy_roster_for_candle",
        _async_return(SimpleNamespace(roster_run_id=uuid.uuid4(), replayed=False)),
    )

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_worker_triggers_strategy_roster_with_autonomous_cycle_link(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    captured: dict[str, object] = {}
    cycle_id = uuid.uuid4()
    candle = SimpleNamespace(
        asset_id=uuid.uuid4(),
        open_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 10, 12, 15, tzinfo=timezone.utc),
    )

    async def _capture_roster(*, db, request):
        captured["request"] = request
        return SimpleNamespace(roster_run_id=uuid.uuid4(), replayed=False)

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_run_kraken_btc_autonomous_cycle_if_due", _async_return((cycle_id, candle)))
    monkeypatch.setattr(worker_module, "run_strategy_roster_for_candle", _capture_roster)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    request = captured["request"]
    assert request.asset_id == candle.asset_id
    assert request.candle_close_time == candle.close_time
    assert request.scheduled_cycle_id == cycle_id


@pytest.mark.asyncio
async def test_worker_still_runs_roster_when_autonomous_cycle_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    captured = {"count": 0}
    candle = SimpleNamespace(
        asset_id=uuid.uuid4(),
        open_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 10, 12, 15, tzinfo=timezone.utc),
    )

    async def _capture_roster(*, db, request):
        captured["count"] += 1
        return SimpleNamespace(roster_run_id=uuid.uuid4(), replayed=False)

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_run_kraken_btc_autonomous_cycle_if_due", _async_return((None, None)))
    monkeypatch.setattr(worker_module, "_load_latest_kraken_btc_15m_candle", _async_return(candle))
    monkeypatch.setattr(worker_module, "run_strategy_roster_for_candle", _capture_roster)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert captured["count"] == 1


@pytest.mark.asyncio
async def test_worker_rolls_back_and_continues_when_roster_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    candle = SimpleNamespace(
        asset_id=uuid.uuid4(),
        open_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 10, 12, 15, tzinfo=timezone.utc),
    )

    async def _raise_roster(*, db, request):
        raise RuntimeError("roster failed")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_run_kraken_btc_autonomous_cycle_if_due", _async_return((uuid.uuid4(), candle)))
    monkeypatch.setattr(worker_module, "run_strategy_roster_for_candle", _raise_roster)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="not_due",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_run_orchestration_cycle_passes_kraken_client_to_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    captured: dict[str, object] = {}

    async def _fake_ingestion_cycle(db_arg, client_arg, kraken_client_arg, **kwargs):
        captured["db"] = db_arg
        captured["client"] = client_arg
        captured["kraken_client"] = kraken_client_arg
        captured["interval"] = kwargs.get("interval")
        return SimpleNamespace(successful_assets=0)

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(
        worker_module,
        "run_deterministic_research_cycle_if_due",
        _async_return(
            SimpleNamespace(
                started=False,
                reason="research_disabled",
                campaign_id=None,
                candidates_generated=0,
                candidates_evaluated=0,
                descendants_generated=0,
                champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    kraken_client = object()
    client = object()

    await run_orchestration_cycle(db=db, client=client, kraken_client=kraken_client, config=_config())

    assert captured["db"] is db
    assert captured["client"] is client
    assert captured["kraken_client"] is kraken_client
    assert captured["interval"] == "1m"


@pytest.mark.asyncio
async def test_run_forever_initializes_kraken_client_and_passes_it_to_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    captured: dict[str, object] = {}

    class _FakeHTTPClient:
        async def __aenter__(self):
            captured["http_client"] = self
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _fake_run_orchestration_cycle(db, **kwargs):
        captured["db"] = db
        captured["cycle_kwargs"] = kwargs
        return SimpleNamespace(
            ingestion_assets_ok=1,
            signals_created=0,
            execution_candidates=0,
            executions_attempted=0,
            executions_rejected=0,
            executions_failed=0,
            executions_skipped=0,
            decisions_inserted=0,
            research_cycles_started=0,
            intelligence_snapshots_captured=0,
        )

    async def _fake_sleep(_seconds: float) -> None:
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(worker_module, "setup_logging", lambda: None)
    monkeypatch.setattr(worker_module.WorkerConfig, "from_env", staticmethod(_config))
    monkeypatch.setattr(worker_module, "AsyncHTTPClient", _FakeHTTPClient)
    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _FakeSessionContext)
    monkeypatch.setattr(worker_module, "BinanceUSClient", lambda http_client: (captured.update({"binance_http": http_client}) or "binance-client"))
    monkeypatch.setattr(worker_module, "KrakenSpotClient", lambda http_client: (captured.update({"kraken_http": http_client}) or "kraken-client"))
    monkeypatch.setattr(worker_module, "run_orchestration_cycle", _fake_run_orchestration_cycle)
    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker_module.run_forever()

    assert captured["binance_http"] is captured["http_client"]
    assert captured["kraken_http"] is captured["http_client"]
    cycle_kwargs = captured["cycle_kwargs"]
    assert cycle_kwargs["client"] == "binance-client"
    assert cycle_kwargs["kraken_client"] == "kraken-client"
