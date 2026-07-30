from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.controlled_proof import exit_recovery
from app.services.orchestration.continuous_pipeline_worker import WorkerConfig, run_orchestration_cycle
from app.services.strategies.base import Signal
from app.services.strategies.registry import StrategyLookupError
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_SLUG
from tests.support.real_sqlite_session import real_sqlite_session_factory


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


@pytest.mark.asyncio
async def test_active_ready_package_check_excludes_expired_preview() -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    class _CaptureDb:
        statement = None

        async def scalar(self, statement):
            self.statement = statement
            return None

    db = _CaptureDb()
    observed_at = datetime.now(timezone.utc)
    assert await worker_module._has_active_ready_package_for_opportunity(
        db=db, decision_record_id=uuid.uuid4(), now=observed_at,
    ) is False
    sql = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "preview_expires_at" in sql
    assert ">" in sql


class _CampaignPreviewCapableDB(_FakeDB):
    async def scalar(self, *_args, **_kwargs):
        return None

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


class _MissingGreenletSimulation(RuntimeError):
    """Stands in for sqlalchemy.exc.MissingGreenlet: raised when code touches
    an attribute of an expired ORM instance outside the async greenlet
    bridge -- exactly what Session.rollback() sets up by expiring every
    instance the session was tracking."""


class _ExpiringCandle:
    def __init__(self, *, id, asset_id, open_time, close_time) -> None:
        self._values = {"id": id, "asset_id": asset_id, "open_time": open_time, "close_time": close_time}
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def __getattr__(self, name):
        if name not in self._values:
            raise AttributeError(name)
        if self._expired:
            raise _MissingGreenletSimulation(
                f"greenlet_spawn has not been called; attribute {name!r} requires a lazy refresh outside async context"
            )
        return self._values[name]


class _ExpiringSessionCampaignPreviewCapableDB(_CampaignPreviewCapableDB):
    """A campaign-preview-capable fake whose rollback() expires a tracked
    candle, mirroring Session.rollback()'s expire-everything behavior."""

    def __init__(self, *, tracked_candle: _ExpiringCandle) -> None:
        super().__init__()
        self._tracked_candle = tracked_candle

    async def rollback(self) -> None:
        await super().rollback()
        self._tracked_candle.expire()


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


class _MandateResolverDB:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.compiled_sql = ""

    async def execute(self, statement):
        self.compiled_sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        matches = [
            row
            for row in self.rows
            if row.status == "ACTIVE"
            and row.provider == "kraken_spot"
            and row.autonomy_level == "LEVEL_2"
        ]
        matches.sort(key=lambda row: row.updated_at, reverse=True)
        limited = matches[:2]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: limited))


def _active_kraken_mandate(*, autonomy_level: str, updated_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_id=uuid.uuid4(),
        status="ACTIVE",
        provider="kraken_spot",
        autonomy_level=autonomy_level,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


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


def _aggregate_strategy_row() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), slug=AGGREGATE_STRATEGY_SLUG, is_active=True)


def _kraken_asset() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        asset_class="crypto",
        symbol="BTCUSD",
        exchange="kraken_spot",
    )


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


def _decision_record() -> SimpleNamespace:
    return SimpleNamespace(
        decision_id=uuid.uuid4(),
        asset={"symbol": "BTCUSDT"},
        timeframe="1m",
        supporting_strategies=[{"strategy_identity": "ma_crossover@1", "action": "BUY", "confidence": 0.8}],
        opposing_strategies=[],
        expected_reward={"expected_value": "0.05"},
        generated_signals=[{"action": "buy"}],
        trade_accepted=True,
        trade_rejected_reason=None,
        confidence=Decimal("0.8"),
    )


_MISSING = object()


def _automatic_cycle(
    *,
    decision_record_id: uuid.UUID | None | object = _MISSING,
    termination_stage: str = "preview_generated",
    proposed_action: str = "OPEN_POSITION_PROPOSED",
    decision_kind: str = "OPEN_POSITION_PROPOSED",
    risk_verdict: str = "ALLOW",
    freshness: str = "fresh",
    final_amount: str = "5",
    selected_decision_reason: str | None = None,
    rejected_candidates: list[dict[str, object]] | None = None,
    instrument: str | None = None,
) -> SimpleNamespace:
    cycle_id = uuid.uuid4()
    campaign_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    if decision_record_id is _MISSING:
        decision_record_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    selected_decision: dict[str, object] = {
        "decision_kind": decision_kind,
        "risk_verdict": risk_verdict,
        "evidence_freshness": freshness,
        "sizing_trace": {"final_amount": final_amount},
    }
    if instrument is not None:
        selected_decision["instrument"] = instrument
    if selected_decision_reason is not None:
        selected_decision["reason"] = selected_decision_reason
    authoritative_composition: dict[str, object] = {
        "proposed_action": proposed_action,
        "selected_decision": selected_decision,
    }
    if rejected_candidates is not None:
        authoritative_composition["rejected_candidates"] = rejected_candidates
    return SimpleNamespace(
        cycle_id=cycle_id,
        capital_campaign_id=campaign_id,
        capital_campaign_version=3,
        decision_record_id=decision_record_id,
        mandate_id=uuid.uuid4(),
        mandate_version_id=uuid.uuid4(),
        mandate_evaluation_id=uuid.uuid4(),
        termination_stage=termination_stage,
        proposed_action=proposed_action,
        risk_verdict=risk_verdict,
        cycle_context={
            "candle": {"close_time": "2026-07-15T00:15:00+00:00"},
            "authoritative_composition": authoritative_composition,
        },
    )


def _automatic_payload(cycle: SimpleNamespace) -> dict[str, object]:
    return {"cycles": [{"cycle_id": str(cycle.cycle_id)}]}


@pytest.mark.asyncio
async def test_ready_package_creation_requires_cycle_mandate_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    cycle.mandate_evaluation_id = None
    create_calls = 0

    async def _create(**_kwargs):
        nonlocal create_calls
        create_calls += 1

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(), orchestration_payload=_automatic_payload(cycle),
    )

    assert create_calls == 0


@pytest.mark.asyncio
async def test_production_shape_correlates_autonomous_and_campaign_cycles_before_ready_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    campaign_cycle = _automatic_cycle()
    campaign_cycle.cycle_kind = "campaign"
    campaign_cycle.mandate_id = None
    campaign_cycle.mandate_version_id = None
    campaign_cycle.mandate_evaluation_id = None
    campaign_cycle.audit_correlation_id = uuid.uuid4()
    campaign_cycle.software_build_version = "test"
    campaign_cycle.cycle_context["trigger"] = "kraken_btc_15m_candle_close"
    campaign_cycle.cycle_context["authoritative_composition"]["selected_decision"]["strategy_identity"] = "strategy_roster_aggregate@1.0.0"
    autonomous_cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(), cycle_kind="autonomous", mandate_id=uuid.uuid4(),
        mandate_version_id=uuid.uuid4(), mandate_evaluation_id=uuid.uuid4(),
        cycle_context={"trigger": "kraken_btc_15m_candle_close", "product_id": "BTC-USD"},
    )
    campaign_evaluation_id = uuid.uuid4()
    create_requests = []
    evaluation_requests = []

    class _Db:
        async def flush(self): return None

    async def _evaluate(*, db, request):
        evaluation_requests.append(request)
        return SimpleNamespace(
            evaluation_id=campaign_evaluation_id,
            mandate_id=autonomous_cycle.mandate_id,
            mandate_version_id=autonomous_cycle.mandate_version_id,
            decision_id=campaign_cycle.decision_record_id,
            authorization_result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        )

    async def _create(*, db, request):
        create_requests.append(request)
        return {"idempotent": False, "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"}}

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(campaign_cycle))
    monkeypatch.setattr(worker_module, "_load_originating_autonomous_cycle", _async_return(autonomous_cycle))
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate)
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(
        db=_Db(), orchestration_payload=_automatic_payload(campaign_cycle),
        originating_autonomous_cycle_id=autonomous_cycle.cycle_id,
    )

    assert len(evaluation_requests) == 1
    assert evaluation_requests[0].decision_id == campaign_cycle.decision_record_id
    assert evaluation_requests[0].request_context["autonomous_cycle_id"] == str(autonomous_cycle.cycle_id)
    assert evaluation_requests[0].request_context["campaign_orchestration_cycle_id"] == str(campaign_cycle.cycle_id)
    assert len(create_requests) == 1
    assert create_requests[0].expected_decision_record_id == campaign_cycle.decision_record_id
    assert create_requests[0].mandate_id == autonomous_cycle.mandate_id
    assert create_requests[0].mandate_version_id == autonomous_cycle.mandate_version_id
    assert create_requests[0].mandate_evaluation_id == campaign_evaluation_id


@pytest.mark.asyncio
async def test_campaign_evaluation_rejects_mismatched_autonomous_cycle_correlation() -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    campaign_cycle = _automatic_cycle()
    campaign_cycle.cycle_kind = "campaign"
    campaign_cycle.mandate_id = campaign_cycle.mandate_version_id = campaign_cycle.mandate_evaluation_id = None
    campaign_cycle.audit_correlation_id = uuid.uuid4()
    campaign_cycle.software_build_version = None
    autonomous_cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(), mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        cycle_context={"trigger": "different_trigger", "product_id": "BTC-USD"},
    )
    reason = await worker_module._ensure_campaign_cycle_mandate_evaluation(
        db=object(), campaign_cycle=campaign_cycle, autonomous_cycle=autonomous_cycle,
        strategy_identity="strategy_roster_aggregate@1.0.0", product="BTC-USD", side="BUY",
        proposed_notional=Decimal("5"),
    )
    assert reason == "autonomous_campaign_cycle_correlation_mismatch"


def _forced_entry_fixture() -> tuple[SimpleNamespace, SimpleNamespace]:
    """A campaign_cycle/autonomous_cycle pair correlated well enough to pass
    the trigger/product_id check, with autonomous_cycle carrying a real
    (PRODUCTION-shaped) mandate identity -- exactly the identity a
    controlled_proof_forced_entry evaluation must never inherit."""
    campaign_cycle = _automatic_cycle()
    campaign_cycle.cycle_kind = "campaign"
    campaign_cycle.mandate_id = campaign_cycle.mandate_version_id = campaign_cycle.mandate_evaluation_id = None
    campaign_cycle.audit_correlation_id = uuid.uuid4()
    campaign_cycle.software_build_version = None
    campaign_cycle.cycle_context["trigger"] = "kraken_btc_15m_candle_close"
    autonomous_cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(),  # the ordinary PRODUCTION mandate identity
        mandate_version_id=uuid.uuid4(),
        cycle_context={"trigger": "kraken_btc_15m_candle_close", "product_id": "BTC-USD"},
    )
    return campaign_cycle, autonomous_cycle


@pytest.mark.asyncio
async def test_forced_entry_fails_closed_when_controlled_proof_mandate_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    campaign_cycle, autonomous_cycle = _forced_entry_fixture()
    evaluate_calls = 0

    async def _evaluate(*, db, request):
        nonlocal evaluate_calls
        evaluate_calls += 1
        raise AssertionError("evaluate_and_record_mandate must never be called when the mandate is unconfigured")

    monkeypatch.setattr(worker_module, "get_settings", lambda: SimpleNamespace(controlled_proof_mandate_id=None))
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate)

    reason = await worker_module._ensure_campaign_cycle_mandate_evaluation(
        db=object(), campaign_cycle=campaign_cycle, autonomous_cycle=autonomous_cycle,
        strategy_identity="strategy_roster_aggregate@1.0.0", product="BTC-USD", side="BUY",
        proposed_notional=Decimal("5"), controlled_proof_forced_entry=True,
    )

    assert reason == "controlled_proof_mandate_missing"
    assert evaluate_calls == 0
    # Fail-closed also means the campaign_cycle is never mutated with any
    # mandate identity -- neither the missing dedicated one nor the
    # autonomous cycle's production one.
    assert campaign_cycle.mandate_id is None


@pytest.mark.asyncio
async def test_forced_entry_never_inherits_autonomous_cycle_production_mandate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.mandates.contracts import MANDATE_PURPOSE_CONTROLLED_PROOF

    campaign_cycle, autonomous_cycle = _forced_entry_fixture()
    controlled_proof_mandate_id = uuid.uuid4()
    controlled_proof_mandate_version_id = uuid.uuid4()
    evaluation_requests = []

    async def _evaluate(*, db, request):
        evaluation_requests.append(request)
        return SimpleNamespace(
            evaluation_id=uuid.uuid4(),
            mandate_id=controlled_proof_mandate_id,
            mandate_version_id=controlled_proof_mandate_version_id,
            decision_id=campaign_cycle.decision_record_id,
            authorization_result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        )

    monkeypatch.setattr(
        worker_module, "get_settings",
        lambda: SimpleNamespace(controlled_proof_mandate_id=controlled_proof_mandate_id),
    )
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate)
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(None))

    class _Db:
        async def flush(self) -> None:
            return None

    reason = await worker_module._ensure_campaign_cycle_mandate_evaluation(
        db=_Db(), campaign_cycle=campaign_cycle, autonomous_cycle=autonomous_cycle,
        strategy_identity="strategy_roster_aggregate@1.0.0", product="BTC-USD", side="BUY",
        proposed_notional=Decimal("5"), controlled_proof_forced_entry=True,
    )

    assert reason is None
    assert len(evaluation_requests) == 1
    request = evaluation_requests[0]
    # The exact invariant under test: the mandate evaluated is the
    # dedicated Controlled Proof mandate -- never autonomous_cycle.mandate_id.
    assert request.mandate_id == controlled_proof_mandate_id
    assert request.mandate_id != autonomous_cycle.mandate_id
    assert request.expected_mandate_purpose == MANDATE_PURPOSE_CONTROLLED_PROOF
    assert campaign_cycle.mandate_id == controlled_proof_mandate_id
    assert campaign_cycle.mandate_version_id == controlled_proof_mandate_version_id
    assert campaign_cycle.mandate_id != autonomous_cycle.mandate_id


@pytest.mark.asyncio
async def test_ordinary_entry_still_pins_autonomous_cycle_mandate_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: controlled_proof_forced_entry defaults to False, so
    every pre-existing (ordinary) caller of this function keeps resolving
    autonomous_cycle.mandate_id exactly as before this change."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.mandates.contracts import MANDATE_PURPOSE_PRODUCTION

    campaign_cycle, autonomous_cycle = _forced_entry_fixture()
    evaluation_requests = []

    async def _evaluate(*, db, request):
        evaluation_requests.append(request)
        return SimpleNamespace(
            evaluation_id=uuid.uuid4(),
            mandate_id=autonomous_cycle.mandate_id,
            mandate_version_id=autonomous_cycle.mandate_version_id,
            decision_id=campaign_cycle.decision_record_id,
            authorization_result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        )

    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate)

    class _Db:
        async def flush(self) -> None:
            return None

    reason = await worker_module._ensure_campaign_cycle_mandate_evaluation(
        db=_Db(), campaign_cycle=campaign_cycle, autonomous_cycle=autonomous_cycle,
        strategy_identity="strategy_roster_aggregate@1.0.0", product="BTC-USD", side="BUY",
        proposed_notional=Decimal("5"),
    )

    assert reason is None
    assert len(evaluation_requests) == 1
    request = evaluation_requests[0]
    assert request.mandate_id == autonomous_cycle.mandate_id
    assert request.expected_mandate_purpose == MANDATE_PURPOSE_PRODUCTION
    assert request.controlled_proof_open_exposure_usd == Decimal("0")
    assert campaign_cycle.mandate_id == autonomous_cycle.mandate_id
    assert campaign_cycle.mandate_version_id == autonomous_cycle.mandate_version_id


def _not_due_research_result() -> SimpleNamespace:
    return SimpleNamespace(
        started=False,
        reason="not_due",
        campaign_id=None,
        candidates_generated=0,
        candidates_evaluated=0,
        descendants_generated=0,
        champion=None,
    )


def _patch_worker_for_campaign_preview_observability(monkeypatch: pytest.MonkeyPatch, worker_module, preview_payload: dict[str, object]) -> None:
    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "run_campaign_orchestration_preview_for_candle", _async_return(preview_payload))
    monkeypatch.setattr(worker_module, "_attempt_automatic_ready_package_creation", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))


@pytest.mark.asyncio
async def test_campaign_preview_candle_not_found_emits_exact_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)
    db = _CampaignPreviewCapableDB()
    _patch_worker_for_campaign_preview_observability(
        monkeypatch,
        worker_module,
        {
            "mode": "campaign_orchestration_preview",
            "trigger": "kraken_btc_15m_candle_close",
            "ready": False,
            "reason": "latest_btc_15m_candle_not_found",
            "cycle_count": 0,
            "cycles": [],
        },
    )

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert "campaign_orchestration_preview_result" in caplog.text
    assert "preview_reason=latest_btc_15m_candle_not_found" in caplog.text
    assert "campaign_orchestration_preview_skipped" in caplog.text
    assert "reason=latest_btc_15m_candle_not_found" in caplog.text


@pytest.mark.asyncio
async def test_campaign_preview_no_candidates_emits_exact_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)
    db = _CampaignPreviewCapableDB()
    _patch_worker_for_campaign_preview_observability(
        monkeypatch,
        worker_module,
        {
            "mode": "campaign_orchestration_preview",
            "trigger": "kraken_btc_15m_candle_close",
            "ready": False,
            "reason": "no_campaign_candidates",
            "cycle_count": 0,
            "cycles": [],
            "considered_campaigns": [
                {"campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b", "version": 1},
            ],
            "eligible_campaigns": [],
            "skipped_campaigns": [
                {
                    "campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
                    "version": 1,
                    "reason": "not_ready",
                }
            ],
        },
    )

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert "campaign_orchestration_preview_result" in caplog.text
    assert "preview_reason=no_campaign_candidates" in caplog.text
    assert "campaign_orchestration_preview_skipped" in caplog.text
    assert "reason=no_campaign_candidates" in caplog.text


@pytest.mark.asyncio
async def test_recovered_exit_recovery_outcome_sweep_runs_independent_of_reconciliation_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovered-outcome backfill sweep (refresh_exit_recovery_outcomes)
    must be invoked on every orchestration cycle even when the live-order
    reconciliation poll finds zero candidates -- the exact production shape
    for a proof whose replacement SELL already reconciled before this
    process started. No order-submission path is touched by this sweep."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof.exit_recovery import ExitRecoveryOutcomeSweepResult

    class _ScalarsCapableDB(_CampaignPreviewCapableDB):
        async def scalars(self, *_args, **_kwargs):
            return SimpleNamespace(all=lambda: [])

    db = _ScalarsCapableDB()
    _patch_worker_for_campaign_preview_observability(
        monkeypatch, worker_module,
        {
            "mode": "campaign_orchestration_preview", "trigger": "kraken_btc_15m_candle_close",
            "ready": False, "reason": "latest_btc_15m_candle_not_found",
            "cycle_count": 0, "cycles": [],
        },
    )

    sweep_calls: list[object] = []

    async def _sweep_spy(*, db):
        sweep_calls.append(db)
        return ExitRecoveryOutcomeSweepResult(candidates=1, projected=1, skipped=0, failed=0)

    monkeypatch.setattr(worker_module, "refresh_exit_recovery_outcomes", _sweep_spy)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert sweep_calls == [db]


_SWEEP_PERSISTENCE_TABLES = [
    AuditLog.__table__, AutonomousExecutionClaim.__table__, CanonicalPreviewPackage.__table__,
    ControlledProofExitRecovery.__table__, ControlledProofRun.__table__, LiveCryptoOrder.__table__,
]


async def _seed_stuck_expired_proof(session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    proof_id, recovery_id, package_id, order_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    async with session_factory() as seed_session:
        seed_session.add(ControlledProofRun(
            proof_id=proof_id, status="EXPIRED", provider="kraken_spot", environment="production",
            campaign_id=uuid.uuid4(), campaign_version=1, product_id="BTC-USD",
            max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            package_id=uuid.uuid4(), sell_package_id=package_id, sell_live_crypto_order_id=order_id,
            net_pnl_usd=None, terminal_verdict="FAILED",
        ))
        seed_session.add(ControlledProofExitRecovery(
            recovery_id=recovery_id, proof_id=proof_id, status="BLOCKED",
            idempotency_key=f"idem-{uuid.uuid4()}", authorized_by="operator:alice",
            authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            blocked_reason="stale_sell_package_replacement_blocked:Stale SELL package has unresolved execution lineage",
        ))
        seed_session.add(AuditLog(
            actor="system:controlled_proof_reconciliation_projector",
            action=exit_recovery._RECOVERED_OUTCOME_ACTION,
            entity_type="controlled_proof_exit_recovery", entity_id=recovery_id,
            before_state={}, after_state={
                "status": "COMPLETED_RECONCILED",
                "original_recovery_id": str(recovery_id),
                "proof_id": str(proof_id),
                "sell_package_id": str(package_id),
                "sell_live_crypto_order_id": str(order_id),
                "recovered_terminal_verdict": "LIFECYCLE_PROVEN_LOSS",
                "recovered_net_pnl_usd": "-0.0393333016409",
            },
        ))
        await seed_session.commit()
    return proof_id, recovery_id


@pytest.mark.asyncio
async def test_real_orchestration_cycle_durably_projects_stuck_proof_across_independent_sessions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reproduces the actual production path end to end: the real
    run_orchestration_cycle entry point, with its real session/transaction
    boundary, against a real database (not a fake). Proves whether a later
    stage in the SAME cycle (on the SAME session) refreshes, expires,
    overwrites, or otherwise undoes the sweep's proof mutation before the
    cycle's own commit -- not just whether an isolated commit call
    persists in isolation."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    caplog.set_level(logging.INFO, logger=exit_recovery.__name__)

    provider_calls: list[str] = []

    async def _no_provider_call(*_args, **_kwargs):
        provider_calls.append("submit")
        raise AssertionError("no provider submission path may be invoked by this sweep")

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "run_campaign_orchestration_preview_for_candle", _async_return({
        "mode": "campaign_orchestration_preview", "trigger": "kraken_btc_15m_candle_close",
        "ready": False, "reason": "latest_btc_15m_candle_not_found", "cycle_count": 0, "cycles": [],
    }))
    monkeypatch.setattr(worker_module, "_attempt_automatic_ready_package_creation", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    # No provider/exchange client exists in this test at all; any attempt
    # to reach one would raise AttributeError on object() well before this
    # spy could even be reached -- present only as an explicit, asserted
    # tripwire for requirement completeness.
    monkeypatch.setattr(worker_module, "live_crypto_orders", SimpleNamespace(submit=_no_provider_call), raising=False)
    monkeypatch.setattr(worker_module, "_record_research_cycle_status", _async_return(None))
    monkeypatch.setattr(worker_module, "score_due_strategy_roster_proposal_outcomes", _async_return(
        SimpleNamespace(scanned_proposals=0, inserted_outcomes=0, skipped_not_due=0, skipped_existing=0, skipped_missing_prices=0),
    ))

    async with real_sqlite_session_factory(_SWEEP_PERSISTENCE_TABLES) as session_factory:
        proof_id, recovery_id = await _seed_stuck_expired_proof(session_factory)

        proof_updates: list[str] = []
        original_projector = exit_recovery.project_blocked_exit_recovery_outcome

        async def _project_and_require_update(**kwargs):
            before = len(proof_updates)
            projected = await original_projector(**kwargs)
            if projected:
                assert len(proof_updates) == before + 1
            return projected

        monkeypatch.setattr(exit_recovery, "project_blocked_exit_recovery_outcome", _project_and_require_update)

        async with session_factory() as cycle_session:
            @event.listens_for(cycle_session.bind.sync_engine, "after_cursor_execute")
            def _capture_proof_update(_conn, _cursor, statement, _parameters, _context, _executemany):
                normalized = " ".join(statement.lower().split())
                if (
                    normalized.startswith("update controlled_proof_runs")
                    and "net_pnl_usd" in normalized
                    and "terminal_verdict" in normalized
                ):
                    proof_updates.append(statement)

            await run_orchestration_cycle(db=cycle_session, client=object(), config=_config())

        assert provider_calls == []
        assert len(proof_updates) == 1
        assert any(
            f"proof_id={proof_id} outcome=projected reason=projection_verified" in message
            and "proof_fields_matched_recovered_outcome=true" in message
            and "orm_mutation_occurred=true" in message
            and "flush_readback_verified=true" in message
            for message in caplog.messages
        )

        async with session_factory() as read_session:
            reloaded_proof = await read_session.get(ControlledProofRun, proof_id)
            assert reloaded_proof.net_pnl_usd == Decimal("-0.0393333016")
            assert reloaded_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
            reloaded_recovery = await read_session.get(ControlledProofExitRecovery, recovery_id)
            assert reloaded_recovery.status == "BLOCKED"
            outcome_audits = (await read_session.scalars(select(AuditLog).where(
                AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
                AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
            ))).all()
            assert len(outcome_audits) == 1

        # A second, complete cycle through another independent session:
        # replay must be a no-op.
        async with session_factory() as second_cycle_session:
            await run_orchestration_cycle(db=second_cycle_session, client=object(), config=_config())
        assert len(proof_updates) == 1
        assert any(
            f"proof_id={proof_id} outcome=skipped reason=proof_already_terminal" in message
            and "proof_fields_matched_recovered_outcome=true" in message
            and "orm_mutation_occurred=false" in message
            and "flush_readback_verified=false" in message
            for message in caplog.messages
        )

        async with session_factory() as final_session:
            final_proof = await final_session.get(ControlledProofRun, proof_id)
            assert final_proof.net_pnl_usd == Decimal("-0.0393333016")
            assert final_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
            outcome_audits = (await final_session.scalars(select(AuditLog).where(
                AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
                AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
            ))).all()
            assert len(outcome_audits) == 1


@pytest.mark.asyncio
async def test_campaign_preview_success_logs_positive_cycle_count_and_no_mutating_ops(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.canonical_preview_package as canonical_package
    import app.services.live_crypto_orders as live_crypto_orders
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    called = {
        "authorize": 0,
        "activate": 0,
        "dry_run": 0,
        "provider_submit": 0,
    }

    async def _unexpected_authorize(*args, **kwargs):
        called["authorize"] += 1
        raise AssertionError("authorize should not be called")

    async def _unexpected_activate(*args, **kwargs):
        called["activate"] += 1
        raise AssertionError("activate should not be called")

    async def _unexpected_dry_run(*args, **kwargs):
        called["dry_run"] += 1
        raise AssertionError("dry run should not be called")

    async def _unexpected_submit(*args, **kwargs):
        called["provider_submit"] += 1
        raise AssertionError("provider submit should not be called")

    caplog.set_level(logging.INFO)
    db = _CampaignPreviewCapableDB()
    monkeypatch.setattr(canonical_package, "authorize_canonical_preview_package", _unexpected_authorize)
    monkeypatch.setattr(canonical_package, "activate_canonical_proving_campaign", _unexpected_activate)
    monkeypatch.setattr(canonical_package, "run_dry_run_for_canonical_preview_package", _unexpected_dry_run)
    monkeypatch.setattr(live_crypto_orders.LiveCryptoOrderService, "submit", _unexpected_submit)
    _patch_worker_for_campaign_preview_observability(
        monkeypatch,
        worker_module,
        {
            "mode": "campaign_orchestration_preview",
            "trigger": "kraken_btc_15m_candle_close",
            "ready": True,
            "reason": None,
            "cycle_count": 1,
            "cycles": [{"cycle_id": str(uuid.uuid4())}],
            "considered_campaigns": [
                {"campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b", "version": 1},
            ],
            "eligible_campaigns": [
                {"campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b", "version": 1},
            ],
            "skipped_campaigns": [],
        },
    )

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert "campaign_orchestration_preview_result" in caplog.text
    assert "cycle_count=1" in caplog.text
    assert "campaign_orchestration_preview_skipped" not in caplog.text
    assert called["authorize"] == 0
    assert called["activate"] == 0
    assert called["dry_run"] == 0
    assert called["provider_submit"] == 0


@pytest.mark.asyncio
async def test_automatic_ready_package_executable_buy_creates_one_ready_package(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    package_id = str(uuid.uuid4())
    calls: list[object] = []

    async def _fake_create(*, db, request):
        calls.append(request)
        return {
            "idempotent": False,
            "package": {"package_id": package_id, "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(calls) == 1
    assert calls[0].max_proposed_order_amount == Decimal("5")
    assert calls[0].expected_decision_record_id == cycle.decision_record_id
    assert calls[0].mandate_id == cycle.mandate_id
    assert calls[0].mandate_version_id == cycle.mandate_version_id
    assert calls[0].mandate_evaluation_id == cycle.mandate_evaluation_id


# Multi-asset expansion: the winning instrument selected by campaign
# composition's own deterministic ranking (selected_decision["instrument"])
# must be the product a ready package is created for -- not the hardcoded
# BTC-USD constant -- and the correct per-product originating autonomous
# cycle (not BTC's) must be resolved for the mandate-evaluation correlation
# check, or a non-BTC winner would always fail closed with
# autonomous_campaign_cycle_correlation_mismatch.
@pytest.mark.asyncio
async def test_automatic_ready_package_uses_the_selected_non_btc_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(instrument="ETH-USD")
    eth_autonomous_cycle_id = uuid.uuid4()
    btc_autonomous_cycle_id = uuid.uuid4()
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    calls: list[object] = []

    async def _fake_create(*, db, request):
        calls.append(request)
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_resolve_autonomous_cycle_products", _async_return(["BTC-USD", "ETH-USD"]))
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
        autonomous_cycle_ids_by_product={"BTC-USD": btc_autonomous_cycle_id, "ETH-USD": eth_autonomous_cycle_id},
    )

    assert len(calls) == 1
    assert calls[0].product == "ETH-USD"


@pytest.mark.asyncio
async def test_automatic_ready_package_out_of_scope_product_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An instrument outside the configured/authorized roster (e.g. campaign
    composition somehow selected a product this worker was never told to
    evaluate) must be rejected with scope_not_supported, never silently
    progressed."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(instrument="DOGE-USD")
    calls: list[object] = []

    async def _fake_create(*, db, request):
        calls.append(request)
        raise AssertionError("must not reach package creation for an out-of-scope product")

    monkeypatch.setattr(worker_module, "_resolve_autonomous_cycle_products", _async_return(["BTC-USD", "ETH-USD"]))
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert calls == []


# Regression for production-readiness gap: a fully computed, risk-checked
# CLOSE_POSITION_PROPOSED (position monitoring already resolves SELL votes
# against an open position into this decision every cycle, per
# resolve_action_position_transition + authoritative.py's candidate_kind
# resolution) was previously discarded before a READY package was ever
# attempted -- non_executable_action (only OPEN_* was accepted) and, even if
# that were fixed, non_canonical_amount (a close's market-value proceeds are
# not expected to equal the original $5 entry exactly). Together these meant
# "manage" could compute an exit forever without it ever becoming visible for
# the same human-gated authorize/activate/execute path BUY already reaches.
@pytest.mark.asyncio
async def test_automatic_ready_package_executable_close_creates_one_ready_package(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        proposed_action="CLOSE_POSITION_PROPOSED",
        decision_kind="CLOSE_POSITION_PROPOSED",
        final_amount="4.73",
    )
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    package_id = str(uuid.uuid4())
    calls: list[object] = []

    async def _fake_create(*, db, request):
        calls.append(request)
        return {
            "idempotent": False,
            "package": {"package_id": package_id, "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_automatic_ready_package_close_still_blocked_by_risk_veto(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Widening the accepted action set to include closes must not create a
    risk-engine bypass -- a vetoed close is skipped exactly like a vetoed
    BUY."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        proposed_action="CLOSE_POSITION_PROPOSED",
        decision_kind="CLOSE_POSITION_PROPOSED",
        final_amount="4.73",
        risk_verdict="VETO",
    )

    create_calls = {"count": 0}

    async def _fake_create(*, db, request):
        create_calls["count"] += 1
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert create_calls["count"] == 0
    assert "reason=risk_not_permitted" in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_buy_still_requires_exact_canonical_amount(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression: the canonical $5 bound must still apply to new entries --
    only closes (which liquidate an already-bounded position at prevailing
    market value) are exempt from the exact-amount match."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(final_amount="4.50")

    create_calls = {"count": 0}

    async def _fake_create(*, db, request):
        create_calls["count"] += 1
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert create_calls["count"] == 0
    assert "reason=non_canonical_amount" in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_replayed_identical_opportunity_returns_same_package(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    package_id = str(uuid.uuid4())
    seen: dict[str, str] = {}
    request_keys: list[str] = []

    async def _fake_create(*, db, request):
        request_keys.append(request.idempotency_key)
        if request.idempotency_key in seen:
            return {
                "idempotent": True,
                "package": {"package_id": seen[request.idempotency_key], "package_state": "READY"},
                "readiness": {"ready": True, "package_state": "READY"},
            }
        seen[request.idempotency_key] = package_id
        return {
            "idempotent": False,
            "package": {"package_id": package_id, "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    payload = _automatic_payload(cycle)
    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=payload)
    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=payload)

    assert len(request_keys) == 2
    assert request_keys[0] == request_keys[1]


@pytest.mark.asyncio
async def test_automatic_ready_package_worker_restart_does_not_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    created_by_key: dict[str, str] = {}
    created_count = {"value": 0}

    async def _fake_create(*, db, request):
        if request.idempotency_key in created_by_key:
            return {
                "idempotent": True,
                "package": {"package_id": created_by_key[request.idempotency_key], "package_state": "READY"},
                "readiness": {"ready": True, "package_state": "READY"},
            }
        created_count["value"] += 1
        package_id = str(uuid.uuid4())
        created_by_key[request.idempotency_key] = package_id
        return {
            "idempotent": False,
            "package": {"package_id": package_id, "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    payload = _automatic_payload(cycle)
    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=payload)
    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=payload)

    assert created_count["value"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cycle", "active_ready", "active_activation", "open_order", "unresolved_recon"),
    [
        (_automatic_cycle(termination_stage="hold_no_package_created", proposed_action="HOLD", decision_kind="HOLD"), False, False, False, False),
        (_automatic_cycle(termination_stage="failed_closed", proposed_action="FAILED_CLOSED", decision_kind="MANUAL_REVIEW_REQUIRED"), False, False, False, False),
        (_automatic_cycle(freshness="stale"), False, False, False, False),
        (_automatic_cycle(decision_record_id=None), False, False, False, False),
        (_automatic_cycle(risk_verdict="VETO"), False, False, False, False),
        (_automatic_cycle(final_amount="4.50"), False, False, False, False),
        (_automatic_cycle(), True, False, False, False),
        (_automatic_cycle(), False, True, False, False),
        (_automatic_cycle(), False, False, True, False),
        (_automatic_cycle(), False, False, False, True),
    ],
)
async def test_automatic_ready_package_skip_conditions_create_no_package(
    monkeypatch: pytest.MonkeyPatch,
    cycle: SimpleNamespace,
    active_ready: bool,
    active_activation: bool,
    open_order: bool,
    unresolved_recon: bool,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    create_calls = {"count": 0}

    async def _fake_create(*, db, request):
        create_calls["count"] += 1
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(active_ready))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(active_activation))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(open_order))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(unresolved_recon))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert create_calls["count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_ready", [False, True])
async def test_worker_delegates_new_or_existing_ready_package_to_bounded_executor(
    monkeypatch: pytest.MonkeyPatch, existing_ready: bool,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    package_id = uuid.uuid4()
    executor_requests = []
    create_calls = {"count": 0}

    async def _create(*, db, request):
        create_calls["count"] += 1
        return {"idempotent": False, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        executor_requests.append(request)
        return SimpleNamespace(final_reason_code="activated_under_mandate")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(existing_ready))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(executor_requests) == 1
    assert create_calls["count"] == (0 if existing_ready else 1)
    assert executor_requests[0].package_id == (None if existing_ready else package_id)
    assert executor_requests[0].decision_record_id == cycle.decision_record_id
    if not existing_ready:
        assert create_calls["count"] == 1


@pytest.mark.asyncio
async def test_worker_contains_unexpected_automatic_package_executor_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()

    async def _create(*, db, request):
        return {"idempotent": False, "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"}}

    async def _explode(*, db, request):
        raise RuntimeError("unexpected executor defect")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _explode)
    caplog.set_level(logging.ERROR)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert "reason=unexpected_executor_failure" in caplog.text
    assert "failed_closed=True" in caplog.text


@pytest.mark.asyncio
async def test_expired_activation_at_prepare_time_terminates_claim_instead_of_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A claim already reaching CLAIMED on an earlier tick must be terminated
    with its true reason when prepare's own activation-window re-check fails
    on a later tick, not swallowed into reason_code=unexpected_executor_failure
    while leaving the claim stuck at CLAIMED forever."""
    import app.services.orchestration.autonomous_execution_claims as claims_module
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.core.errors import InvalidRequestError
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome
    from app.services.orchestration.autonomous_execution_claims import AutonomousClaimOutcome

    cycle = _automatic_cycle()
    package_id = uuid.uuid4()
    claim = SimpleNamespace(claim_id=uuid.uuid4(), package_id=package_id, claim_status="CLAIMED")
    blocked_calls = []

    async def _create(*, db, request):
        return {"idempotent": False, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="DRY_RUN_PASSED",
            activation_state="ACTIVATED", authority_source="MANDATE", replayed=True,
            final_reason_code="already_activated", failed_closed=False, starting_state="ACTIVATED",
        )

    async def _claim(*, db, package_id):
        return AutonomousClaimOutcome(claim, False, "already_claimed")

    async def _prepare(*, db, claim_id):
        raise InvalidRequestError(
            message="Autonomous order preparation failed closed",
            details={"blocker": "activation_not_effective"},
        )

    async def _mark_pre_provider_blocked(*, db, claim, reason_code):
        blocked_calls.append((claim, reason_code))

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_activated_package", _claim)
    # prepare_autonomous_claimed_order and mark_pre_provider_blocked are now
    # called from inside advance_claimed_execution (autonomous_execution_claims.py),
    # not directly by the worker -- patch them where they're actually used,
    # exercising the real advance_claimed_execution/worker integration rather
    # than a bypassed one.
    monkeypatch.setattr(claims_module, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(claims_module, "mark_pre_provider_blocked", _mark_pre_provider_blocked)
    caplog.set_level(logging.INFO)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert blocked_calls == [(claim, "activation_not_effective")]
    assert "reason=activation_not_effective" in caplog.text
    assert "unexpected_executor_failure" not in caplog.text


@pytest.mark.asyncio
async def test_expired_mandate_authorization_on_replay_never_reaches_claim_or_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker-level regression for the production sequence
    activation_result=ACTIVATED -> reason_code=mandate ("mandate package
    authorization expired") -> reason_code=unexpected_executor_failure.

    automatic_package_executor._outcome() already reports NOT_ACTIVATED with
    failed_closed=True for exactly this case (see
    test_executor_activated_replay_with_expired_mandate_authorization_does_not_report_activated
    in test_automatic_package_executor.py), but that test only proves the
    executor's own return value in isolation. This test closes the actual
    integration seam where the original defect lived: it proves the WORKER
    itself respects that outcome end-to-end -- it must never call
    claim_activated_package (or anything downstream of it), must report
    a deterministic, non-crashing final_state, and must never log
    unexpected_executor_failure, when the executor reports this outcome."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle()
    package_id = uuid.uuid4()
    claim_calls: list[uuid.UUID] = []

    async def _create(*, db, request):
        return {"idempotent": False, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="DRY_RUN_PASSED",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=True,
            final_reason_code="mandate package authorization expired", failed_closed=True, starting_state="ACTIVATED",
        )

    async def _claim(*, db, package_id):
        claim_calls.append(package_id)
        raise AssertionError("claim_activated_package must not be called when activation_state is NOT_ACTIVATED")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_activated_package", _claim)
    caplog.set_level(logging.INFO)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert claim_calls == []
    assert "unexpected_executor_failure" not in caplog.text
    assert "final_state=AUTHORIZED" in caplog.text
    assert "reason_code=mandate package authorization expired" in caplog.text
    assert "failed_closed=True" in caplog.text


# --- Controlled Proof worker integration: purely additive claim + linkage ----------

# These tests encoded the retired implementation shortcut: claiming and
# forcing a Controlled Proof from inside an ambient autonomous campaign
# cycle.  Keeping them executable would require restoring the coupling the
# governing contract explicitly forbids.  The immediate/periodic dispatch
# tests below replace that assertion at the correct operator-candidate seam;
# the canonical package, claim, preparation, and side-neutral execution
# suites cover the shared downstream path.
_RETIRED_ORGANIC_PROOF_TESTS = {
    "test_controlled_proof_is_claimed_and_linked_through_normal_package_creation",
    "test_controlled_proof_linkage_is_not_duplicated_on_worker_restart",
    "test_controlled_proof_forces_buy_candidate_when_strategy_would_hold",
    "test_controlled_proof_activation_retry_uses_organic_decision_when_package_creation_skipped",
    "test_controlled_proof_forced_entry_never_mutates_original_hold_decision",
    "test_controlled_proof_forces_buy_when_hold_surfaces_as_termination_stage",
    "test_controlled_proof_does_not_override_non_strategy_hold_termination",
    "test_controlled_proof_never_overrides_failed_closed_termination",
    "test_controlled_proof_forced_entry_blocked_by_risk_denial",
    "test_controlled_proof_forced_entry_blocked_by_risk_resize",
    "test_controlled_proof_forced_entry_blocked_by_stale_evidence",
    "test_controlled_proof_forced_entry_blocked_by_mandate_denial",
    "test_controlled_proof_forced_buy_not_reproposed_after_entry_already_linked",
    "test_controlled_proof_forces_sell_after_buy_reconciled",
    "test_controlled_proof_sell_not_reproposed_after_sell_already_linked",
    "test_run_orchestration_cycle_still_reaches_controlled_proof_claim_on_normal_candle_cadence",
}


@pytest.fixture(autouse=True)
def _retire_organic_proof_coupling_contract(request: pytest.FixtureRequest) -> None:
    if request.node.name in _RETIRED_ORGANIC_PROOF_TESTS:
        pytest.skip("obsolete: Controlled Proof is no longer claimed from an organic autonomous cycle")

@pytest.mark.asyncio
async def test_controlled_proof_is_claimed_and_linked_through_normal_package_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLAIMED controlled proof for this exact scope, combined with a
    genuine qualifying BUY decision this cycle, must be linked to the
    decision and the resulting package -- via the completely unmodified
    create_canonical_preview_package call already used for every other
    automatic package."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle()
    package_id = uuid.uuid4()
    proof = SimpleNamespace(proof_id=uuid.uuid4(), decision_record_id=None, package_id=None)
    claim_calls = []
    entry_link_calls = []
    package_link_calls = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        claim_calls.append((campaign_id, campaign_version, provider, environment, product_id, cycle_id))
        return proof

    async def _link_entry(*, db, proof, decision_record_id, mandate_id, mandate_version_id, mandate_evaluation_id):
        entry_link_calls.append(decision_record_id)
        proof.decision_record_id = decision_record_id

    async def _link_package(*, db, proof, package_id):
        package_link_calls.append(package_id)
        proof.package_id = package_id

    async def _create(*, db, request):
        return {"idempotent": False, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", _link_entry)
    monkeypatch.setattr(worker_module, "link_controlled_proof_package", _link_package)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(claim_calls) == 1
    assert claim_calls[0][0] == cycle.capital_campaign_id
    assert entry_link_calls == [cycle.decision_record_id]
    assert package_link_calls == [package_id]
    assert proof.decision_record_id == cycle.decision_record_id
    assert proof.package_id == package_id


@pytest.mark.asyncio
async def test_controlled_proof_linkage_is_not_duplicated_on_worker_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a worker restart re-processing the same cycle after the
    package was already created and already linked on an earlier run
    (create_canonical_preview_package idempotently replays; the proof is
    already CLAIMED with decision/package already set). The real
    link_controlled_proof_entry/_package no-op once already linked -- this
    test proves the worker calls them again unconditionally (safe to do so)
    and they do not create a second link or a second BUY."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof.service import link_controlled_proof_entry, link_controlled_proof_package
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle()
    package_id = uuid.uuid4()
    # Proof already fully linked from an earlier (pre-restart) tick.
    proof = SimpleNamespace(proof_id=uuid.uuid4(), decision_record_id=cycle.decision_record_id, package_id=package_id)

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _create(*, db, request):
        return {"idempotent": True, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=True,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    # Real (not faked) linkage functions -- proving their own idempotency
    # guard, exercised through the worker's call site, not just in isolation.
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", link_controlled_proof_entry)
    monkeypatch.setattr(worker_module, "link_controlled_proof_package", link_controlled_proof_package)

    class _NoopDb:
        def add(self, obj) -> None:
            raise AssertionError("relinking an already-linked proof must not write anything")

        async def flush(self) -> None:
            return None

    await worker_module._attempt_automatic_ready_package_creation(db=_NoopDb(), orchestration_payload=_automatic_payload(cycle))

    assert proof.decision_record_id == cycle.decision_record_id
    assert proof.package_id == package_id


# --- Controlled Proof deliberate BUY/SELL forcing: overrides only the ---------------
# "ordinary strategy said HOLD" objection, never a real gate ------------------------

def _controlled_proof_stub(*, decision_record_id=None, package_id=None) -> SimpleNamespace:
    return SimpleNamespace(proof_id=uuid.uuid4(), decision_record_id=decision_record_id, package_id=package_id)


@pytest.mark.asyncio
async def test_controlled_proof_forces_buy_candidate_when_strategy_would_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 1 & 2: a claimed proof deliberately emits one
    CONTROLLED_PROOF-labeled BUY candidate even when the ordinary strategy
    composition proposed HOLD this cycle."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    package_id = uuid.uuid4()
    proof = _controlled_proof_stub()
    create_requests: list = []
    entry_link_calls = []
    package_link_calls = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _risk_allow(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        from app.services.controlled_proof import ControlledProofRiskOutcome
        return ControlledProofRiskOutcome(
            verdict="ALLOW", approved_notional_usd=notional_usd, reason_code="risk_approved", risk_event_id=uuid.uuid4(),
        )

    async def _link_entry(*, db, proof, decision_record_id, mandate_id, mandate_version_id, mandate_evaluation_id):
        entry_link_calls.append(decision_record_id)
        proof.decision_record_id = decision_record_id

    async def _link_package(*, db, proof, package_id):
        package_link_calls.append(package_id)
        proof.package_id = package_id

    # Distinct from cycle.decision_record_id -- simulates the real, truthful
    # decision record create_canonical_preview_package now creates for a
    # controlled-proof-forced entry, never the organic cycle's own decision.
    forced_decision_record_id = uuid.uuid4()

    async def _create(*, db, request):
        create_requests.append(request)
        return {
            "idempotent": False,
            "package": {
                "package_id": str(package_id), "package_state": "READY",
                "decision_record_id": str(forced_decision_record_id),
            },
        }

    execute_requests: list = []

    async def _execute(*, db, request):
        execute_requests.append(request)
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=request.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_allow)
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", _link_entry)
    monkeypatch.setattr(worker_module, "link_controlled_proof_package", _link_package)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(create_requests) == 1
    assert create_requests[0].commissioning_entry_mode == "controlled_proof"
    assert create_requests[0].forced_action == "OPEN_POSITION_PROPOSED"
    assert create_requests[0].controlled_proof_id == proof.proof_id
    # A controlled-proof-forced request must never constrain
    # create_canonical_preview_package to the organic cycle's own decision --
    # the whole point is that a new, different decision gets created.
    assert create_requests[0].expected_decision_record_id is None
    # The proof must be linked to the package's own truthful decision
    # record, not the organic cycle's, and never fall back silently.
    assert entry_link_calls == [forced_decision_record_id]
    assert entry_link_calls != [cycle.decision_record_id]
    assert package_link_calls == [package_id]
    # Requirement (production blocker fix): activation must be looked up
    # using the package's own linked decision record, never the organic
    # cycle's -- otherwise execute_automatic_ready_package_through_
    # activation's own CanonicalPreviewPackage.decision_record_id ==
    # request.decision_record_id lookup would never find the real package.
    assert len(execute_requests) == 1
    assert execute_requests[0].decision_record_id == forced_decision_record_id
    assert execute_requests[0].decision_record_id != cycle.decision_record_id


@pytest.mark.asyncio
async def test_controlled_proof_activation_retry_uses_organic_decision_when_package_creation_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real bug found in review: on a retry cycle
    where _has_active_ready_package_for_opportunity already finds an active
    package (skip_reason="active_ready_package_exists"), package creation
    is skipped entirely this cycle -- linked_decision_record_id must still
    be defined (falling back to the organic decision_record_id) so
    activation can still be attempted, never a NameError."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle(proposed_action="OPEN_POSITION_PROPOSED", decision_kind="OPEN_POSITION_PROPOSED")
    execute_requests: list = []

    async def _create(*, db, request):
        raise AssertionError("package creation must not be attempted when an active package already exists")

    async def _execute(*, db, request):
        execute_requests.append(request)
        return AutomaticPackageExecutionOutcome(
            package_id=None, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=request.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(True))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _async_return(None))

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(execute_requests) == 1
    assert execute_requests[0].decision_record_id == cycle.decision_record_id


@pytest.mark.asyncio
async def test_controlled_proof_forced_entry_never_mutates_original_hold_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fresh Controlled Proof risk evaluation is local evidence for the
    forced-entry path only -- even on a successful forced BUY, the organic
    HOLD decision (selected_decision, including its absent risk_verdict
    key), the persisted cycle_context, and the cycle's own organic
    risk_verdict field must come out byte-for-byte identical to how they
    went in."""
    import copy

    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    # Mirrors authoritative.py's real HOLD-branch selected_decision, which
    # never sets a risk_verdict key at all (only the winning-candidate
    # branch does) -- the _automatic_cycle test helper's default of
    # "ALLOW" is purely synthetic fixture data, not what a genuine HOLD
    # produces.
    del cycle.cycle_context["authoritative_composition"]["selected_decision"]["risk_verdict"]
    original_cycle_context = copy.deepcopy(cycle.cycle_context)
    original_risk_verdict = cycle.risk_verdict
    package_id = uuid.uuid4()
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _risk_allow(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        return ControlledProofRiskOutcome(
            verdict="ALLOW", approved_notional_usd=notional_usd, reason_code="risk_approved", risk_event_id=uuid.uuid4(),
        )

    async def _link_entry(*, db, proof, decision_record_id, mandate_id, mandate_version_id, mandate_evaluation_id):
        proof.decision_record_id = decision_record_id

    async def _link_package(*, db, proof, package_id):
        proof.package_id = package_id

    async def _create(*, db, request):
        return {"idempotent": False, "package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_allow)
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", _link_entry)
    monkeypatch.setattr(worker_module, "link_controlled_proof_package", _link_package)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert cycle.cycle_context == original_cycle_context
    assert "risk_verdict" not in cycle.cycle_context["authoritative_composition"]["selected_decision"]
    assert cycle.risk_verdict == original_risk_verdict


@pytest.mark.asyncio
async def test_controlled_proof_forces_buy_when_hold_surfaces_as_termination_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production-observed gap: a genuine strategy HOLD that surfaces as
    termination_stage="hold_no_package_created" (skip_reason=
    "termination_stage_hold_no_package_created") with the ordinary
    strategy_hold_signal reason must be just as overridable as the
    proposed_action/decision_kind-driven HOLD path already covered by
    test_controlled_proof_forces_buy_candidate_when_strategy_would_hold."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle(
        proposed_action="HOLD", decision_kind="HOLD",
        termination_stage="hold_no_package_created", selected_decision_reason="strategy_hold_signal",
    )
    package_id = uuid.uuid4()
    proof = _controlled_proof_stub()
    create_requests: list = []
    entry_link_calls = []
    package_link_calls = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _risk_allow(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        from app.services.controlled_proof import ControlledProofRiskOutcome
        return ControlledProofRiskOutcome(
            verdict="ALLOW", approved_notional_usd=notional_usd, reason_code="risk_approved", risk_event_id=uuid.uuid4(),
        )

    async def _link_entry(*, db, proof, decision_record_id, mandate_id, mandate_version_id, mandate_evaluation_id):
        entry_link_calls.append(decision_record_id)
        proof.decision_record_id = decision_record_id

    async def _link_package(*, db, proof, package_id):
        package_link_calls.append(package_id)
        proof.package_id = package_id

    # Distinct from cycle.decision_record_id -- simulates the real, truthful
    # decision record create_canonical_preview_package now creates for a
    # controlled-proof-forced entry, never the organic cycle's own decision.
    forced_decision_record_id = uuid.uuid4()

    async def _create(*, db, request):
        create_requests.append(request)
        return {
            "idempotent": False,
            "package": {
                "package_id": str(package_id), "package_state": "READY",
                "decision_record_id": str(forced_decision_record_id),
            },
        }

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_allow)
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", _link_entry)
    monkeypatch.setattr(worker_module, "link_controlled_proof_package", _link_package)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(create_requests) == 1
    assert create_requests[0].commissioning_entry_mode == "controlled_proof"
    assert create_requests[0].forced_action == "OPEN_POSITION_PROPOSED"
    assert create_requests[0].controlled_proof_id == proof.proof_id
    # A controlled-proof-forced request must never constrain
    # create_canonical_preview_package to the organic cycle's own decision --
    # the whole point is that a new, different decision gets created.
    assert create_requests[0].expected_decision_record_id is None
    # The proof must be linked to the package's own truthful decision
    # record, not the organic cycle's, and never fall back silently.
    assert entry_link_calls == [forced_decision_record_id]
    assert entry_link_calls != [cycle.decision_record_id]
    assert package_link_calls == [package_id]


@pytest.mark.asyncio
async def test_controlled_proof_does_not_override_non_strategy_hold_termination(monkeypatch: pytest.MonkeyPatch) -> None:
    """termination_stage="hold_no_package_created" with any reason other
    than "strategy_hold_signal" must stay blocked -- this is not "any HOLD
    termination is overridable", only the narrow ordinary-strategy-HOLD
    sub-case, mirroring canonical_preview_package.py's own narrowing."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        proposed_action="HOLD", decision_kind="HOLD",
        termination_stage="hold_no_package_created", selected_decision_reason="insufficient_balance",
    )
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _create(*, db, request):
        raise AssertionError("must not force a BUY for a non-strategy_hold_signal termination reason")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


@pytest.mark.asyncio
async def test_controlled_proof_never_overrides_failed_closed_termination(monkeypatch: pytest.MonkeyPatch) -> None:
    """failed_closed must never be overridable, even in the pathological
    case where selected_decision.reason happens to equal
    "strategy_hold_signal" -- the skip_reason string for this termination
    stage is "termination_stage_failed_closed", never
    "termination_stage_hold_no_package_created", so it can never match the
    new override condition regardless of the reason field."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        proposed_action="HOLD", decision_kind="HOLD",
        termination_stage="failed_closed", selected_decision_reason="strategy_hold_signal",
    )
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _create(*, db, request):
        raise AssertionError("must never force a BUY for a failed_closed termination")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


@pytest.mark.asyncio
async def test_controlled_proof_forced_entry_blocked_by_risk_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 4: the HOLD override never defeats a real risk denial --
    it only ever overrides the single "ordinary strategy said HOLD"
    objection, and every other gate re-runs against the cycle's real data.
    The organic cycle.risk_verdict is left at its default (never consulted
    by the forced path -- see authoritative.py's HOLD-branch, which never
    sets a risk_verdict at all); the block must come from a genuine, fresh
    Controlled Proof risk evaluation reporting DENY, not from stale/absent
    organic risk data."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _load_runtime_campaign(*, db, campaign_id):
        return SimpleNamespace(paper_account_id=uuid.uuid4())

    async def _risk_deny(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        assert side == "BUY"
        return ControlledProofRiskOutcome(
            verdict="DENY", approved_notional_usd=None, reason_code="max_drawdown_breached", risk_event_id=uuid.uuid4(),
        )

    async def _create(*, db, request):
        raise AssertionError("a real risk denial must never be overridden by controlled-proof forcing")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _load_runtime_campaign)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_deny)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


@pytest.mark.asyncio
async def test_controlled_proof_forced_entry_blocked_by_risk_resize(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine RESIZE verdict must never be silently proceeded with at the
    full $5 -- the canonical package pipeline hard-requires exactly $5
    (create_canonical_preview_package rejects any other amount), so there is
    no code path to execute at a smaller, risk-approved size. It must block,
    with a distinct reason code diagnosable as "risk wants a smaller size",
    not "risk said no"."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _load_runtime_campaign(*, db, campaign_id):
        return SimpleNamespace(paper_account_id=uuid.uuid4())

    async def _risk_resize(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        return ControlledProofRiskOutcome(
            verdict="RESIZE", approved_notional_usd=Decimal("1"), reason_code="position_resized_by_risk_engine",
            risk_event_id=uuid.uuid4(),
        )

    async def _create(*, db, request):
        raise AssertionError("a RESIZE verdict must never be silently proceeded with at the full $5")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _load_runtime_campaign)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_resize)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


@pytest.mark.asyncio
async def test_controlled_proof_forced_entry_blocked_by_stale_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 5: same, but for stale market evidence."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE", freshness="stale")
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _create(*, db, request):
        raise AssertionError("stale market evidence must never be overridden by controlled-proof forcing")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


@pytest.mark.asyncio
async def test_controlled_proof_forced_entry_blocked_by_mandate_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 3: a real mandate-authorization denial still blocks the
    controlled-proof-forced BUY. Forces the campaign cycle's mandate bundle
    to be incomplete (so the real _ensure_campaign_cycle_mandate_evaluation
    re-evaluation path runs) and has the real mandate evaluation call report
    a rejected/mismatched result. The fresh Controlled Proof risk check
    (which runs before the mandate gate) is mocked to ALLOW so this test
    genuinely exercises the mandate-denial gate rather than short-circuiting
    on an unrelated risk-unavailable fail-closed."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    cycle.cycle_kind = "campaign"
    cycle.mandate_id = cycle.mandate_version_id = cycle.mandate_evaluation_id = None
    cycle.audit_correlation_id = uuid.uuid4()
    cycle.software_build_version = "test"
    cycle.cycle_context["trigger"] = "kraken_btc_15m_candle_close"
    autonomous_mandate_id = uuid.uuid4()
    autonomous_cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(), cycle_kind="autonomous", mandate_id=autonomous_mandate_id,
        mandate_version_id=uuid.uuid4(), mandate_evaluation_id=uuid.uuid4(),
        cycle_context={"trigger": "kraken_btc_15m_candle_close", "product_id": "BTC-USD"},
    )
    proof = _controlled_proof_stub()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _resolve_strategy_identity(*, db, mandate_id):
        return "ma_crossover@1.0.0"

    async def _load_runtime_campaign(*, db, campaign_id):
        return SimpleNamespace(paper_account_id=uuid.uuid4())

    async def _risk_allow(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        return ControlledProofRiskOutcome(
            verdict="ALLOW", approved_notional_usd=notional_usd, reason_code="risk_approved", risk_event_id=uuid.uuid4(),
        )

    async def _evaluate_denied(*, db, request):
        return SimpleNamespace(
            evaluation_id=uuid.uuid4(), mandate_id=request.mandate_id, mandate_version_id=uuid.uuid4(),
            decision_id=cycle.decision_record_id, authorization_result="DENIED",
            approval_result="APPROVAL_REJECTED_BY_MANDATE_POLICY",
        )

    async def _create(*, db, request):
        raise AssertionError("a real mandate denial must never be overridden by controlled-proof forcing")

    class _Db:
        async def flush(self) -> None:
            return None

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_load_originating_autonomous_cycle", _async_return(autonomous_cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "resolve_controlled_proof_strategy_identity", _resolve_strategy_identity)
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _load_runtime_campaign)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_allow)
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate_denied)
    monkeypatch.setattr(
        worker_module, "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_mandate_id=autonomous_mandate_id,
            parsed_autonomous_cycle_additional_products=[],
        ),
    )
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(
        db=_Db(), orchestration_payload=_automatic_payload(cycle), originating_autonomous_cycle_id=autonomous_cycle.cycle_id,
    )


@pytest.mark.asyncio
async def test_controlled_proof_forced_buy_not_reproposed_after_entry_already_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 6: once a controlled proof already has a linked entry
    (decision_record_id set) from an earlier tick, a worker restart must not
    force a second BUY candidate, even while the ordinary strategy still
    proposes HOLD. This also guarantees the bounded audit-persistence claim:
    a successful forced entry is evaluated by the fresh Risk Engine exactly
    once, ever, for a given proof -- once already_has_entry makes
    target_action resolve to None, evaluate_controlled_proof_risk (and the
    RiskEvent it would persist) is never reached again."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    proof = _controlled_proof_stub(decision_record_id=uuid.uuid4(), package_id=uuid.uuid4())
    risk_calls: list = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return False

    async def _risk_should_never_be_called(**kwargs):
        risk_calls.append(kwargs)
        raise AssertionError("evaluate_controlled_proof_risk must not run once the proof's entry is already linked")

    async def _create(*, db, request):
        raise AssertionError("must not force a second BUY once the proof's one controlled entry is already linked")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_should_never_be_called)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert risk_calls == []


@pytest.mark.asyncio
async def test_controlled_proof_forces_sell_after_buy_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 7: once should_propose_controlled_sell reports the
    controlled BUY is filled and the position is open, the proof deliberately
    proposes exactly one controlled SELL through the normal production path,
    even while the ordinary strategy still proposes HOLD."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    buy_package_id = uuid.uuid4()
    sell_package_id = uuid.uuid4()
    proof = _controlled_proof_stub(decision_record_id=cycle.decision_record_id, package_id=buy_package_id)
    create_requests: list = []
    sell_link_calls = []
    entry_link_calls = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        return True

    async def _risk_allow(*, db, proof_id, campaign_id, campaign_version, paper_account_id, product_id, side, notional_usd, actor):
        from app.services.controlled_proof import ControlledProofRiskOutcome
        assert side == "SELL"
        return ControlledProofRiskOutcome(
            verdict="ALLOW", approved_notional_usd=notional_usd, reason_code="risk_approved", risk_event_id=uuid.uuid4(),
        )

    async def _link_sell(*, db, proof, sell_package_id):
        sell_link_calls.append(sell_package_id)
        proof.sell_package_id = sell_package_id

    async def _link_entry(*, db, proof, decision_record_id, mandate_id, mandate_version_id, mandate_evaluation_id):
        entry_link_calls.append(decision_record_id)

    async def _create(*, db, request):
        create_requests.append(request)
        return {"idempotent": False, "package": {"package_id": str(sell_package_id), "package_state": "READY"}}

    async def _execute(*, db, request):
        return AutomaticPackageExecutionOutcome(
            package_id=sell_package_id, campaign_id=cycle.capital_campaign_id,
            campaign_version=cycle.capital_campaign_version, decision_record_id=cycle.decision_record_id,
            mandate_id=cycle.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN",
            activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False,
            final_reason_code="test_stub_stops_before_activation", failed_closed=True, starting_state="READY",
        )

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _risk_allow)
    monkeypatch.setattr(worker_module, "link_controlled_proof_sell_package", _link_sell)
    monkeypatch.setattr(worker_module, "link_controlled_proof_entry", _link_entry)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert len(create_requests) == 1
    assert create_requests[0].commissioning_entry_mode == "controlled_proof"
    assert create_requests[0].forced_action == "CLOSE_POSITION_PROPOSED"
    assert create_requests[0].controlled_proof_id == proof.proof_id
    assert create_requests[0].expected_decision_record_id is None
    assert sell_link_calls == [sell_package_id]
    assert entry_link_calls == [], "a SELL must never be linked through the BUY entry-linkage path"
    assert proof.sell_package_id == sell_package_id


@pytest.mark.asyncio
async def test_controlled_proof_sell_not_reproposed_after_sell_already_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 8: once the proof's one controlled SELL is already
    linked, a worker restart must not propose a second one, even while
    should_propose_controlled_sell's own readiness check would otherwise
    still be satisfied and the ordinary strategy still proposes HOLD."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    proof = _controlled_proof_stub(decision_record_id=cycle.decision_record_id, package_id=uuid.uuid4())
    proof.sell_package_id = uuid.uuid4()

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        return proof

    async def _sell_ready(*, db, proof):
        # The real should_propose_controlled_sell already returns False once
        # proof.sell_package_id is set -- asserted here as the worker's own
        # contract with that helper, not re-derived.
        return proof.sell_package_id is None

    async def _create(*, db, request):
        raise AssertionError("must not propose a second controlled SELL once one is already linked")

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _sell_ready)
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))


# --- Controlled Proof: immediate dispatch (RUN_CONTROLLED_PROOF accepted -> begins promptly) ---

class _FakeSessionContext:
    """Minimal async-context-manager stand-in for AsyncSessionLocal() in
    dispatch tests -- proves a fresh, independent session is used, without
    needing a real database."""

    def __init__(self, db: object) -> None:
        self._db = db

    async def __aenter__(self) -> object:
        return self._db

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ambient_state",
    [
        "BUY", "SELL", "HOLD", "weak_or_conflicting_agreement", "hold_action",
        "sell_signal_no_position_to_close", "risk_not_permitted", "failed_candidate",
    ],
)
async def test_operator_controlled_proof_dispatch_is_independent_of_ambient_autonomous_state(
    monkeypatch: pytest.MonkeyPatch, ambient_state: str,
) -> None:
    """The operator attempt has no ambient action/skip input at all.

    Parameterizing every historically-coupled state makes the contract
    explicit: none can reach or gate the proof candidate builder.
    """
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    proof_id = uuid.uuid4()
    calls: list[uuid.UUID] = []

    async def _operator_attempt(*, db, proof_id):
        calls.append(proof_id)

    async def _ambient_attempt(**_kwargs):
        raise AssertionError(f"ambient autonomous path must not run for {ambient_state}")

    monkeypatch.setattr(worker_module, "AsyncSessionLocal", lambda: _FakeSessionContext(object()))
    monkeypatch.setattr(worker_module, "_attempt_operator_controlled_proof_entry", _operator_attempt)
    monkeypatch.setattr(worker_module, "_run_autonomous_and_campaign_orchestration_attempt", _ambient_attempt)

    await worker_module.dispatch_controlled_proof_immediate_attempt(proof_id=proof_id)

    assert calls == [proof_id]


@pytest.mark.asyncio
async def test_controlled_proof_dispatch_runs_operator_attempt_in_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: an accepted proof is promptly dispatched through the
    same shared path the timer-driven poll uses -- never a second,
    divergent pipeline -- using its own fresh session, not any caller's."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    proof_id = uuid.uuid4()
    sessions_opened: list = []
    attempts: list = []
    fake_db = object()

    def _fake_session_factory():
        sessions_opened.append(fake_db)
        return _FakeSessionContext(fake_db)

    async def _fake_attempt(*, db, proof_id):
        attempts.append((db, proof_id))

    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _fake_session_factory)
    monkeypatch.setattr(worker_module, "_attempt_operator_controlled_proof_entry", _fake_attempt)

    await worker_module.dispatch_controlled_proof_immediate_attempt(proof_id=proof_id)

    assert sessions_opened == [fake_db]
    assert attempts == [(fake_db, proof_id)]


@pytest.mark.asyncio
async def test_controlled_proof_dispatch_failure_is_logged_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement: failures remain recorded and fail closed -- a failure
    inside the dispatched attempt must never raise out of dispatch (which
    runs unsupervised as a background task with no caller to catch it) and
    must be clearly logged with the proof id."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    proof_id = uuid.uuid4()

    def _fake_session_factory():
        return _FakeSessionContext(object())

    async def _fake_attempt(*, db, proof_id):
        raise RuntimeError("simulated orchestration failure")

    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _fake_session_factory)
    monkeypatch.setattr(worker_module, "_attempt_operator_controlled_proof_entry", _fake_attempt)

    with caplog.at_level(logging.INFO, logger=worker_module.logger.name):
        await worker_module.dispatch_controlled_proof_immediate_attempt(proof_id=proof_id)

    messages = [record.getMessage() for record in caplog.records]
    assert any("controlled_proof_dispatch_started" in m and str(proof_id) in m for m in messages)
    assert any("controlled_proof_dispatch_failed" in m and str(proof_id) in m for m in messages)
    assert not any("controlled_proof_dispatch_completed" in m for m in messages)


@pytest.mark.asyncio
async def test_controlled_proof_duplicate_dispatch_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: duplicate dispatch (e.g. two concurrent operator
    requests, or a dispatch racing the normal poll) must never claim,
    package, or activate the same proof twice. The dispatch mechanism adds
    no new locking of its own -- it relies entirely on
    claim_next_controlled_proof_for_scope's SELECT ... FOR UPDATE plus
    every downstream idempotency key, exercised here by having the second
    call's shared-attempt observe the proof as already claimed/linked."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    proof_id = uuid.uuid4()
    proof = _controlled_proof_stub()
    attempt_calls = 0

    async def _fake_attempt(*, db, proof_id):
        nonlocal attempt_calls
        attempt_calls += 1
        if attempt_calls == 1:
            # First dispatch claims and links the proof's one controlled entry.
            proof.decision_record_id = uuid.uuid4()
        else:
            # Second (duplicate) dispatch must see it already linked and do
            # nothing further -- mirroring already_has_entry's real
            # short-circuit in _attempt_automatic_ready_package_creation.
            assert proof.decision_record_id is not None

    def _fake_session_factory():
        return _FakeSessionContext(object())

    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _fake_session_factory)
    monkeypatch.setattr(worker_module, "_attempt_operator_controlled_proof_entry", _fake_attempt)

    await worker_module.dispatch_controlled_proof_immediate_attempt(proof_id=proof_id)
    await worker_module.dispatch_controlled_proof_immediate_attempt(proof_id=proof_id)

    assert attempt_calls == 2
    assert proof.decision_record_id is not None


@pytest.mark.asyncio
async def test_run_orchestration_cycle_still_reaches_controlled_proof_claim_on_normal_candle_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: normal autonomous candle-driven orchestration remains
    unchanged by the dispatch feature -- run_orchestration_cycle must still
    reach claim_next_controlled_proof_for_scope exactly as before the
    _run_autonomous_and_campaign_orchestration_attempt extraction, without
    mocking _attempt_automatic_ready_package_creation itself out of the
    way (unlike the observability-focused helper elsewhere in this file)."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(proposed_action="HOLD_NO_TRADE", decision_kind="HOLD_NO_TRADE")
    claim_calls: list = []

    async def _claim(*, db, campaign_id, campaign_version, provider, environment, product_id, cycle_id):
        claim_calls.append(product_id)
        return None

    async def _create(*, db, request):
        raise AssertionError("no proof claimed -- must not create a package")

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "claim_next_controlled_proof_for_scope", _claim)
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _async_return(False))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(
        worker_module, "run_campaign_orchestration_preview_for_candle",
        _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}], "cycle_count": 1}),
    )

    await worker_module.run_orchestration_cycle(db=_CampaignPreviewCapableDB(), client=object(), config=_config())

    assert claim_calls == ["BTC-USD"]


@pytest.mark.asyncio
async def test_automatic_ready_package_path_never_calls_authorize_activate_dryrun_or_provider_submit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.canonical_preview_package as canonical_package
    import app.services.live_crypto_orders as live_crypto_orders
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle()
    runtime_campaign = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    called = {
        "authorize": 0,
        "activate": 0,
        "dry_run": 0,
        "provider_submit": 0,
    }

    async def _unexpected_authorize(*args, **kwargs):
        called["authorize"] += 1
        raise AssertionError("authorize should not be called")

    async def _unexpected_activate(*args, **kwargs):
        called["activate"] += 1
        raise AssertionError("activate should not be called")

    async def _unexpected_dry_run(*args, **kwargs):
        called["dry_run"] += 1
        raise AssertionError("dry run should not be called")

    async def _unexpected_submit(*args, **kwargs):
        called["provider_submit"] += 1
        raise AssertionError("provider submit should not be called")

    async def _fake_create(*, db, request):
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    monkeypatch.setattr(canonical_package, "authorize_canonical_preview_package", _unexpected_authorize)
    monkeypatch.setattr(canonical_package, "activate_canonical_proving_campaign", _unexpected_activate)
    monkeypatch.setattr(canonical_package, "run_dry_run_for_canonical_preview_package", _unexpected_dry_run)
    monkeypatch.setattr(live_crypto_orders.LiveCryptoOrderService, "submit", _unexpected_submit)

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    caplog.set_level(logging.INFO)
    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert called["authorize"] == 0
    assert called["activate"] == 0
    assert called["dry_run"] == 0
    assert called["provider_submit"] == 0
    # Automatic mandate progression remains independently disabled by default.
    assert "automatic_package_progression_skipped" in caplog.text
    assert "reason=feature_disabled" in caplog.text


@pytest.mark.asyncio
async def test_bounded_path_shadow_proposal_to_ready_package_without_live_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 6: shadow strategy proposals -> authoritative governed aggregate ->
    risk approval -> positive net edge -> execution-ready package, with
    exchange submission mocked/disabled throughout.

    Uses the REAL compose_campaign_authoritative_cycle (not a hand-built
    stand-in composition dict) so the produced OPEN_POSITION_PROPOSED
    decision genuinely reflects a shadow-mode strategy vote (the roster
    layer always sets execution_mode=SHADOW/live_submission_allowed=False --
    see authoritative.py's roster-run/proposal scope check) that clears
    risk approval and the corrected net-edge gate, then feeds that real
    composition into _attempt_automatic_ready_package_creation and confirms
    a READY package is created while authorize/activate/dry_run/provider_submit
    are never reached.
    """
    from decimal import Decimal as _Decimal
    from datetime import datetime as _datetime, timezone as _timezone
    from uuid import UUID as _UUID

    import app.services.canonical_preview_package as canonical_package
    import app.services.live_crypto_orders as live_crypto_orders
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle
    from app.services.risk import RiskDecisionAction, RiskEvaluationResult

    campaign = SimpleNamespace(
        campaign_id=_UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=3,
        runtime_campaign_uuid=_UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=_Decimal("25"),
        maximum_position_size=_Decimal("10"),
        minimum_position_size=_Decimal("2"),
        maximum_total_exposure=_Decimal("20"),
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=_UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=_Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=_Decimal("25"))
    candle = SimpleNamespace(asset_id=_UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=_Decimal("100"), close_time=_datetime(2026, 7, 15, 0, 15, tzinfo=_timezone.utc), interval="15m", open_time=_datetime(2026, 7, 15, 0, 0, tzinfo=_timezone.utc))
    asset = SimpleNamespace(id=_UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=_Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    # execution_mode/live_submission_allowed here mirror what the strategy
    # roster layer actually enforces (SHADOW / False) before authoritative.py
    # will trust a roster run's evidence at all.
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1",
        "strategy_version": "1",
        "action": "BUY",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": "4.2",
        "expected_value": "4.2",
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        "execution_mode": "SHADOW",
        "live_submission_allowed": False,
    }
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=_Decimal("25"), start_of_day_equity=_Decimal("25"), current_equity=_Decimal("25"),
        max_position_size_pct=_Decimal("0.10"), max_daily_loss_pct=_Decimal("0.03"), high_water_mark_equity=_Decimal("25"),
        max_drawdown_pct=_Decimal("0.10"), consecutive_losses_on_pair=0, cooldown_after_losses=3, last_loss_at=None,
        cooldown_duration_minutes=_Decimal("1440"), evaluation_time=_datetime(2026, 7, 15, 0, 16, tzinfo=_timezone.utc),
        data_is_stale=False, data_has_gaps=False, global_kill_switch_engaged_state=False, global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False, account_kill_switch_rearm_required=False, global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True, risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=_Decimal("0.05"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=_UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    composition = result.composition
    assert composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    assert composition["termination_stage"] == "preview_generated"
    assert composition["failed_closed"] is False

    cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(),
        capital_campaign_id=campaign.campaign_id,
        capital_campaign_version=campaign.version,
            decision_record_id=_UUID(composition["decision_record_id"]),
            mandate_id=uuid.uuid4(),
            mandate_version_id=uuid.uuid4(),
            mandate_evaluation_id=uuid.uuid4(),
        termination_stage=composition["termination_stage"],
        proposed_action=composition["proposed_action"],
        risk_verdict=composition["selected_decision"].get("risk_verdict"),
        cycle_context={
            "candle": {"close_time": "2026-07-15T00:15:00+00:00"},
            "authoritative_composition": composition,
        },
    )
    payload = {"cycles": [{"cycle_id": str(cycle.cycle_id)}]}

    runtime_campaign_for_package = SimpleNamespace(paper_account_id=uuid.uuid4())
    profile = SimpleNamespace(id=uuid.uuid4())
    package_calls: list[object] = []
    live_authority_calls = {"authorize": 0, "activate": 0, "dry_run": 0, "provider_submit": 0}

    async def _fake_create(*, db, request):
        package_calls.append(request)
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    def _unexpected(name):
        async def _inner(*args, **kwargs):
            live_authority_calls[name] += 1
            raise AssertionError(f"{name} should not be called")
        return _inner

    monkeypatch.setattr(canonical_package, "authorize_canonical_preview_package", _unexpected("authorize"))
    monkeypatch.setattr(canonical_package, "activate_canonical_proving_campaign", _unexpected("activate"))
    monkeypatch.setattr(canonical_package, "run_dry_run_for_canonical_preview_package", _unexpected("dry_run"))
    monkeypatch.setattr(live_crypto_orders.LiveCryptoOrderService, "submit", _unexpected("provider_submit"))

    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "_has_active_ready_package_for_opportunity", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_active_proving_activation", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(runtime_campaign_for_package))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(profile))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=payload)

    assert len(package_calls) == 1
    assert package_calls[0].max_proposed_order_amount == _Decimal("5")
    assert live_authority_calls == {"authorize": 0, "activate": 0, "dry_run": 0, "provider_submit": 0}


@pytest.mark.asyncio
async def test_automatic_ready_package_hold_termination_logs_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        termination_stage="hold_no_package_created",
        proposed_action="HOLD",
        decision_kind="HOLD",
    )

    create_calls = {"count": 0}

    async def _fake_create(*, db, request):
        create_calls["count"] += 1
        return {
            "idempotent": False,
            "package": {"package_id": str(uuid.uuid4()), "package_state": "READY"},
            "readiness": {"ready": True, "package_state": "READY"},
        }

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _fake_create)

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert create_calls["count"] == 0
    assert "automatic_ready_package_skipped" in caplog.text
    assert "reason=termination_stage_hold_no_package_created" in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_hold_exposes_strategy_hold_signal_underlying_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        termination_stage="hold_no_package_created",
        proposed_action="HOLD",
        decision_kind="HOLD",
        selected_decision_reason="strategy_hold_signal",
        rejected_candidates=[{"instrument": "BTC-USD", "reason": "strategy_hold_signal"}],
    )

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert "automatic_ready_package_skipped" in caplog.text
    assert "reason=termination_stage_hold_no_package_created" in caplog.text
    assert "underlying_reason=strategy_hold_signal" in caplog.text
    assert '"strategy_hold_signal"' in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_position_transition_hold_exposes_underlying_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        termination_stage="hold_no_package_created",
        proposed_action="HOLD",
        decision_kind="HOLD",
        selected_decision_reason="action_position_transition_hold",
        rejected_candidates=[{"instrument": "BTC-USD", "reason": "action_position_transition_hold"}],
    )

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert "underlying_reason=action_position_transition_hold" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["global_kill_switch_engaged", "position_below_minimum_order_size", "non_positive_net_edge"],
)
async def test_automatic_ready_package_hold_reasons_are_distinguishable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reason: str,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        termination_stage="hold_no_package_created",
        proposed_action="HOLD",
        decision_kind="HOLD",
        selected_decision_reason=reason,
        rejected_candidates=[{"instrument": "BTC-USD", "reason": reason}],
    )

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert f"underlying_reason={reason}" in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_failed_closed_exposes_underlying_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(
        termination_stage="failed_closed",
        proposed_action="FAILED_CLOSED",
        decision_kind="MANUAL_REVIEW_REQUIRED",
        selected_decision_reason="risk_unavailable",
        rejected_candidates=[{"instrument": "BTC-USD", "reason": "risk_unavailable"}],
    )

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert "reason=termination_stage_failed_closed" in caplog.text
    assert "underlying_reason=risk_unavailable" in caplog.text


@pytest.mark.asyncio
async def test_automatic_ready_package_non_hold_skip_has_no_underlying_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    cycle = _automatic_cycle(risk_verdict="VETO")

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "_load_cycle_by_id", _async_return(cycle))

    await worker_module._attempt_automatic_ready_package_creation(
        db=object(),
        orchestration_payload=_automatic_payload(cycle),
    )

    assert "reason=risk_not_permitted" in caplog.text
    assert "underlying_reason=None" in caplog.text


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
async def test_replay_failure_is_contained_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(_decision_record()))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(account))
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _async_return(SimpleNamespace(execution_status="executed")))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("buy"))

    async def _fail_build(*_args, **_kwargs):
        raise RuntimeError("decision package read failed")

    monkeypatch.setattr(worker_module.DecisionPackageBuilder, "build_decision_package", _fail_build)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.signals_created == 1
    assert db.commits > 0
    assert any(item.__class__.__name__ == "AuditLog" and getattr(item, "action", None) == "decision_package_replay_failed" for item in db.added)


@pytest.mark.asyncio
async def test_replay_cancellation_propagates_when_worker_is_shutting_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    class _CancelingTask:
        def cancelling(self) -> int:
            return 1

    class _FakeEvidenceSessionContext:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _raise_cancelled(*_args, **_kwargs):
        raise worker_module.asyncio.CancelledError()

    monkeypatch.setattr(worker_module.asyncio, "current_task", lambda: _CancelingTask())
    monkeypatch.setattr(worker_module.DecisionPackageBuilder, "build_decision_package", _raise_cancelled)
    monkeypatch.setattr(worker_module, "AsyncSessionLocal", lambda: _FakeEvidenceSessionContext())

    with pytest.raises(worker_module.asyncio.CancelledError):
        await worker_module._produce_research_evidence(
            db=_FakeDB(),
            decision_package_builder=worker_module.DecisionPackageBuilder(),
            decision_record=_decision_record(),
        )


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
async def test_aggregate_strategy_identity_is_skipped_without_reaching_registry(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # strategy_roster_aggregate is a real, active Strategy catalog row
    # (created by _ensure_aggregate_strategy_catalog_entry in authoritative.py
    # purely for canonical package binding continuity) but is not an
    # independently executable strategy module. It must be filtered out of
    # the generic per-strategy paper-execution loop before ever calling
    # strategy_registry.get, and must never trigger the
    # "Skipping unregistered strategy" warning -- that warning should be
    # reserved for genuinely unexpected unregistered slugs.
    db = _FakeDB()
    asset = _asset()
    enabled_strategy = _strategy_row()
    aggregate_strategy = _aggregate_strategy_row()
    parameter_set = _parameter_set()
    registry_lookups: list[str] = []

    def _tracking_get(slug):
        registry_lookups.append(slug)
        if slug == AGGREGATE_STRATEGY_SLUG:
            raise StrategyLookupError(slug)
        return _FixedStrategy("hold")

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([enabled_strategy, aggregate_strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _async_return(_candles(2)))
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", _tracking_get)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    # The genuine strategy still executes normally.
    assert stats.signals_created == 1
    generated_signals = [item for item in db.added if item.__class__.__name__ == "Signal"]
    assert len(generated_signals) == 1
    assert generated_signals[0].strategy_id == enabled_strategy.id

    # The aggregate identity never reached strategy_registry.get at all.
    assert AGGREGATE_STRATEGY_SLUG not in registry_lookups

    skip_records = [record for record in caplog.records if "paper_execution_skip reason=aggregate_identity_not_executable" in record.getMessage()]
    assert len(skip_records) == 1
    assert AGGREGATE_STRATEGY_SLUG in skip_records[0].getMessage()

    # No "unregistered strategy" warning-spam for the known aggregate identity.
    warning_records = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert not any("unregistered strategy" in record.getMessage().lower() for record in warning_records)


@pytest.mark.asyncio
async def test_kraken_asset_candle_lookup_uses_kraken_ingestion_interval_not_config_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_ingestion_cycle (worker_entrypoint.py) always writes Kraken candles
    # at KRAKEN_CANDLE_INTERVAL ("15m") regardless of the configured
    # ORCHESTRATION_CANDLE_INTERVAL default ("1m" in _config() below). Before
    # the fix, this loop queried every asset with config.candle_interval, so
    # a Kraken asset's candles were queried at the wrong interval and never
    # found -- a permanent candle_count=0 for any Kraken proving-campaign
    # asset. The lookup must resolve interval per-asset by exchange instead.
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    db = _FakeDB()
    kraken_asset = _kraken_asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    candle_lookup_calls: list[tuple[uuid.UUID, str]] = []

    async def _tracking_load_latest_candles(_db, *, asset_id, interval, limit):
        candle_lookup_calls.append((asset_id, interval))
        return _candles(2)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([kraken_asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _tracking_load_latest_candles)
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    config = _config()
    assert config.candle_interval == "1m"

    stats = await run_orchestration_cycle(db=db, client=object(), config=config)

    assert candle_lookup_calls == [(kraken_asset.id, "15m")]
    assert stats.signals_created == 1


@pytest.mark.asyncio
async def test_binance_asset_candle_lookup_still_uses_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    db = _FakeDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    candle_lookup_calls: list[tuple[uuid.UUID, str]] = []

    async def _tracking_load_latest_candles(_db, *, asset_id, interval, limit):
        candle_lookup_calls.append((asset_id, interval))
        return _candles(2)

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "ingest_decision_records", _fake_decision_ingestion)
    monkeypatch.setattr(worker_module, "_load_decision_record_for_signal", _async_return(None))
    monkeypatch.setattr(worker_module, "_produce_research_evidence", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([asset]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([strategy]))
    monkeypatch.setattr(worker_module, "_load_latest_parameter_set", _async_return(parameter_set))
    monkeypatch.setattr(worker_module, "_load_latest_candles", _tracking_load_latest_candles)
    monkeypatch.setattr(worker_module, "_signal_exists", _async_return(False))
    monkeypatch.setattr(worker_module, "_load_primary_account_by_asset_class", _async_return(None))
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("hold"))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert candle_lookup_calls == [(asset.id, "1m")]
    assert stats.signals_created == 1


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
    # _ResumeCapableDB only emulates enough of AsyncSession for the
    # commissioning-resume hook this test isolates; the claim-recovery
    # sweep is an unrelated, orthogonal per-cycle step, neutralized here
    # the same way every other unrelated hook already is above.
    monkeypatch.setattr(worker_module, "sweep_stale_autonomous_execution_claims", _async_return(0))

    await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert resume_calls["count"] == 1


@pytest.mark.asyncio
async def test_worker_invokes_stale_claim_recovery_sweep_every_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring proof: the claim-recovery sweep runs every cycle, independent
    of decision composition -- the only thing that revisits a claim once
    the cycle that originally created it stops recurring (see
    sweep_stale_autonomous_execution_claims)."""
    db = _ResumeCapableDB()
    sweep_calls = []

    async def _sweep(*, db):
        sweep_calls.append(db)
        return 1

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
                started=False, reason="not_due", campaign_id=None, candidates_generated=0,
                candidates_evaluated=0, descendants_generated=0, champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "sweep_stale_autonomous_execution_claims", _sweep)

    await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert sweep_calls == [db]
    # The sweep's own result is committed durably, independent of whatever
    # the rest of the cycle does later.
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_worker_isolates_stale_claim_recovery_sweep_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ResumeCapableDB()

    async def _sweep_fail(*, db):
        raise RuntimeError("sweep failed")

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
                started=False, reason="not_due", campaign_id=None, candidates_generated=0,
                candidates_evaluated=0, descendants_generated=0, champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "sweep_stale_autonomous_execution_claims", _sweep_fail)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert db.rollbacks >= 1


@pytest.mark.asyncio
async def test_worker_invokes_automatic_reconciliation_poll_every_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring proof: automatic reconciliation runs every cycle, before this
    cycle's own orchestration attempt -- so a fill discovered here is
    already visible to _has_unresolved_reconciliation and
    should_propose_controlled_sell within the same cycle. Both Controlled
    Proof and ordinary autonomous execution share this one call site --
    poll_unresolved_live_orders itself has no Controlled-Proof-specific
    branch."""
    db = _ResumeCapableDB()
    poll_calls = []

    async def _poll(*, db):
        poll_calls.append(db)
        from app.services.orchestration.reconciliation_scheduler import ReconciliationPollOutcome
        return ReconciliationPollOutcome(candidates_discovered=1, reconciled=1, still_pending=0, failed=0)

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
                started=False, reason="not_due", campaign_id=None, candidates_generated=0,
                candidates_evaluated=0, descendants_generated=0, champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "sweep_stale_autonomous_execution_claims", _async_return(0))
    monkeypatch.setattr(worker_module, "poll_unresolved_live_orders", _poll)

    await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert poll_calls == [db]


@pytest.mark.asyncio
async def test_worker_isolates_automatic_reconciliation_poll_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ResumeCapableDB()

    async def _poll_fail(*, db):
        raise RuntimeError("reconciliation poll failed")

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
                started=False, reason="not_due", campaign_id=None, candidates_generated=0,
                candidates_evaluated=0, descendants_generated=0, champion=None,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))
    monkeypatch.setattr(worker_module, "sweep_stale_autonomous_execution_claims", _async_return(0))
    monkeypatch.setattr(worker_module, "poll_unresolved_live_orders", _poll_fail)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    # A reconciliation poll failure must never abort the rest of the cycle.
    assert stats.ingestion_assets_ok == 1
    assert db.rollbacks >= 1


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


# Regression for production incident: strategy_aggregate_completed correctly
# resolved a SELL-majority-no-position vote to HOLD, and several minutes
# later a *different* substage -- the per-(strategy, asset) paper-execution
# loop -- failed with PendingRollbackError all the way up to run_forever's
# top-level "Pipeline orchestration cycle failed" handler, losing that
# cycle's remaining paper-execution work. Root cause: each (strategy, asset)
# iteration is its own transactional unit delimited by a per-iteration
# db.commit(), but nothing rolled back on failure -- an exception from
# orchestrate_paper_signal_execution was caught and handled (log + audit),
# but if it had already left the session invalid, every following statement
# in that same iteration (ingest_decision_records, the audit-log add, or the
# commit itself) failed too, and propagated completely uncaught out of
# run_orchestration_cycle since no outer handler wrapped this loop.
@pytest.mark.asyncio
async def test_paper_execution_iteration_failure_rolls_back_and_cycle_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _RecoveryAwareDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())

    async def _raising_orchestrate(*args, **kwargs):
        db.failed_transaction = True
        raise RuntimeError("simulated live-execution db failure")

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
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _raising_orchestrate)
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("buy"))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    caplog.set_level(logging.INFO)

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    # The cycle completed (did not raise / did not surface PendingRollbackError
    # out of run_orchestration_cycle) and failed this one iteration closed.
    assert stats.signals_created == 1
    assert stats.executions_attempted == 1
    assert db.rollbacks >= 1
    # _rollback_active_session cleared the poisoned flag -- the session is
    # usable again, exactly as it must be for later stages/cycles.
    assert db.failed_transaction is False

    assert "paper_execution_iteration_failed" in caplog.text
    assert "stage=paper_execution_iteration" in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_paper_execution_iteration_failure_does_not_poison_next_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _RecoveryAwareDB()
    asset = _asset()
    strategy = _strategy_row()
    parameter_set = _parameter_set()
    account = SimpleNamespace(id=uuid.uuid4())

    call_count = {"value": 0}

    async def _first_fails_then_succeeds(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            db.failed_transaction = True
            raise RuntimeError("simulated live-execution db failure")
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
    monkeypatch.setattr(worker_module, "orchestrate_paper_signal_execution", _first_fails_then_succeeds)
    monkeypatch.setattr(worker_module.strategy_registry, "get", lambda slug: _FixedStrategy("buy"))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    first_stats = await run_orchestration_cycle(db=db, client=object(), config=_config())
    assert first_stats.signals_created == 1
    assert first_stats.executions_attempted == 1
    assert db.failed_transaction is False

    # A later cycle -- reusing the same (in production, always fresh) session
    # -- is unaffected by the earlier failure and completes successfully.
    second_stats = await run_orchestration_cycle(db=db, client=object(), config=_config())
    assert second_stats.signals_created == 1
    assert second_stats.executions_attempted == 1
    assert call_count["value"] == 2


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
async def test_active_level1_and_level2_resolver_selects_level2() -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    level1 = _active_kraken_mandate(autonomy_level="LEVEL_1")
    level2 = _active_kraken_mandate(autonomy_level="LEVEL_2")
    db = _MandateResolverDB([level1, level2])

    resolved = await worker_module._load_single_active_kraken_mandate(db)

    assert resolved is level2
    assert "autonomous_capital_mandates.autonomy_level = 'LEVEL_2'" in db.compiled_sql
    assert "LIMIT 2" in db.compiled_sql


@pytest.mark.asyncio
async def test_only_active_level1_resolver_safely_skips(caplog: pytest.LogCaptureFixture) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    db = _MandateResolverDB([_active_kraken_mandate(autonomy_level="LEVEL_1")])
    caplog.set_level(logging.INFO)

    resolved = await worker_module._load_single_active_kraken_mandate(db)

    assert resolved is None
    assert "autonomous_cycle_skip reason=no_active_kraken_mandate" in caplog.text


@pytest.mark.asyncio
async def test_two_active_level2_mandates_remain_ambiguous_and_fail_closed(caplog: pytest.LogCaptureFixture) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    db = _MandateResolverDB(
        [
            _active_kraken_mandate(autonomy_level="LEVEL_2", updated_at=datetime(2026, 7, 22, 2, tzinfo=timezone.utc)),
            _active_kraken_mandate(autonomy_level="LEVEL_2", updated_at=datetime(2026, 7, 22, 1, tzinfo=timezone.utc)),
        ]
    )
    caplog.set_level(logging.WARNING)

    resolved = await worker_module._load_single_active_kraken_mandate(db)

    assert resolved is None
    assert "autonomous_cycle_skip reason=ambiguous_active_kraken_mandates mandate_count=2" in caplog.text


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
        _async_return(SimpleNamespace(id=uuid.uuid4(), asset_id=uuid.uuid4(), close_time=candle_close)),
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


# Regression for the first production incident after the aggregator went
# live: campaign orchestration composed the cycle before the strategy roster
# had created this candle's StrategyRosterRun, so the aggregator's exact-match
# lookup always missed (strategy_aggregate_skipped
# reason=exact_roster_run_unavailable) on every single cycle. The roster must
# run, and its writes must be visible, before campaign orchestration composes
# the same candle.
@pytest.mark.asyncio
async def test_worker_runs_strategy_roster_before_campaign_orchestration_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _CampaignPreviewCapableDB()
    cycle_id = uuid.uuid4()
    candle = SimpleNamespace(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        open_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 10, 12, 15, tzinfo=timezone.utc),
    )
    call_order: list[str] = []

    async def _roster(*, db, request):
        call_order.append("strategy_roster")
        return SimpleNamespace(roster_run_id=uuid.uuid4(), replayed=False)

    async def _campaign_preview(*, db, trigger):
        call_order.append("campaign_orchestration_preview")
        return {"cycle_count": 0, "reason": "no_campaign_candidates", "considered_campaigns": [], "eligible_campaigns": [], "skipped_campaigns": []}

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_run_kraken_btc_autonomous_cycle_if_due", _async_return((cycle_id, candle)))
    monkeypatch.setattr(worker_module, "run_strategy_roster_for_candle", _roster)
    monkeypatch.setattr(worker_module, "run_campaign_orchestration_preview_for_candle", _campaign_preview)
    monkeypatch.setattr(worker_module, "_attempt_automatic_ready_package_creation", _async_return(None))
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _async_return(_not_due_research_result()))
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    assert call_order == ["strategy_roster", "campaign_orchestration_preview"]


# Regression for the second half of the same production incident: a
# campaign_orchestration failure (e.g. the compounding-percentage bug above)
# rolls back the shared session, which expires every ORM instance the session
# was tracking, including the previously loaded kraken candle. Any later code
# that still touches candle.<attr> directly (rather than a primitive captured
# before the rollback) raises MissingGreenlet under the real async ORM. This
# proves the worker only ever uses primitives captured up front, so a prior
# rollback cannot poison the campaign_orchestration block's own logging, and
# the cycle still proceeds into its later stages (research, snapshot).
@pytest.mark.asyncio
async def test_worker_survives_a_prior_rollback_without_touching_expired_candle_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    candle = _ExpiringCandle(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        open_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 10, 12, 15, tzinfo=timezone.utc),
    )
    db = _ExpiringSessionCampaignPreviewCapableDB(tracked_candle=candle)
    cycle_id = uuid.uuid4()
    research_started = {"count": 0}
    ready_package_attempted = {"count": 0}

    async def _raise_roster(*, db, request):
        # Simulates any independently caught, database-backed subsystem
        # failure that triggers _rollback_active_session before this point --
        # here it is the roster itself, but the same hazard exists for any
        # earlier block once identities are shared across the whole cycle.
        raise RuntimeError("roster failed")

    async def _campaign_preview(*, db, trigger):
        return {"cycle_count": 0, "reason": "no_campaign_candidates", "considered_campaigns": [], "eligible_campaigns": [], "skipped_campaigns": []}

    async def _ready_package_attempted(*, db, orchestration_payload, originating_autonomous_cycle_id=None, autonomous_cycle_ids_by_product=None):
        # Only reached if campaign_orchestration's try body -- including its
        # logging, which reads the candle's id/close_time -- ran to
        # completion without raising. A stale direct attribute touch on the
        # expired candle there would raise _MissingGreenletSimulation and get
        # caught by that block's own except before this point is ever reached.
        ready_package_attempted["count"] += 1

    async def _research_started(*, db):
        research_started["count"] += 1
        return _not_due_research_result()

    import app.services.orchestration.continuous_pipeline_worker as worker_module

    monkeypatch.setattr(worker_module, "run_ingestion_cycle", _fake_ingestion_cycle)
    monkeypatch.setattr(worker_module, "_run_kraken_btc_autonomous_cycle_if_due", _async_return((cycle_id, candle)))
    monkeypatch.setattr(worker_module, "run_strategy_roster_for_candle", _raise_roster)
    monkeypatch.setattr(worker_module, "run_campaign_orchestration_preview_for_candle", _campaign_preview)
    monkeypatch.setattr(worker_module, "_attempt_automatic_ready_package_creation", _ready_package_attempted)
    monkeypatch.setattr(worker_module, "_load_active_assets", _async_return([]))
    monkeypatch.setattr(worker_module, "_load_active_strategies", _async_return([]))
    monkeypatch.setattr(worker_module, "run_deterministic_research_cycle_if_due", _research_started)
    monkeypatch.setattr(worker_module, "capture_system_intelligence_snapshot_if_due", _async_return(None))

    stats = await run_orchestration_cycle(db=db, client=object(), config=_config())

    assert stats.ingestion_assets_ok == 1
    # Exactly one rollback, from the roster failure. If campaign_orchestration's
    # logging still touched the expired candle directly (the pre-fix bug), it
    # would raise inside that block's own try, get caught by its own except,
    # and trigger a second rollback here.
    assert db.rollbacks == 1
    assert ready_package_attempted["count"] == 1
    # The cycle must still reach its later stages after the contained failure.
    assert research_started["count"] == 1


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


@pytest.mark.asyncio
async def test_run_forever_persists_startup_event_with_initialized_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    class _BootAuditSession:
        def __init__(self, *, fail_commit: bool = False) -> None:
            self.fail_commit = fail_commit
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

        async def commit(self) -> None:
            if self.fail_commit:
                raise RuntimeError("boot-commit-failed")

    class _SessionContext:
        def __init__(self, session: object) -> None:
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeHTTPClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _fake_run_orchestration_cycle(_db, **_kwargs):
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

    boot_session = _BootAuditSession()
    sessions = [boot_session, object()]

    def _fake_async_session_local():
        return _SessionContext(sessions.pop(0))

    monkeypatch.setattr(worker_module, "setup_logging", lambda: None)
    monkeypatch.setattr(worker_module.WorkerConfig, "from_env", staticmethod(_config))
    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _fake_async_session_local)
    monkeypatch.setattr(worker_module, "AsyncHTTPClient", _FakeHTTPClient)
    monkeypatch.setattr(worker_module, "BinanceUSClient", lambda _http_client: object())
    monkeypatch.setattr(worker_module, "KrakenSpotClient", lambda _http_client: object())
    monkeypatch.setattr(worker_module, "run_orchestration_cycle", _fake_run_orchestration_cycle)
    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker_module.run_forever()

    assert len(boot_session.added) == 1
    startup_event = boot_session.added[0]
    assert startup_event.action == worker_module._WORKER_BOOT_ACTION
    payload = startup_event.after_state
    started_at = datetime.fromisoformat(payload["started_at"])
    assert started_at.tzinfo is not None
    assert isinstance(payload["run_id"], str)
    assert payload["run_id"]


@pytest.mark.asyncio
async def test_run_forever_persists_startup_failure_event_without_timestamp_nameerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    class _BootAuditSession:
        def __init__(self, *, fail_commit: bool = False) -> None:
            self.fail_commit = fail_commit
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            self.added.append(obj)

        async def commit(self) -> None:
            if self.fail_commit:
                raise RuntimeError("boot-commit-failed")

    class _SessionContext:
        def __init__(self, session: object) -> None:
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeHTTPClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _fake_run_orchestration_cycle(_db, **_kwargs):
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

    boot_session = _BootAuditSession(fail_commit=True)
    boot_failed_session = _BootAuditSession()
    sessions = [boot_session, boot_failed_session, object()]

    def _fake_async_session_local():
        return _SessionContext(sessions.pop(0))

    monkeypatch.setattr(worker_module, "setup_logging", lambda: None)
    monkeypatch.setattr(worker_module.WorkerConfig, "from_env", staticmethod(_config))
    monkeypatch.setattr(worker_module, "AsyncSessionLocal", _fake_async_session_local)
    monkeypatch.setattr(worker_module, "AsyncHTTPClient", _FakeHTTPClient)
    monkeypatch.setattr(worker_module, "BinanceUSClient", lambda _http_client: object())
    monkeypatch.setattr(worker_module, "KrakenSpotClient", lambda _http_client: object())
    monkeypatch.setattr(worker_module, "run_orchestration_cycle", _fake_run_orchestration_cycle)
    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker_module.run_forever()

    assert len(boot_session.added) == 1
    assert len(boot_failed_session.added) == 1

    startup_payload = boot_session.added[0].after_state
    startup_failed_event = boot_failed_session.added[0]
    assert startup_failed_event.action == worker_module._WORKER_BOOT_FAILED_ACTION
    failure_payload = startup_failed_event.after_state

    assert failure_payload["run_id"] == startup_payload["run_id"]
    assert failure_payload["started_at"] == startup_payload["started_at"]
    started_at = datetime.fromisoformat(failure_payload["started_at"])
    assert started_at.tzinfo is not None


# --- _has_active_proving_activation: stale/expired activation lifecycle ---
#
# Production evidence: a BUY candidate that cleared the (now-fixed) economic
# gate was still permanently blocked with active_proving_activation_exists.
# Root cause -- confirmed by tracing every write site for CanonicalProvingActivation
# (app/services/canonical_preview_package.py's activate/pause/revoke functions)
# and every other read site (operator_cli/service.py::_activation_is_active,
# live_crypto_orders.py's order-submission gate): activation_state is set to
# 'ACTIVE' at creation and only ever transitions to PAUSED/REVOKED via explicit
# operator action -- nothing anywhere transitions it to EXPIRED/COMPLETED once
# its bounded expires_at window elapses (activation windows here are typically
# minutes, e.g. approval_event renewals use now + 5 minutes). Every OTHER read
# site in the codebase already guards against this by checking BOTH
# activation_state == 'ACTIVE' AND expires_at > now; _has_active_proving_activation
# was the one place that checked activation_state alone, so a long-expired
# activation left over from an earlier bounded proving/commissioning run
# permanently blocked all future automatic ready-package creation for that
# scope. These tests exercise the real SQL query (not a mock) against a real
# database to prove the fix actually filters at the query level.


def _install_sqlite_uuid_compiler() -> None:
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "CHAR(36)"


_install_sqlite_uuid_compiler()


class _AwaitableActivationSession:
    """Minimal AsyncSession-shaped adapter over a real synchronous ORM Session,
    scoped to exactly what _has_active_proving_activation and
    _has_unresolved_reconciliation need (db.scalar, db.execute)."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self._session = session

    async def scalar(self, statement):
        return self._session.scalar(statement)

    async def execute(self, statement):
        return self._session.execute(statement)


@contextmanager
def _proving_activation_sqlite_session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.schema import DefaultClause
    from sqlalchemy.sql.elements import TextClause

    from app.models.canonical_proving_activation import CanonicalProvingActivation

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    table = CanonicalProvingActivation.__table__
    # Postgres-only server defaults (gen_random_uuid(), now()) aren't valid
    # SQLite DEFAULT-clause syntax without parens; every column is supplied
    # explicitly on insert below so the default is never actually invoked,
    # but SQLite still parses it at CREATE TABLE time.
    for column in table.columns:
        default = column.server_default
        if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
            raw = default.arg.text.strip()
            if raw.endswith("()") and not raw.startswith("("):
                column.server_default = DefaultClause(text(f"({raw})"))
    CanonicalProvingActivation.metadata.create_all(engine, tables=[table])
    try:
        with Session(engine) as session:
            yield session, _AwaitableActivationSession(session)
    finally:
        engine.dispose()


def _seed_proving_activation(session, *, activation_state: str, expires_at: datetime, **scope) -> None:  # noqa: ANN001
    from app.models.canonical_proving_activation import CanonicalProvingActivation

    session.add(
        CanonicalProvingActivation(
            activation_id=uuid.uuid4(),
            package_id=uuid.uuid4(),
            approval_event_id=uuid.uuid4(),
            dry_run_live_crypto_order_id=uuid.uuid4(),
            campaign_id=scope["campaign_id"],
            campaign_version=scope["campaign_version"],
            paper_account_id=uuid.uuid4(),
            live_trading_profile_id=uuid.uuid4(),
            provider=scope["provider"],
            environment=scope["environment"],
            product=scope["product"],
            max_order_amount=Decimal("5"),
            max_deployed_capital=Decimal("5"),
            no_leverage=True,
            activated_at=expires_at - timedelta(hours=1),
            expires_at=expires_at,
            activation_state=activation_state,
            revoked_at=None,
            paused_at=None,
            invalidated_reason=None,
            created_at=expires_at - timedelta(hours=1),
            updated_at=expires_at - timedelta(hours=1),
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_has_active_proving_activation_ignores_expired_row() -> None:
    """The exact production defect: an ACTIVE-state row whose expires_at has
    already passed must NOT count as an active proving activation -- it is
    indistinguishable in the database from a genuinely current one unless
    expires_at is checked, since nothing ever flips activation_state on
    expiry."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    scope = dict(campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot", environment="production", product="BTC-USD")
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

    with _proving_activation_sqlite_session() as (raw_session, db):
        _seed_proving_activation(raw_session, activation_state="ACTIVE", expires_at=now - timedelta(days=5), **scope)
        result = await worker_module._has_active_proving_activation(db=db, now=now, **scope)

    assert result is False


@pytest.mark.asyncio
async def test_has_active_proving_activation_honors_unexpired_row() -> None:
    """A genuinely current ACTIVE activation (expires_at in the future) must
    still block -- this is the safety behavior the fix must preserve."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    scope = dict(campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot", environment="production", product="BTC-USD")
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

    with _proving_activation_sqlite_session() as (raw_session, db):
        _seed_proving_activation(raw_session, activation_state="ACTIVE", expires_at=now + timedelta(minutes=5), **scope)
        result = await worker_module._has_active_proving_activation(db=db, now=now, **scope)

    assert result is True


@pytest.mark.asyncio
async def test_has_active_proving_activation_ignores_revoked_row_regardless_of_expiry() -> None:
    """A REVOKED activation must never block, even if it hasn't technically
    reached its expires_at yet -- fail-closed behavior must not be confused
    with "block forever regardless of operator action"."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    scope = dict(campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot", environment="production", product="BTC-USD")
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

    with _proving_activation_sqlite_session() as (raw_session, db):
        _seed_proving_activation(raw_session, activation_state="REVOKED", expires_at=now + timedelta(minutes=5), **scope)
        result = await worker_module._has_active_proving_activation(db=db, now=now, **scope)

    assert result is False


@pytest.mark.asyncio
async def test_has_active_proving_activation_scopes_by_campaign_and_market() -> None:
    """An unexpired ACTIVE activation for a DIFFERENT campaign/version/market
    scope must not block this campaign's package creation -- the fix must not
    have widened the match beyond the original scope filters."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    other_scope = dict(campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot", environment="production", product="BTC-USD")
    query_scope = dict(campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot", environment="production", product="BTC-USD")

    with _proving_activation_sqlite_session() as (raw_session, db):
        _seed_proving_activation(raw_session, activation_state="ACTIVE", expires_at=now + timedelta(minutes=5), **other_scope)
        result = await worker_module._has_active_proving_activation(db=db, now=now, **query_scope)

    assert result is False


# --- _has_unresolved_reconciliation: diagnostic logging for the blocking gate ---
#
# Production evidence: with active_proving_activation_exists eliminated, the
# worker now blocks on unresolved_reconciliation_exists with no reconciliation
# ID, order ID, provider order ID, state, or timestamp in the logs -- pure
# instrumentation task, business logic (which records count as "unresolved")
# must not change. These tests exercise the real query against a real
# database and assert on real log output, not mocks, so they would catch a
# diagnostic query that silently drifted from the boolean gate's own query.


def _install_sqlite_jsonb_compiler() -> None:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "JSON"


_install_sqlite_jsonb_compiler()


def _fix_sqlite_server_defaults(table) -> None:  # noqa: ANN001
    from sqlalchemy import text
    from sqlalchemy.schema import DefaultClause
    from sqlalchemy.sql.elements import TextClause

    for column in table.columns:
        default = column.server_default
        if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
            raw = default.arg.text.strip().split("::", 1)[0]
            if raw.endswith("()") and not raw.startswith("("):
                raw = f"({raw})"
            column.server_default = DefaultClause(text(raw))


@contextmanager
def _reconciliation_sqlite_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.models.live_crypto_order import LiveCryptoOrder
    from app.models.live_reconciliation_event import LiveReconciliationEvent

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    tables = [LiveCryptoOrder.__table__, LiveReconciliationEvent.__table__]
    for table in tables:
        _fix_sqlite_server_defaults(table)
    LiveCryptoOrder.metadata.create_all(engine, tables=tables)
    try:
        with Session(engine) as session:
            yield session, _AwaitableActivationSession(session)
    finally:
        engine.dispose()


def _seed_live_crypto_order(
    session, *, live_crypto_order_id: uuid.UUID, provider: str, environment: str, product: str, provider_order_id: str | None, status: str = "PARTIALLY_FILLED"  # noqa: ANN001
):
    from app.models.live_crypto_order import LiveCryptoOrder

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    session.add(
        LiveCryptoOrder(
            live_crypto_order_id=live_crypto_order_id,
            crypto_order_preview_id=uuid.uuid4(),
            exchange_connection_id=uuid.uuid4(),
            provider=provider,
            environment=environment,
            product_id=product,
            side="buy",
            order_type="market",
            requested_quote_size=Decimal("5"),
            client_order_id=f"client-{live_crypto_order_id}",
            status=status,
            risk_event_id=None,
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=provider_order_id,
            provider_status="partially_filled",
            submitted_at=now - timedelta(minutes=10),
            acknowledged_at=now - timedelta(minutes=9),
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={},
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
    )
    session.commit()


def _seed_reconciliation_event(
    session, *, live_crypto_order_id: uuid.UUID, reconciliation_status: str, provider_order_id: str | None, sequence_number: int = 1  # noqa: ANN001
) -> uuid.UUID:
    from app.models.live_reconciliation_event import LiveReconciliationEvent

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    event_id = uuid.uuid4()
    session.add(
        LiveReconciliationEvent(
            id=event_id,
            idempotency_key=f"idem-{event_id}",
            event_hash=f"hash-{event_id}",
            live_trading_profile_id=uuid.uuid4(),
            live_crypto_order_id=live_crypto_order_id,
            capital_campaign_id=None,
            source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="order_reconciled",
            reconciliation_status=reconciliation_status,
            provider_name="kraken_spot",
            provider_order_id=provider_order_id,
            provider_fill_id=None,
            event_payload={},
            provenance={},
            immutable_contract_version="1.0.0",
            provider_recorded_at=now - timedelta(minutes=8),
            recorded_at=now - timedelta(minutes=8),
            created_at=now - timedelta(minutes=8),
        )
    )
    session.commit()
    return event_id


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_logs_full_diagnostic_detail_for_blocking_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="KRAKEN-ORDER-1", **scope)
        event_id = _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="open", provider_order_id="KRAKEN-ORDER-1")

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is True

    trigger_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_gate_triggered ")]
    assert len(trigger_records) == 1
    trigger_message = trigger_records[0].getMessage()
    assert "provider=kraken_spot" in trigger_message
    assert "environment=production" in trigger_message
    assert "product=BTC-USD" in trigger_message
    assert "matched_record_count=1" in trigger_message

    detail_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_record_detail ")]
    assert len(detail_records) == 1
    detail_message = detail_records[0].getMessage()
    assert f"reconciliation_event_id={event_id}" in detail_message
    assert f"live_crypto_order_id={order_id}" in detail_message
    assert "provider_order_id=KRAKEN-ORDER-1" in detail_message
    assert "reconciliation_status=open" in detail_message
    assert "unresolved_because=status_in_unresolved_set" in detail_message
    assert "order_status=PARTIALLY_FILLED" in detail_message
    # SQLite round-trips DateTime(timezone=True) without an offset suffix;
    # the value itself (not the tz representation, a SQLite-only artifact)
    # is what matters here.
    assert "recorded_at=2026-07-21T11:52:00" in detail_message


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_logs_nothing_when_all_resolved(caplog: pytest.LogCaptureFixture) -> None:
    """A reconciliation event in a resolved state (e.g. 'filled') must not
    trigger the gate or any diagnostic logging -- fail-closed behavior is
    scoped to genuinely unresolved states only."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="KRAKEN-ORDER-2", status="FILLED", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="filled", provider_order_id="KRAKEN-ORDER-2")

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is False
    assert not [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_")]


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_logs_every_matching_record(caplog: pytest.LogCaptureFixture) -> None:
    """Multiple unresolved records blocking the same scope must each get
    their own detail line -- not just the first one the boolean check
    happened to find via LIMIT 1."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    order_a, order_b = uuid.uuid4(), uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_a, provider_order_id="KRAKEN-ORDER-A", **scope)
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_b, provider_order_id="KRAKEN-ORDER-B", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_a, reconciliation_status="open", provider_order_id="KRAKEN-ORDER-A")
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_b, reconciliation_status="conflict", provider_order_id="KRAKEN-ORDER-B")

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is True
    trigger_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_gate_triggered ")]
    assert "matched_record_count=2" in trigger_records[0].getMessage()
    detail_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_record_detail ")]
    assert len(detail_records) == 2
    assert any("reconciliation_status=open" in r.getMessage() for r in detail_records)
    assert any("reconciliation_status=conflict" in r.getMessage() for r in detail_records)


# --- _has_unresolved_reconciliation: latest-per-order semantics ---
#
# Production evidence: a BUY that cleared every other gate was permanently
# blocked with unresolved_reconciliation_exists. Diagnostics showed 3
# matched records for one order (partially_filled, partially_filled,
# reconciliation_required) from July 18th, while the order's OWN status
# fields already read FILLED. Root cause: live_reconciliation_events is
# append-only -- reconcile_live_order_and_fills() (accounting_reconciliation.py)
# never updates or deletes a prior row, it appends a new one as the order's
# state evolves, and the SAME function call that observes a provider status
# of FILLED both sets LiveCryptoOrder.status="FILLED" and appends a new
# reconciliation_status="filled" event with a higher sequence_number. The
# gate was written to match ANY historical row in an unresolved state,
# which is permanently true for any order that was ever partially filled
# even after it fully resolved. app.services.risk.equity_evidence already
# had the correct fix for the identical status vocabulary (latest event per
# order, by max sequence_number) -- these tests prove the worker's gate now
# applies that same rule.


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_ignores_superseded_history_once_order_resolves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact production shape: an order accumulates partially_filled and
    reconciliation_required events over time, then a later reconciliation
    pass observes the provider's true FILLED state and appends a resolving
    event with a higher sequence_number. The gate must follow the order to
    its current (resolved) state, not get stuck on its own superseded
    history."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="OAXUZJ-7WRL5-NPFWYA", status="FILLED", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="partially_filled", provider_order_id="OAXUZJ-7WRL5-NPFWYA", sequence_number=1)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="partially_filled", provider_order_id="OAXUZJ-7WRL5-NPFWYA", sequence_number=2)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="reconciliation_required", provider_order_id="OAXUZJ-7WRL5-NPFWYA", sequence_number=3)
        # The later, resolving pass -- this is what LiveCryptoOrder.status
        # ending up "FILLED" implies must have happened in production.
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="filled", provider_order_id="OAXUZJ-7WRL5-NPFWYA", sequence_number=4)

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is False
    assert not [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_")]


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_still_blocks_when_latest_event_is_genuinely_unresolved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail-closed behavior must be preserved: if the LATEST event for an
    order is still unresolved (no later resolving pass has ever run), the
    gate must keep blocking exactly as before."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="K-STUCK-1", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="partially_filled", provider_order_id="K-STUCK-1", sequence_number=1)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="reconciliation_required", provider_order_id="K-STUCK-1", sequence_number=2)

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is True
    trigger_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_gate_triggered ")]
    assert "matched_record_count=1" in trigger_records[0].getMessage()
    detail_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_record_detail ")]
    assert len(detail_records) == 1
    # Only the LATEST (sequence_number=2) record should be reported, not the
    # superseded sequence_number=1 one.
    assert "reconciliation_status=reconciliation_required" in detail_records[0].getMessage()
    assert "sequence_number=2" in detail_records[0].getMessage()


@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_one_resolved_order_does_not_mask_another_stuck_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One order resolving must not hide a genuinely different, still-stuck
    order in the same scope -- latest-per-order must be evaluated
    independently for every order, not collapsed across the whole scope."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module

    resolved_order, stuck_order = uuid.uuid4(), uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")

    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=resolved_order, provider_order_id="K-RESOLVED", status="FILLED", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=resolved_order, reconciliation_status="partially_filled", provider_order_id="K-RESOLVED", sequence_number=1)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=resolved_order, reconciliation_status="filled", provider_order_id="K-RESOLVED", sequence_number=2)

        _seed_live_crypto_order(raw_session, live_crypto_order_id=stuck_order, provider_order_id="K-STUCK-2", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=stuck_order, reconciliation_status="open", provider_order_id="K-STUCK-2", sequence_number=1)

        with caplog.at_level(logging.INFO, logger="app.services.orchestration.continuous_pipeline_worker"):
            result = await worker_module._has_unresolved_reconciliation(db=db, **scope)

    assert result is True
    detail_records = [r for r in caplog.records if r.getMessage().startswith("unresolved_reconciliation_record_detail ")]
    assert len(detail_records) == 1
    assert f"live_crypto_order_id={stuck_order}" in detail_records[0].getMessage()


@pytest.mark.asyncio
async def test_claim_guard_blocks_when_latest_reconciliation_is_unresolved() -> None:
    from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")
    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="K-STUCK", **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="partially_filled", provider_order_id="K-STUCK", sequence_number=1)
        latest_id = _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="reconciliation_required", provider_order_id="K-STUCK", sequence_number=2)
        result = await db.scalar(claim_blocking_reconciliation_statement(**scope))

    assert result == latest_id


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved_status", ["filled", "canceled", "rejected"])
async def test_claim_guard_ignores_superseded_unresolved_history(resolved_status: str) -> None:
    from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

    order_id = uuid.uuid4()
    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")
    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="K-RESOLVED", status=resolved_status.upper(), **scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="partially_filled", provider_order_id="K-RESOLVED", sequence_number=1)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="reconciliation_required", provider_order_id="K-RESOLVED", sequence_number=2)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status=resolved_status, provider_order_id="K-RESOLVED", sequence_number=3)
        result = await db.scalar(claim_blocking_reconciliation_statement(**scope))

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_scope",
    [
        {"provider": "kraken_spot", "environment": "sandbox", "product": "BTC-USD"},
        {"provider": "kraken_spot", "environment": "production", "product": "ETH-USD"},
        {"provider": "coinbase", "environment": "production", "product": "BTC-USD"},
    ],
)
async def test_claim_guard_isolates_provider_environment_and_product(other_scope: dict[str, str]) -> None:
    from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

    order_id = uuid.uuid4()
    target = dict(provider="kraken_spot", environment="production", product="BTC-USD")
    with _reconciliation_sqlite_session() as (raw_session, db):
        _seed_live_crypto_order(raw_session, live_crypto_order_id=order_id, provider_order_id="OTHER-SCOPE", **other_scope)
        _seed_reconciliation_event(raw_session, live_crypto_order_id=order_id, reconciliation_status="unknown", provider_order_id="OTHER-SCOPE")
        result = await db.scalar(claim_blocking_reconciliation_statement(**target))

    assert result is None


@pytest.mark.asyncio
async def test_claim_guard_blocks_unscopable_orphaned_current_state() -> None:
    from app.services.orchestration.reconciliation_guard import claim_blocking_reconciliation_statement

    scope = dict(provider="kraken_spot", environment="production", product="BTC-USD")
    with _reconciliation_sqlite_session() as (raw_session, db):
        event_id = _seed_reconciliation_event(
            raw_session, live_crypto_order_id=None, reconciliation_status="unknown", provider_order_id="K-ORPHAN",
        )
        result = await db.scalar(claim_blocking_reconciliation_statement(**scope))

    assert result == event_id


@pytest.mark.asyncio
async def test_exit_recovery_enters_existing_pipeline_as_sell_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        package_id=uuid.uuid4(), sell_package_id=None, campaign_id=uuid.uuid4(), campaign_version=1,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
        max_notional_usd=Decimal("5"), audit_correlation_id=uuid.uuid4(),
    )
    recovery = SimpleNamespace(recovery_id=uuid.uuid4(), status="IN_PROGRESS")
    package_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(worker_module, "claim_exit_recovery_by_id", _async_return((recovery, proof)))
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _async_return(True))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _async_return(ControlledProofRiskOutcome(verdict="ALLOW", approved_notional_usd=Decimal("5"), reason_code=None, risk_event_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "get_settings", lambda: SimpleNamespace(automatic_mandate_package_activation_mandate_id=uuid.uuid4(), controlled_proof_mandate_id=uuid.uuid4()))
    monkeypatch.setattr(worker_module, "compute_controlled_proof_open_exposure_usd", _async_return(Decimal("0")))
    monkeypatch.setattr(worker_module, "resolve_controlled_proof_strategy_identity", _async_return("ma_crossover@1.0.0"))
    async def _create_decision(**kwargs):
        captured["decision_request"] = kwargs
        return uuid.uuid4()
    monkeypatch.setattr(worker_module, "create_controlled_proof_decision_record", _create_decision)
    evaluation = SimpleNamespace(authorization_result="AUTHORIZED", mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), evaluation_id=uuid.uuid4())
    async def _evaluate(*, db, request):
        captured["evaluation_request"] = request
        return evaluation
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _evaluate)

    async def _create(*, db, request):
        captured["request"] = request
        return {"package": {"package_id": str(package_id)}}
    async def _link(*, db, proof, sell_package_id, preserve_terminal_status=False):
        captured["preserve"] = preserve_terminal_status
        proof.sell_package_id = sell_package_id
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)
    monkeypatch.setattr(worker_module, "link_controlled_proof_sell_package", _link)
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _async_return(AutomaticPackageExecutionOutcome(package_id=package_id, campaign_id=proof.campaign_id, campaign_version=1, decision_record_id=uuid.uuid4(), mandate_id=evaluation.mandate_id, authorization_state="AUTHORIZED", dry_run_state="NOT_RUN", activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=False, final_reason_code="test", failed_closed=True, starting_state="READY")))

    await worker_module._attempt_operator_controlled_proof_entry(db=_FakeDB(), recovery_id=recovery.recovery_id)

    request = captured["request"]
    assert request.forced_action == "CLOSE_POSITION_PROPOSED"
    assert request.commissioning_entry_mode == "controlled_proof"
    assert request.controlled_proof_exit_recovery_id == recovery.recovery_id
    assert captured["decision_request"]["controlled_proof_exit_recovery_id"] == recovery.recovery_id
    assert captured["evaluation_request"].decision_id == request.expected_decision_record_id
    assert captured["evaluation_request"].idempotency_key == (
        f"controlled-proof-mandate-eval:{proof.proof_id}:SELL:exit-recovery:"
        f"{recovery.recovery_id}:decision:{request.expected_decision_record_id}"
    )
    assert request.idempotency_key == worker_module.hashlib.sha256(
        f"controlled-proof:{proof.proof_id}:SELL:exit-recovery:{recovery.recovery_id}".encode()
    ).hexdigest()
    assert captured["preserve"] is True
    assert proof.status == "EXPIRED"
    assert proof.terminal_verdict == "FAILED"


@pytest.mark.asyncio
async def test_exit_recovery_resumes_exact_linked_package_after_worker_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.orchestration.automatic_package_executor import AutomaticPackageExecutionOutcome

    package_id, decision_id = uuid.uuid4(), uuid.uuid4()
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        package_id=uuid.uuid4(), sell_package_id=package_id, campaign_id=uuid.uuid4(), campaign_version=1,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
    )
    recovery = SimpleNamespace(recovery_id=uuid.uuid4(), status="IN_PROGRESS")
    package = SimpleNamespace(
        package_id=package_id, decision_record_id=decision_id,
        authorization_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    class _Db(_FakeDB):
        async def get(self, model, identity):
            assert model is worker_module.CanonicalPreviewPackage and identity == package_id
            return package

    progressed = []
    monkeypatch.setattr(worker_module, "claim_exit_recovery_by_id", _async_return((recovery, proof)))
    monkeypatch.setattr(worker_module, "get_controlled_proof_view", _async_return({}))
    monkeypatch.setattr(worker_module, "refresh_exit_recovery_completion", _async_return(None))
    async def _execute(*, db, request):
        progressed.append(request)
        return AutomaticPackageExecutionOutcome(package_id=package_id, campaign_id=proof.campaign_id, campaign_version=1, decision_record_id=decision_id, mandate_id=uuid.uuid4(), authorization_state="AUTHORIZED", dry_run_state="NOT_RUN", activation_state="NOT_ACTIVATED", authority_source="MANDATE", replayed=True, final_reason_code="retryable", failed_closed=True, starting_state="READY")
    monkeypatch.setattr(worker_module, "execute_automatic_ready_package_through_activation", _execute)

    await worker_module._attempt_operator_controlled_proof_entry(db=_Db(), recovery_id=recovery.recovery_id)

    assert len(progressed) == 1
    assert progressed[0].package_id == package_id
    assert progressed[0].decision_record_id == decision_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (
            lambda: IntegrityError(
                "INSERT canonical_preview_packages", {}, Exception("uq_cpp_decision_id")
            ),
            "fresh_authority_persistence_integrity_failure",
        ),
        (lambda: LookupError("canonical_mandate_evaluation_mismatch"), "fresh_authority_evidence_validation_failure"),
        (
            lambda: InvalidRequestError("Mandate evaluation authority lineage mismatch"),
            "fresh_authority_evidence_validation_failure",
        ),
    ],
)
async def test_exit_recovery_package_failure_rolls_back_and_blocks_cleanly(
    monkeypatch: pytest.MonkeyPatch, failure, expected_reason: str,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome

    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        package_id=uuid.uuid4(), sell_package_id=None, campaign_id=uuid.uuid4(), campaign_version=1,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
        max_notional_usd=Decimal("5"), audit_correlation_id=uuid.uuid4(),
    )
    recovery = SimpleNamespace(recovery_id=uuid.uuid4(), status="IN_PROGRESS")
    db = _FakeDB()
    blocked = []
    monkeypatch.setattr(worker_module, "claim_exit_recovery_by_id", _async_return((recovery, proof)))
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _async_return(True))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _async_return(ControlledProofRiskOutcome(verdict="ALLOW", approved_notional_usd=Decimal("5"), reason_code=None, risk_event_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "get_settings", lambda: SimpleNamespace(automatic_mandate_package_activation_mandate_id=uuid.uuid4(), controlled_proof_mandate_id=uuid.uuid4()))
    monkeypatch.setattr(worker_module, "compute_controlled_proof_open_exposure_usd", _async_return(Decimal("0")))
    monkeypatch.setattr(worker_module, "resolve_controlled_proof_strategy_identity", _async_return("ma_crossover@1.0.0"))
    monkeypatch.setattr(worker_module, "create_controlled_proof_decision_record", _async_return(uuid.uuid4()))
    evaluation = SimpleNamespace(authorization_result="AUTHORIZED", mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), evaluation_id=uuid.uuid4())
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _async_return(evaluation))

    async def _insert_fails(**_kwargs):
        raise failure()
    async def _block(**kwargs):
        blocked.append(kwargs["reason"])
        recovery.status = "BLOCKED"
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _insert_fails)
    monkeypatch.setattr(worker_module, "block_exit_recovery", _block)

    await worker_module._attempt_operator_controlled_proof_entry(db=db, recovery_id=recovery.recovery_id)

    assert db.rollbacks == 1
    assert db.commits == 1
    assert recovery.status == "BLOCKED"
    assert blocked == [expected_reason]


@pytest.mark.asyncio
async def test_exit_recovery_sell_package_link_dbapi_error_logs_full_diagnostics_without_changing_outcome(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Reproduces the retryable:entry_attempt_failed:DBAPIError blocker: a
    DBAPIError raised out of link_controlled_proof_sell_package() must still
    classify/persist exactly as before (fail-closed, unchanged reason
    string), while the worker's diagnostic log line now carries the full
    underlying exception detail that the DB-facing failure_reason column
    was never designed to hold."""
    import app.services.orchestration.continuous_pipeline_worker as worker_module
    from app.services.controlled_proof import ControlledProofRiskOutcome
    from sqlalchemy.exc import DBAPIError

    class _FakeAsyncpgUniqueViolationError(Exception):
        pass

    package_id = uuid.uuid4()
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        package_id=uuid.uuid4(), sell_package_id=None, position_id="POS-777",
        campaign_id=uuid.uuid4(), campaign_version=1,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
        max_notional_usd=Decimal("5"), audit_correlation_id=uuid.uuid4(),
    )
    recovery = SimpleNamespace(recovery_id=uuid.uuid4(), status="IN_PROGRESS")
    db = _FakeDB()

    orig = _FakeAsyncpgUniqueViolationError(
        'duplicate key value violates unique constraint "uq_controlled_proof_runs_sell_package_id"'
    )
    dbapi_error = DBAPIError(
        "UPDATE controlled_proof_runs SET sell_package_id = %(sell_package_id)s WHERE proof_id = %(proof_id)s",
        {"sell_package_id": str(package_id), "proof_id": str(proof.proof_id)},
        orig,
    )

    monkeypatch.setattr(worker_module, "claim_exit_recovery_by_id", _async_return((recovery, proof)))
    monkeypatch.setattr(worker_module, "should_propose_controlled_sell", _async_return(True))
    monkeypatch.setattr(worker_module, "_load_runtime_campaign", _async_return(SimpleNamespace(paper_account_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_load_live_trading_profile_for_paper_account", _async_return(SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "_has_open_live_order", _async_return(False))
    monkeypatch.setattr(worker_module, "_has_unresolved_reconciliation", _async_return(False))
    monkeypatch.setattr(worker_module, "evaluate_controlled_proof_risk", _async_return(ControlledProofRiskOutcome(verdict="ALLOW", approved_notional_usd=Decimal("5"), reason_code=None, risk_event_id=uuid.uuid4())))
    monkeypatch.setattr(worker_module, "get_settings", lambda: SimpleNamespace(automatic_mandate_package_activation_mandate_id=uuid.uuid4(), controlled_proof_mandate_id=uuid.uuid4()))
    monkeypatch.setattr(worker_module, "compute_controlled_proof_open_exposure_usd", _async_return(Decimal("0")))
    monkeypatch.setattr(worker_module, "resolve_controlled_proof_strategy_identity", _async_return("ma_crossover@1.0.0"))
    monkeypatch.setattr(worker_module, "create_controlled_proof_decision_record", _async_return(uuid.uuid4()))
    evaluation = SimpleNamespace(authorization_result="AUTHORIZED", mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), evaluation_id=uuid.uuid4())
    monkeypatch.setattr(worker_module, "evaluate_and_record_mandate", _async_return(evaluation))

    async def _create(*, db, request):
        return {"package": {"package_id": str(package_id)}}
    monkeypatch.setattr(worker_module, "create_canonical_preview_package", _create)

    async def _link_fails(*, db, proof, sell_package_id, preserve_terminal_status=False):
        raise dbapi_error
    monkeypatch.setattr(worker_module, "link_controlled_proof_sell_package", _link_fails)

    with caplog.at_level(logging.ERROR, logger="app.services.orchestration.continuous_pipeline_worker"):
        await worker_module._attempt_operator_controlled_proof_entry(db=db, recovery_id=recovery.recovery_id)

    # Fail-closed outcome is unchanged: same classification, same rollback/commit shape.
    assert db.rollbacks == 1
    assert db.commits == 1
    assert recovery.failure_reason == "retryable:entry_attempt_failed:DBAPIError"
    assert proof.sell_package_id is None

    [record] = [r for r in caplog.records if r.getMessage().startswith("controlled_proof_entry_attempt_failed")]
    message = record.getMessage()
    assert "stage=sell_package_linking" in message
    assert f"recovery_id={recovery.recovery_id}" in message
    assert f"proof_id={proof.proof_id}" in message
    assert f"controlled_proof_run_id={proof.proof_id}" in message
    assert f"sell_package_id={package_id}" in message
    assert "position_id=POS-777" in message
    assert "exception_class=sqlalchemy.exc.DBAPIError" in message
    assert "orig_exception_class=" in message
    assert "_FakeAsyncpgUniqueViolationError" in message
    assert "uq_controlled_proof_runs_sell_package_id" in message
    assert "sql_statement=" in message
    assert "UPDATE controlled_proof_runs" in message
    assert "sql_params=" in message
    assert str(package_id) in record.getMessage()
    # The traceback of the original exception is attached to the record.
    assert record.exc_info is not None
    assert record.exc_info[1] is dbapi_error
