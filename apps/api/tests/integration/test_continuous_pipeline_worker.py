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
from app.services.strategy_roster.decision_aggregator import AGGREGATE_STRATEGY_SLUG


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
async def test_automatic_ready_package_path_never_calls_authorize_activate_dryrun_or_provider_submit(monkeypatch: pytest.MonkeyPatch) -> None:
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

    await worker_module._attempt_automatic_ready_package_creation(db=object(), orchestration_payload=_automatic_payload(cycle))

    assert called["authorize"] == 0
    assert called["activate"] == 0
    assert called["dry_run"] == 0
    assert called["provider_submit"] == 0


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

    async def _ready_package_attempted(*, db, orchestration_payload):
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
