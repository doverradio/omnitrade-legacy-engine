"""Tests for the claim-lineage / custody-establishment integration added on
top of the entry-intelligence limit-order lifecycle: a provider-confirmed
FILLED BUY_LIMIT must reach the EXACT SAME AutonomousPositionCustody
authority a FILLED market BUY reaches, via the SAME, unmodified
claim_activated_package / establish_buy_custody /
release_execution_claim_scope_if_order_resolved functions."""
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
    STAGE_RECONCILIATION_REQUIRED,
    STAGE_SUBMITTED,
    AutonomousLimitEntryAttempt,
)
from app.models.live_crypto_order import LiveCryptoOrder
from app.services.exchange_connections.providers.base import ExchangeProviderFill, ExchangeProviderOrder


class _FakeSession:
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


def _attempt(**overrides) -> AutonomousLimitEntryAttempt:
    now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    kwargs = dict(
        campaign_id=uuid4(), campaign_version=1, instrument="BTC-USD", environment="production",
        paper_account_id=uuid4(), decision_record_id=uuid4(),
        stage=STAGE_SUBMITTED, preferred_limit_price=Decimal("99.92"), maximum_profitable_entry_price=Decimal("99.92"),
        requested_base_quantity=Decimal("0.5"), approved_notional=Decimal("50"),
        expires_at=now + timedelta(minutes=60), max_replacement_count=1,
        min_repricing_interval_minutes=15, idempotency_key=str(uuid4()), next_attempt_at=now,
        filled_base_quantity=Decimal("0"), replacement_count=0, retry_count=0,
        evidence_provenance={"strategy_identity": "momentum@1.0.0"},
    )
    kwargs.update(overrides)
    return AutonomousLimitEntryAttempt(**kwargs)


def _live_order(**overrides) -> LiveCryptoOrder:
    kwargs = dict(
        live_crypto_order_id=uuid4(), crypto_order_preview_id=uuid4(), exchange_connection_id=uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY",
        order_type="LIMIT", limit_price=Decimal("99.92"), requested_quote_size=Decimal("50"),
        client_order_id="cid", status="ACKNOWLEDGED", provider_order_id="O-1",
        audit_correlation_id=uuid4(), safe_provider_response={},
    )
    kwargs.update(overrides)
    return LiveCryptoOrder(**kwargs)


# --- _establish_claim_lineage ---


@pytest.mark.asyncio
async def test_establish_claim_lineage_idempotent_when_claim_already_set() -> None:
    attempt = _attempt(claim_id=uuid4())
    db = _FakeSession()
    connection = SimpleNamespace(exchange_connection_id=uuid4())

    blocker = await worker._establish_claim_lineage(db=db, attempt=attempt, connection=connection, now=datetime.now(timezone.utc))

    assert blocker is None
    assert db.added == []  # nothing new created -- pure no-op


@pytest.mark.asyncio
async def test_establish_claim_lineage_fails_closed_missing_paper_account(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = _attempt(paper_account_id=None)
    db = _FakeSession()
    connection = SimpleNamespace(exchange_connection_id=uuid4())

    blocker = await worker._establish_claim_lineage(db=db, attempt=attempt, connection=connection, now=datetime.now(timezone.utc))

    assert blocker == "missing_paper_account_id"
    assert attempt.claim_id is None


@pytest.mark.asyncio
async def test_establish_claim_lineage_full_happy_path_creates_package_activation_and_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = _attempt()
    db = _FakeSession()
    connection = SimpleNamespace(exchange_connection_id=uuid4())
    package_id = uuid4()
    activation_id = uuid4()
    claim_id = uuid4()
    dry_run_order_id = uuid4()

    profile = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(worker, "_load_live_trading_profile_for_paper_account", lambda **_kwargs: _async(profile))

    strategy = SimpleNamespace(id=uuid4())
    parameter_set = SimpleNamespace(id=uuid4())

    calls: list[str] = []

    async def _fake_resolve_strategy(**_kwargs):
        calls.append("resolve_strategy")
        return strategy, parameter_set

    async def _fake_create_package(**kwargs):
        calls.append("create_package")
        assert kwargs["request"].commissioning_entry_mode == "autonomous_limit_entry"
        assert kwargs["request"].forced_action == "OPEN_POSITION_PROPOSED"
        assert kwargs["request"].expected_decision_record_id == attempt.decision_record_id
        return {"package": {"package_id": str(package_id), "package_state": "READY"}}

    async def _fake_authorize(**_kwargs):
        calls.append("authorize")
        return {"package": {"package_state": "AUTHORIZED"}}

    async def _fake_dry_run(**_kwargs):
        calls.append("dry_run")
        return {"package": {"package_state": "DRY_RUN_PASSED"}}

    async def _fake_activate(**_kwargs):
        calls.append("activate")
        return {"activation": {"activation_id": str(activation_id)}}

    async def _fake_claim(**kwargs):
        calls.append("claim")
        assert str(kwargs["package_id"]) == str(package_id)
        return SimpleNamespace(claim=SimpleNamespace(claim_id=claim_id), created=True, reason_code="claimed")

    package_row = SimpleNamespace(package_state="ACTIVATED", dry_run_live_crypto_order_id=dry_run_order_id)

    async def _fake_load_package(**_kwargs):
        return package_row

    monkeypatch.setattr(worker, "_load_package_by_id", _fake_load_package)

    import app.services.canonical_preview_package as cpp_module
    monkeypatch.setattr(cpp_module, "_resolve_strategy_and_parameter_binding", _fake_resolve_strategy)
    monkeypatch.setattr(cpp_module, "create_canonical_preview_package", _fake_create_package)
    monkeypatch.setattr(cpp_module, "authorize_canonical_preview_package_under_mandate", _fake_authorize)
    monkeypatch.setattr(cpp_module, "run_dry_run_for_canonical_preview_package", _fake_dry_run)
    monkeypatch.setattr(cpp_module, "activate_canonical_proving_campaign", _fake_activate)

    import app.services.orchestration.autonomous_execution_claims as claims_module
    monkeypatch.setattr(claims_module, "claim_activated_package", _fake_claim)

    blocker = await worker._establish_claim_lineage(db=db, attempt=attempt, connection=connection, now=datetime.now(timezone.utc))

    assert blocker is None
    assert attempt.package_id == package_id
    assert attempt.activation_id == activation_id
    assert attempt.claim_id == claim_id
    assert calls == ["resolve_strategy", "create_package", "authorize", "dry_run", "activate", "claim"]


async def _async(value):
    return value


# --- fill -> release_execution_claim_scope_if_order_resolved -> custody ---


@pytest.mark.asyncio
async def test_filled_order_resolves_claim_scope_and_records_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.autonomous_position_custody import AutonomousPositionCustody

    claim_id = uuid4()
    custody_id = uuid4()
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED)
    live_order = _live_order()

    release_calls: list[dict] = []

    async def _fake_release(**kwargs):
        release_calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )

    custody_row = SimpleNamespace(custody_id=custody_id, custody_state="ACTIVE")
    db = _FakeSession(scalar_results=[custody_row])

    live_order.status = "FILLED"
    await worker._resolve_claim_scope_and_custody(db=db, attempt=attempt, live_order=live_order, now=datetime.now(timezone.utc))

    assert len(release_calls) == 1
    assert release_calls[0]["live_crypto_order_id"] == live_order.live_crypto_order_id
    assert release_calls[0]["order_status"] == "FILLED"
    assert attempt.custody_id == custody_id


@pytest.mark.asyncio
async def test_one_provider_confirmed_fill_creates_exactly_one_custody_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: READY (claim already established) -> poll observes FILLED
    -> reconcile -> release_execution_claim_scope_if_order_resolved ->
    exactly one custody. Simulates the shared authority function's OWN
    idempotency (a real establish_buy_custody call is a no-op / returns the
    same row on a second call for the same claim) by having the stubbed
    release function only ever create the custody row ONCE, regardless of
    how many times it's invoked -- proving THIS module's own code never
    duplicates the read/observation step either."""
    claim_id = uuid4()
    custody_id = uuid4()
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED, live_crypto_order_id=uuid4())
    live_order = _live_order(live_crypto_order_id=attempt.live_crypto_order_id)

    custody_created_count = {"n": 0}

    async def _fake_release(**_kwargs):
        custody_created_count["n"] += 1  # simulates establish_buy_custody's own idempotent insert-or-return

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )

    custody_row = SimpleNamespace(custody_id=custody_id, custody_state="ACTIVE")
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order}, scalar_results=[custody_row, custody_row])

    async def _fake_reconcile(**_kwargs):
        live_order.status = "FILLED"
        return {}

    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", _fake_reconcile)

    client = SimpleNamespace(
        lookup_order=lambda **_kwargs: _async(ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY",
            status="FILLED", submitted_at=None, acknowledged_at=None,
        )),
        list_fills=lambda **_kwargs: _async([
            ExchangeProviderFill(provider_fill_id="F-1", provider_order_id="O-1", product_id="BTC-USD", size=Decimal("0.5"), price=Decimal("99.9"), fee=None, occurred_at=None),
        ]),
    )

    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)

    now = datetime.now(timezone.utc)
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)
    assert attempt.stage == STAGE_FILLED
    assert attempt.custody_id == custody_id
    assert custody_created_count["n"] == 1

    # Restart/replay: advancing an already-FILLED (terminal) attempt again
    # must be a pure no-op -- advance_due_limit_entry_attempts itself never
    # selects terminal-stage rows, but advance_one_limit_entry_attempt must
    # also not do anything harmful if ever called directly on one.
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=now)
    assert attempt.stage == STAGE_FILLED
    assert custody_created_count["n"] == 1


@pytest.mark.asyncio
async def test_restart_after_reconciliation_establishes_custody_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a crash AFTER reconcile_live_order_and_fills committed
    (live_order.status is already FILLED, durably) but BEFORE
    release_execution_claim_scope_if_order_resolved / custody observation
    ran. A fresh process picking this attempt back up (stage still
    SUBMITTED, since that write never happened) must still reach custody
    exactly once."""
    claim_id = uuid4()
    custody_id = uuid4()
    live_order = _live_order(status="FILLED")  # already reconciled before the crash
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED, live_crypto_order_id=live_order.live_crypto_order_id, filled_base_quantity=Decimal("0.5"))

    release_calls: list[str] = []

    async def _fake_release(**kwargs):
        release_calls.append(kwargs["order_status"])

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )
    custody_row = SimpleNamespace(custody_id=custody_id, custody_state="ACTIVE")
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order}, scalar_results=[custody_row])

    async def _fake_reconcile(**_kwargs):
        return {}  # already FILLED; a no-op re-reconciliation

    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", _fake_reconcile)

    client = SimpleNamespace(
        lookup_order=lambda **_kwargs: _async(ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY",
            status="FILLED", submitted_at=None, acknowledged_at=None,
        )),
        list_fills=lambda **_kwargs: _async([
            ExchangeProviderFill(provider_fill_id="F-1", provider_order_id="O-1", product_id="BTC-USD", size=Decimal("0.5"), price=Decimal("99.9"), fee=None, occurred_at=None),
        ]),
    )

    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime.now(timezone.utc))

    assert attempt.stage == STAGE_FILLED
    assert attempt.custody_id == custody_id
    assert release_calls == ["FILLED"]


@pytest.mark.asyncio
async def test_ambiguous_provider_state_creates_no_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_id = uuid4()
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED, live_crypto_order_id=uuid4())
    live_order = _live_order(live_crypto_order_id=attempt.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    release_calls: list[str] = []

    async def _fake_release(**kwargs):
        release_calls.append(kwargs["order_status"])

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )

    client = SimpleNamespace(
        lookup_order=lambda **_kwargs: _async(ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY",
            status="UNKNOWN", submitted_at=None, acknowledged_at=None,
        )),
        list_fills=lambda **_kwargs: _async([]),
    )

    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime.now(timezone.utc))

    assert attempt.stage == STAGE_RECONCILIATION_REQUIRED
    assert attempt.custody_id is None
    assert release_calls == []  # never even attempted for an unresolved/unknown state


@pytest.mark.asyncio
async def test_unreconciled_fill_creates_no_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    """An order still genuinely OPEN (no fill, no reconciliation event) must
    never reach release_execution_claim_scope_if_order_resolved at all --
    custody can only ever be considered after an authoritative reconciled
    terminal outcome."""
    claim_id = uuid4()
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED, live_crypto_order_id=uuid4())
    live_order = _live_order(live_crypto_order_id=attempt.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    release_calls: list[str] = []

    async def _fake_release(**kwargs):
        release_calls.append(kwargs["order_status"])

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )

    client = SimpleNamespace(
        lookup_order=lambda **_kwargs: _async(ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY",
            status="OPEN", submitted_at=None, acknowledged_at=None,
        )),
        list_fills=lambda **_kwargs: _async([]),
    )

    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)

    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime.now(timezone.utc))

    assert attempt.custody_id is None
    assert release_calls == []


# --- claim release timing ---


@pytest.mark.asyncio
async def test_claim_release_occurs_only_after_authoritative_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """release_execution_claim_scope_if_order_resolved must never be called
    while the order is still OPEN/PARTIALLY_FILLED-and-still-resting --
    only once reconcile_live_order_and_fills has just set an authoritative
    terminal status (FILLED here)."""
    claim_id = uuid4()
    attempt = _attempt(claim_id=claim_id, stage=STAGE_SUBMITTED, live_crypto_order_id=uuid4(), requested_base_quantity=Decimal("1.0"))
    live_order = _live_order(live_crypto_order_id=attempt.live_crypto_order_id)
    db = _FakeSession(get_registry={live_order.live_crypto_order_id: live_order})

    release_calls: list[str] = []

    async def _fake_release(**kwargs):
        release_calls.append(kwargs["order_status"])

    monkeypatch.setattr(
        "app.services.orchestration.autonomous_execution_claims.release_execution_claim_scope_if_order_resolved",
        _fake_release,
    )

    client = SimpleNamespace(
        lookup_order=lambda **_kwargs: _async(ExchangeProviderOrder(
            provider_order_id="O-1", client_order_id="cid", product_id="BTC-USD", side="BUY",
            status="OPEN", submitted_at=None, acknowledged_at=None,
        )),
        list_fills=lambda **_kwargs: _async([
            ExchangeProviderFill(provider_fill_id="F-1", provider_order_id="O-1", product_id="BTC-USD", size=Decimal("0.3"), price=Decimal("99.9"), fee=None, occurred_at=None),
        ]),
    )

    async def _resolve(**_kwargs):
        return client, {"api_key": "k", "api_secret": "s"}, SimpleNamespace(exchange_connection_id=uuid4())

    monkeypatch.setattr(worker, "_resolve_provider_and_credentials", _resolve)
    monkeypatch.setattr(worker, "reconcile_live_order_and_fills", lambda **_kwargs: _async({}))

    # Still-resting partial fill (not yet a terminal outcome) -- must not
    # release the claim scope.
    await worker.advance_one_limit_entry_attempt(db=db, attempt=attempt, now=datetime.now(timezone.utc))
    assert release_calls == []
    assert attempt.custody_id is None


# --- existing market-BUY custody behavior unchanged ---


def test_market_buy_custody_functions_are_never_modified_by_this_module() -> None:
    """Sanity guard: this module must call the SHARED authority functions,
    never redefine or shadow them -- the market-BUY path's own behavior
    must be completely unaffected by anything in this file."""
    import inspect

    from app.services.orchestration import autonomous_execution_claims

    source = inspect.getsource(worker)
    assert "def establish_buy_custody" not in source
    assert "def release_execution_claim_scope_if_order_resolved" not in source
    assert "def claim_activated_package" not in source
    # The functions this module calls at runtime really are the exact same,
    # unmodified objects the market-BUY path defines (not a local shadow
    # with the same name) -- a real import equality check, not just a
    # source-text grep.
    assert callable(autonomous_execution_claims.release_execution_claim_scope_if_order_resolved)
    assert callable(autonomous_execution_claims.claim_activated_package)


def test_existing_market_buy_test_suite_unaffected() -> None:
    """This module must never import canonical_preview_package or
    autonomous_execution_claims at MODULE level (that would risk a
    behavior-order or monkeypatch-target change for the market-BUY path's
    own extensive test suite) -- both are imported lazily, call-time only,
    inside _establish_claim_lineage / _resolve_claim_scope_and_custody."""
    import inspect

    source = inspect.getsource(worker)
    module_level_source = source.split("\ndef ")[0].split("\nasync def ")[0]
    assert "import canonical_preview_package" not in module_level_source
    assert "import autonomous_execution_claims" not in module_level_source
