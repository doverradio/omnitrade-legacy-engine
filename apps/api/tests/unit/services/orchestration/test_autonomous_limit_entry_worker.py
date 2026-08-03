from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.services.orchestration.autonomous_limit_entry_worker as worker
from app.models.autonomous_limit_entry_attempt import (
    STAGE_CANCEL_REQUESTED,
    STAGE_CANCELLED,
    STAGE_FILLED,
    STAGE_OPEN,
    STAGE_PARTIALLY_FILLED,
    STAGE_PROPOSED,
    STAGE_READY,
    STAGE_RECONCILIATION_REQUIRED,
    STAGE_REJECTED,
    STAGE_REPLACED,
    STAGE_SUBMITTED,
    AutonomousLimitEntryAttempt,
)
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.entry_intelligence.decision import EntryIntelligenceCandidate
from app.services.exchange_connections.providers.base import (
    ExchangeCancelResult,
    ExchangeOrderSubmissionResult,
    ExchangeProviderFee,
    ExchangeProviderFill,
    ExchangeProviderOrder,
    ExchangeProviderRejection,
)
from app.services.risk import RiskDecisionAction
from app.services.risk.risk_engine import RiskEvaluationResult


class _FakeSession:
    """Minimal AsyncSession stand-in: real model instances are ordinary
    Python objects until added to a real session and flushed, so this only
    needs to fake the I/O surface (add/flush/scalar/get/rollback) -- no real
    database is required to test the state-machine logic itself."""

    def __init__(self, *, scalar_results: list | None = None, get_registry: dict | None = None) -> None:
        self.added: list = []
        self.flush_count = 0
        self._scalar_results = list(scalar_results or [])
        self._get_registry = get_registry or {}

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def rollback(self) -> None:
        pass

    async def scalar(self, _stmt):
        if self._scalar_results:
            return self._scalar_results.pop(0)
        return None

    async def get(self, _model, id_):
        return self._get_registry.get(id_)


def _candidate(**overrides) -> EntryIntelligenceCandidate:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    kwargs = dict(
        instrument="BTC-USD", venue="kraken_spot", side="BUY",
        signal_time=now, candle_close=now, timeframe="15m",
        campaign_id=str(uuid4()), campaign_version=1,
        strategy_identity="momentum@1.0.0", strategy_coalition="momentum@1.0.0",
        contributing_strategies=("momentum",), signal_strength="0.97",
        market_regime="TRENDING", volatility_regime=None,
        expected_holding_period_minutes=None,
        expected_exit_price=Decimal("99.95"),
        market_entry_price=Decimal("100.00"),
        maximum_profitable_entry_price=Decimal("99.92"),
        preferred_limit_price=Decimal("99.92"),
        invalidation_price=None,
        expiration_time=now + timedelta(minutes=60),
        expected_gross_edge_at_market_pct=Decimal("-0.05"),
        expected_net_edge_at_market_pct=Decimal("-0.08"),
        expected_gross_edge_at_limit_pct=Decimal("0.03"),
        expected_net_edge_at_limit_pct=Decimal("0.001"),
        confidence_sample_size=30,
        uncertainty_penalty_pct=Decimal("0.02"),
        evidence_provenance="strategy_asset_timeframe",
        approved_notional=Decimal("50.00"),
        decision="BUY_LIMIT",
        reason="bounded_limit_entry_creates_positive_expected_net_edge",
        maximum_replacement_count=1,
        minimum_repricing_interval_minutes=15,
    )
    kwargs.update(overrides)
    return EntryIntelligenceCandidate(**kwargs)


def _risk_context() -> SimpleNamespace:
    return SimpleNamespace(
        account_equity=Decimal("1000"), max_position_size_pct=Decimal("0.5"),
        start_of_day_equity=Decimal("1000"), current_equity=Decimal("1000"),
        max_daily_loss_pct=Decimal("0.1"), high_water_mark_equity=Decimal("1000"),
        max_drawdown_pct=Decimal("0.2"), consecutive_losses_on_pair=0,
        cooldown_after_losses=5, last_loss_at=None, cooldown_duration_minutes=Decimal("60"),
        evaluation_time=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        data_is_stale=False, data_has_gaps=False,
        global_kill_switch_engaged_state=False, global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False, account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True, account_kill_switch_state_observed=True,
    )


# --- propose_and_risk_evaluate_limit_entry ---


@pytest.mark.asyncio
async def test_propose_creates_ready_attempt_on_risk_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeSession(scalar_results=[None])  # no existing attempt

    monkeypatch.setattr(
        worker, "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.5")),
    )
    monkeypatch.setattr(
        worker, "persist_risk_decision",
        lambda **_kwargs: _async_result(SimpleNamespace(risk_event_id=uuid4())),
    )

    attempt = await worker.propose_and_risk_evaluate_limit_entry(
        db=db, campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD",
        environment="production", decision_record_id=uuid4(),
        candidate=_candidate(), paper_account_id=uuid4(), asset_id=uuid4(),
        asset_min_order_notional=Decimal("1"), asset_qty_step_size=None,
        asset_supports_fractional=True, risk_context=_risk_context(),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert attempt.stage == STAGE_READY
    assert attempt.risk_event_id is not None
    assert attempt in db.added


@pytest.mark.asyncio
async def test_propose_rejected_by_risk_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeSession(scalar_results=[None])

    monkeypatch.setattr(
        worker, "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.REJECT, reason_code="max_position_size_exceeded", approved_quantity=Decimal("0")),
    )
    monkeypatch.setattr(
        worker, "persist_risk_decision",
        lambda **_kwargs: _async_result(SimpleNamespace(risk_event_id=uuid4())),
    )

    attempt = await worker.propose_and_risk_evaluate_limit_entry(
        db=db, campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD",
        environment="production", decision_record_id=uuid4(),
        candidate=_candidate(), paper_account_id=uuid4(), asset_id=uuid4(),
        asset_min_order_notional=Decimal("1"), asset_qty_step_size=None,
        asset_supports_fractional=True, risk_context=_risk_context(),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert attempt.stage == STAGE_REJECTED
    assert attempt.terminal_reason == "max_position_size_exceeded"


@pytest.mark.asyncio
async def test_propose_is_idempotent_returns_existing_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = AutonomousLimitEntryAttempt(
        campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD", environment="production",
        stage=STAGE_READY, preferred_limit_price=Decimal("99.92"), maximum_profitable_entry_price=Decimal("99.92"),
        requested_base_quantity=Decimal("0.5"), approved_notional=Decimal("50"),
        expires_at=datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc),
        max_replacement_count=1, min_repricing_interval_minutes=15,
        idempotency_key="already-exists", next_attempt_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    db = _FakeSession(scalar_results=[existing])

    called = {"risk": False}

    def _should_not_be_called(**_kwargs):
        called["risk"] = True
        raise AssertionError("risk must not be re-evaluated for an already-existing attempt")

    monkeypatch.setattr(worker, "evaluate_signal_risk", _should_not_be_called)

    attempt = await worker.propose_and_risk_evaluate_limit_entry(
        db=db, campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD",
        environment="production", decision_record_id=uuid4(),
        candidate=_candidate(), paper_account_id=uuid4(), asset_id=uuid4(),
        asset_min_order_notional=Decimal("1"), asset_qty_step_size=None,
        asset_supports_fractional=True, risk_context=_risk_context(),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert attempt is existing
    assert called["risk"] is False


async def _async_result(value):
    return value


# --- advance_one_limit_entry_attempt: submit, poll, expire, cancel, replace ---


def _attempt(**overrides) -> AutonomousLimitEntryAttempt:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    kwargs = dict(
        campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD", environment="production",
        stage=STAGE_READY, preferred_limit_price=Decimal("99.92"), maximum_profitable_entry_price=Decimal("99.92"),
        requested_base_quantity=Decimal("0.5"), approved_notional=Decimal("50"),
        expires_at=now + timedelta(minutes=60), max_replacement_count=1,
        min_repricing_interval_minutes=15, idempotency_key=str(uuid4()), next_attempt_at=now,
        filled_base_quantity=Decimal("0"), replacement_count=0, retry_count=0,
    )
    kwargs.update(overrides)
    return AutonomousLimitEntryAttempt(**kwargs)


class _StubClient:
    def __init__(self, *, submission=None, lookup=None, fills=None, cancel=None) -> None:
        self._submission = submission
        self._lookup = lookup
        self._fills = fills or []
        self._cancel = cancel

    async def submit_order(self, **_kwargs):
        return self._submission

    async def lookup_order(self, **_kwargs):
        return self._lookup

    async def list_fills(self, **_kwargs):
        return self._fills

    async def cancel_order(self, **_kwargs):
        return self._cancel


def _patch_provider(monkeypatch: pytest.MonkeyPatch, client: _StubClient) -> None:
    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)


@pytest.mark.asyncio
async def test_submit_ready_attempt_transitions_to_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient(
        submission=ExchangeOrderSubmissionResult(
            classification="success",
            order=ExchangeProviderOrder(
                provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD",
                side="BUY", status="OPEN", submitted_at=datetime.now(timezone.utc), acknowledged_at=datetime.now(timezone.utc),
            ),
            rejection=None, ambiguous=None,
        )
    )
    _patch_provider(monkeypatch, client)

    attempt = _attempt(stage=STAGE_READY)
    db = _FakeSession()
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)

    assert attempt.stage == STAGE_SUBMITTED
    assert attempt.live_crypto_order_id is not None
    live_orders = [item for item in db.added if isinstance(item, LiveCryptoOrder)]
    assert len(live_orders) == 1
    assert live_orders[0].order_type == "LIMIT"
    assert live_orders[0].limit_price == attempt.preferred_limit_price
    assert live_orders[0].provider_order_id == "O-1"


@pytest.mark.asyncio
async def test_submit_rejected_by_provider_sets_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient(
        submission=ExchangeOrderSubmissionResult(
            classification="rejected", order=None,
            rejection=ExchangeProviderRejection(code="insufficient_funds", message="no funds"),
            ambiguous=None,
        )
    )
    _patch_provider(monkeypatch, client)

    attempt = _attempt(stage=STAGE_READY)
    db = _FakeSession()
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc))

    assert attempt.stage == STAGE_RECONCILIATION_REQUIRED
    assert "insufficient_funds" in attempt.terminal_reason


@pytest.mark.asyncio
async def test_poll_open_order_stays_open_and_reschedules(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        lookup=ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD",
            side="BUY", status="OPEN", submitted_at=None, acknowledged_at=None,
        ),
        fills=[],
    )
    _patch_provider(monkeypatch, client)

    now = attempt.expires_at - timedelta(minutes=30)
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)

    assert attempt.stage == STAGE_OPEN
    assert attempt.next_attempt_at > now


@pytest.mark.asyncio
async def test_poll_partial_fill_updates_stage_and_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id, requested_base_quantity=Decimal("1.0"))
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="OPEN", submitted_at=None, acknowledged_at=None),
        fills=[ExchangeProviderFill(provider_fill_id="F-1", provider_order_id="O-1", product_id="BTC-USD", size=Decimal("0.4"), price=Decimal("99.9"), fee=None, occurred_at=None)],
    )
    _patch_provider(monkeypatch, client)

    reconcile_calls: list[str] = []

    async def _fake_reconcile(**kwargs):
        reconcile_calls.append(str(kwargs["live_crypto_order_id"]))
        return {}

    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", _fake_reconcile)

    now = attempt.expires_at - timedelta(minutes=30)
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)

    assert attempt.stage == STAGE_PARTIALLY_FILLED
    assert attempt.filled_base_quantity == Decimal("0.4")
    assert len(reconcile_calls) == 1


@pytest.mark.asyncio
async def test_poll_full_fill_transitions_to_filled_and_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id, requested_base_quantity=Decimal("0.5"))
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="FILLED", submitted_at=None, acknowledged_at=None),
        fills=[ExchangeProviderFill(provider_fill_id="F-1", provider_order_id="O-1", product_id="BTC-USD", size=Decimal("0.5"), price=Decimal("99.9"), fee=None, occurred_at=None)],
    )
    _patch_provider(monkeypatch, client)

    reconcile_calls: list[str] = []

    async def _fake_reconcile(**kwargs):
        reconcile_calls.append(str(kwargs["live_crypto_order_id"]))
        return {}

    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", _fake_reconcile)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))

    assert attempt.stage == STAGE_FILLED
    assert len(reconcile_calls) == 1


@pytest.mark.asyncio
async def test_poll_expired_order_requests_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    expires_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    attempt = _attempt(stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id, expires_at=expires_at)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="OPEN", submitted_at=None, acknowledged_at=None),
        fills=[],
    )
    _patch_provider(monkeypatch, client)

    now = expires_at + timedelta(minutes=1)
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)

    assert attempt.stage == STAGE_CANCEL_REQUESTED
    assert attempt.terminal_reason == "expired"


@pytest.mark.asyncio
async def test_unrecognized_provider_status_fails_closed_to_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="UNKNOWN", submitted_at=None, acknowledged_at=None),
        fills=[],
    )
    _patch_provider(monkeypatch, client)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))

    assert attempt.stage == STAGE_RECONCILIATION_REQUIRED
    assert attempt.terminal_reason == "unknown_provider_state"


@pytest.mark.asyncio
async def test_cancel_confirmation_transitions_to_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_CANCEL_REQUESTED, live_crypto_order_id=live_order.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        cancel=ExchangeCancelResult(classification="success", provider_status="CANCELLED"),
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="CANCELLED", submitted_at=None, acknowledged_at=None),
    )
    _patch_provider(monkeypatch, client)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))

    assert attempt.stage == STAGE_CANCELLED
    assert attempt.cancel_confirmed_at is not None
    assert live_order.status == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_confirmation_fill_wins_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the order actually filled just before our cancel took effect, the
    fill must win -- never mark a filled order CANCELLED."""
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    attempt = _attempt(stage=STAGE_CANCEL_REQUESTED, live_crypto_order_id=live_order.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    client = _StubClient(
        cancel=ExchangeCancelResult(classification="already_resolved", provider_status=None),
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="FILLED", submitted_at=None, acknowledged_at=None),
    )
    _patch_provider(monkeypatch, client)
    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", lambda **_kwargs: _async_result({}))

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))

    assert attempt.stage == STAGE_FILLED


# --- replacement, bounded by maximum_profitable_entry_price and max count ---


@pytest.mark.asyncio
async def test_replacement_created_when_still_economically_justified(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = _attempt(stage=STAGE_CANCELLED, maximum_profitable_entry_price=Decimal("100.00"), replacement_count=0, max_replacement_count=1)
    db = _FakeSession()

    await worker.advance_one_limit_entry_attempt(
        db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc),
        current_reference_price=Decimal("99.50"),
    )

    assert attempt.stage == STAGE_REPLACED
    replacements = [item for item in db.added if isinstance(item, AutonomousLimitEntryAttempt)]
    assert len(replacements) == 1
    assert replacements[0].replaces_attempt_id == attempt.attempt_id
    assert replacements[0].replacement_count == 1
    assert replacements[0].preferred_limit_price <= attempt.maximum_profitable_entry_price


@pytest.mark.asyncio
async def test_replacement_never_chases_above_maximum_profitable_entry_price(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = _attempt(stage=STAGE_CANCELLED, maximum_profitable_entry_price=Decimal("100.00"), replacement_count=0, max_replacement_count=1)
    db = _FakeSession()

    # current market price has risen ABOVE the economic bound -- no replacement.
    await worker.advance_one_limit_entry_attempt(
        db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc),
        current_reference_price=Decimal("100.50"),
    )

    assert attempt.stage == STAGE_REPLACED  # this attempt is still superseded/terminal
    replacements = [item for item in db.added if isinstance(item, AutonomousLimitEntryAttempt)]
    assert len(replacements) == 0  # but no new (chasing) attempt was created


@pytest.mark.asyncio
async def test_replacement_bounded_by_max_replacement_count(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = _attempt(stage=STAGE_CANCELLED, maximum_profitable_entry_price=Decimal("100.00"), replacement_count=1, max_replacement_count=1)
    db = _FakeSession()

    await worker.advance_one_limit_entry_attempt(
        db=db, attempt=attempt, now=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc),
        current_reference_price=Decimal("99.50"),
    )

    replacements = [item for item in db.added if isinstance(item, AutonomousLimitEntryAttempt)]
    assert len(replacements) == 0


# --- restart recovery ---


@pytest.mark.asyncio
async def test_restart_recovery_resumes_polling_from_persisted_stage_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a crash between submission and the first poll: the only
    thing that survives is the persisted attempt row (stage=SUBMITTED,
    live_crypto_order_id set) and the LiveCryptoOrder row -- no in-memory
    state. advance_one_limit_entry_attempt must resume correctly from that
    alone."""
    live_order = LiveCryptoOrder(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    fresh_process_attempt = AutonomousLimitEntryAttempt(
        campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD", environment="production",
        stage=STAGE_SUBMITTED, preferred_limit_price=Decimal("99.92"), maximum_profitable_entry_price=Decimal("99.92"),
        requested_base_quantity=Decimal("0.5"), approved_notional=Decimal("50"),
        expires_at=datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc), max_replacement_count=1,
        min_repricing_interval_minutes=15, idempotency_key=str(uuid4()),
        next_attempt_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        live_crypto_order_id=live_order.live_crypto_order_id,
        filled_base_quantity=Decimal("0"), replacement_count=0, retry_count=0,
    )
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})
    client = _StubClient(
        lookup=ExchangeProviderOrder(provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY", status="OPEN", submitted_at=None, acknowledged_at=None),
        fills=[],
    )
    _patch_provider(monkeypatch, client)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=fresh_process_attempt, now=datetime(2026, 8, 3, 12, 5, tzinfo=timezone.utc))

    assert fresh_process_attempt.stage == STAGE_OPEN


# --- custody boundary: this module must never establish custody ---


def test_module_never_imports_or_calls_custody_establishment() -> None:
    """Explicit, honest boundary: a filled BUY_LIMIT reconciles and
    accounts for the position via reconcile_live_order_and_fills, but this
    module must never itself transition anything into
    AutonomousPositionCustody -- that requires execution-claim lineage this
    module does not build (see module docstring)."""
    assert "establish_buy_custody" not in dir(worker)
    assert not hasattr(worker, "AutonomousPositionCustody")
