from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import inspect
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services import canonical_campaign_binding as binding


_CURRENT_OLD_ACCOUNT_ID = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")


class _Tx:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db
        self._runtime_before = None
        self._runtime_exchange_before = None
        self._profile_before = None
        self._added_before = 0
        self._flushes_before = 0

    async def __aenter__(self):
        self._db._in_transaction = True
        if getattr(self._db, "runtime", None) is not None:
            self._runtime_before = getattr(self._db.runtime, "paper_account_id", None)
            self._runtime_exchange_before = getattr(self._db.runtime, "exchange", None)
        if getattr(self._db, "live_profile", None) is not None:
            self._profile_before = getattr(self._db.live_profile, "paper_account_id", None)
        self._added_before = len(self._db.added)
        self._flushes_before = self._db.flushes
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            if getattr(self._db, "runtime", None) is not None:
                self._db.runtime.paper_account_id = self._runtime_before
                self._db.runtime.exchange = self._runtime_exchange_before
            if getattr(self._db, "live_profile", None) is not None:
                self._db.live_profile.paper_account_id = self._profile_before
            del self._db.added[self._added_before :]
            self._db.flushes = self._flushes_before
        _ = exc_type, exc, tb
        self._db._in_transaction = False
        return False


class _FakeDb:
    def __init__(self, *, runtime, live_profile) -> None:
        self.runtime = runtime
        self.live_profile = live_profile
        self.added: list[object] = []
        self.flushes = 0
        self._in_transaction = False

    def begin(self):
        return _Tx(self)

    def in_transaction(self) -> bool:
        return self._in_transaction

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1
        for item in self.added:
            if item.__class__.__name__ == "PaperAccount" and getattr(item, "id", None) is None:
                item.id = uuid4()

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is None:
            return None
        if entity.__name__ == "CapitalCampaign":
            return self.runtime
        if entity.__name__ == "LiveTradingProfile":
            return self.live_profile
        return None


def _request(*, confirm: bool = False, idempotency_key: str | None = None) -> binding.CanonicalProvingAccountTransitionRequest:
    old_account_id = _CURRENT_OLD_ACCOUNT_ID
    return binding.CanonicalProvingAccountTransitionRequest(
        campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        campaign_version=1,
        runtime_campaign_id=2,
        live_trading_profile_id=UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d"),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        old_paper_account_id=old_account_id,
        actor="operator:human",
        confirm=confirm,
        idempotency_key=idempotency_key,
    )


def _definition() -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        version=1,
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        metadata_evidence={},
        updated_at=datetime.now(timezone.utc),
    )


def _runtime(*, paper_account_id: UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        uuid=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        status="READY",
        paper_account_id=paper_account_id,
        exchange="kraken_spot",
        definition_campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        definition_version=1,
        updated_at=datetime.now(timezone.utc),
    )


def _profile(*, paper_account_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d"),
        paper_account_id=paper_account_id,
        provenance_metadata={"provider": "kraken_spot", "exchange_environment": "production"},
        updated_at=datetime.now(timezone.utc),
    )


def _old_account(account_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=account_id,
        owner_user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        asset_class="crypto",
        is_active=True,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("4.33159379773015"),
    )


def _connection(*, usd_available: Decimal = Decimal("25"), total_equity: Decimal = Decimal("62")) -> SimpleNamespace:
    return SimpleNamespace(
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        status="connected",
        account_status="active",
        credentials_valid=True,
        balances=[
            {"currency": "USD", "available": format(usd_available, "f"), "reserved": "2", "total": format(usd_available + Decimal("2"), "f")},
            {"currency": "BTC", "available": "0.0001", "reserved": "0", "total": "0.0001", "price_usd": "100000"},
            {"currency": "ETH", "available": "0.01", "reserved": "0", "total": "0.01", "price_usd": "2500"},
        ],
        total_equity_usd=format(total_equity, "f"),
        last_successful_sync_at=datetime.now(timezone.utc),
        last_readiness_verdict="READY_FOR_OPERATOR_REVIEW",
        last_readiness_report=[{"code": "usd_balance_retrieved", "status": "pass", "explanation": "ok"}],
    )


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


def _configure_happy_path(monkeypatch: pytest.MonkeyPatch, *, old_account_id: UUID) -> None:
    global _CURRENT_OLD_ACCOUNT_ID
    _CURRENT_OLD_ACCOUNT_ID = old_account_id
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition()))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition()))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(paper_account_id=old_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_old_account(old_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_old_account(old_account_id)))
    monkeypatch.setattr(binding, "_count_account_trade_rows", _async_return(43))
    monkeypatch.setattr(binding, "_count_account_open_positions", _async_return(3))
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))
    monkeypatch.setattr(binding, "_count_unknown_provider_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_active_canonical_packages", _async_return(0))
    monkeypatch.setattr(binding, "_count_active_proving_activations", _async_return(0))
    monkeypatch.setattr(binding, "_latest_proving_transition_audit", _async_return(None))
    monkeypatch.setattr(binding, "_latest_proving_transition_audit_for_update", _async_return(None))


@pytest.mark.asyncio
async def test_contaminated_old_account_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert result.snapshot["contamination_summary"]["shared_historical_state"] is True


@pytest.mark.asyncio
async def test_clean_dedicated_account_preview_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_account_trade_rows", _async_return(0))
    monkeypatch.setattr(binding, "_count_account_open_positions", _async_return(0))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert result.ready is True


@pytest.mark.asyncio
async def test_preview_performs_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_account_trade_rows", _async_return(0))
    monkeypatch.setattr(binding, "_count_account_open_positions", _async_return(0))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert db.flushes == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_execute_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    with pytest.raises(PermissionError, match="confirm=true"):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=False, idempotency_key="x1"))


@pytest.mark.asyncio
async def test_exact_idempotent_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    audit = SimpleNamespace(after_state={"idempotency_key": "same-key", "new_paper_account_id": str(uuid4())})
    monkeypatch.setattr(binding, "_latest_proving_transition_audit_for_update", _async_return(audit))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="same-key"))
    assert result.idempotent is True
    assert result.changed is False


@pytest.mark.asyncio
async def test_exact_idempotent_retry_creates_no_duplicate_account_or_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))

    first = await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="stable-key"))
    audit = SimpleNamespace(after_state={"idempotency_key": "stable-key", "new_paper_account_id": first.after["new_paper_account_id"]})
    monkeypatch.setattr(binding, "_latest_proving_transition_audit_for_update", _async_return(audit))

    second = await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="stable-key"))

    assert first.changed is True
    assert second.changed is False
    assert second.idempotent is True
    assert first.after["new_paper_account_id"] == second.after["new_paper_account_id"]
    assert sum(1 for item in db.added if item.__class__.__name__ == "PaperAccount") == 1
    assert sum(1 for item in db.added if item.__class__.__name__ == "AuditLog") == 1


@pytest.mark.asyncio
async def test_conflicting_retry_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    audit = SimpleNamespace(after_state={"idempotency_key": "first-key", "new_paper_account_id": str(uuid4())})
    monkeypatch.setattr(binding, "_latest_proving_transition_audit_for_update", _async_return(audit))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    with pytest.raises(PermissionError, match="conflicting retry"):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="second-key"))


@pytest.mark.asyncio
async def test_old_account_remains_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    old = _old_account(old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(old))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="ok-1"))
    assert old.id == old_account_id
    assert old.current_cash_balance == Decimal("4.33159379773015")


@pytest.mark.asyncio
async def test_new_account_has_no_historical_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert result.snapshot["proposed_new_account"]["historical_trade_count"] == 0


@pytest.mark.asyncio
async def test_new_account_has_no_open_paper_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert result.snapshot["proposed_new_account"]["open_position_count"] == 0


@pytest.mark.asyncio
async def test_canonical_campaign_binding_changes_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    runtime = _runtime(paper_account_id=old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=runtime, live_profile=_profile(paper_account_id=old_account_id))
    await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="ok-2"))
    assert runtime.paper_account_id != old_account_id
    assert runtime.exchange == "kraken_spot"


@pytest.mark.asyncio
async def test_live_profile_binding_changes_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    profile = _profile(paper_account_id=old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=profile)
    await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="ok-3"))
    assert profile.paper_account_id != old_account_id


@pytest.mark.asyncio
async def test_provider_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(1))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "clean_reconciliation_state" in result.blockers


@pytest.mark.asyncio
async def test_unknown_provider_order_blocks_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_unknown_provider_orders", _async_return(1))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "no_unknown_provider_orders" in result.blockers


@pytest.mark.asyncio
async def test_active_package_blocks_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_active_canonical_packages", _async_return(1))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "no_active_canonical_package" in result.blockers


@pytest.mark.asyncio
async def test_active_activation_blocks_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_active_proving_activations", _async_return(1))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "no_active_proving_activation" in result.blockers


@pytest.mark.asyncio
async def test_initial_cash_cannot_exceed_provider_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(
        binding,
        "_proposed_new_account_balances",
        lambda **_kwargs: (Decimal("30"), Decimal("30"), Decimal("30"), Decimal("0")),
    )
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    with pytest.raises(PermissionError, match="initial_cash_not_exceed_provider_available"):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="bad-cash"))


@pytest.mark.asyncio
async def test_exact_5_risk_availability_after_valid_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(_connection(usd_available=Decimal("5.00"), total_equity=Decimal("25"))))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    codes = {item.code: item.passed for item in result.checks}
    assert codes["risk_liquid_cash_supports_exact_5"] is True


def test_transition_contains_no_order_submission_calls() -> None:
    source = inspect.getsource(binding.transition_canonical_proving_account)
    assert "create_order(" not in source
    assert "submit" not in source


@pytest.mark.asyncio
async def test_failed_execute_leaves_original_bindings_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    runtime = _runtime(paper_account_id=old_account_id)
    profile = _profile(paper_account_id=old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(1))
    db = _FakeDb(runtime=runtime, live_profile=profile)
    with pytest.raises(PermissionError):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="blocked"))
    assert runtime.paper_account_id == old_account_id
    assert profile.paper_account_id == old_account_id


@pytest.mark.asyncio
async def test_failure_after_account_creation_before_runtime_binding_rolls_back_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingRuntime:
        def __init__(self, old_account_id: UUID) -> None:
            self.id = 2
            self.uuid = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
            self.status = "READY"
            self._paper_account_id = old_account_id
            self.exchange = "kraken_spot"
            self.definition_campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
            self.definition_version = 1
            self.updated_at = datetime.now(timezone.utc)

        @property
        def paper_account_id(self) -> UUID:
            return self._paper_account_id

        @paper_account_id.setter
        def paper_account_id(self, value: UUID) -> None:
            if value != self._paper_account_id:
                raise RuntimeError("explode-before-runtime-binding")
            self._paper_account_id = value

    old_account_id = uuid4()
    runtime = _ExplodingRuntime(old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=runtime, live_profile=_profile(paper_account_id=old_account_id))

    with pytest.raises(RuntimeError, match="explode-before-runtime-binding"):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="explode-1"))

    assert runtime.paper_account_id == old_account_id
    assert db.live_profile.paper_account_id == old_account_id
    assert sum(1 for item in db.added if item.__class__.__name__ == "PaperAccount") == 0
    assert sum(1 for item in db.added if item.__class__.__name__ == "AuditLog") == 0


@pytest.mark.asyncio
async def test_failure_after_runtime_binding_before_profile_binding_rolls_back_both(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingProfile:
        def __init__(self, old_account_id: UUID) -> None:
            self.id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")
            self._paper_account_id = old_account_id
            self.provenance_metadata = {"provider": "kraken_spot", "exchange_environment": "production"}
            self.updated_at = datetime.now(timezone.utc)

        @property
        def paper_account_id(self) -> UUID:
            return self._paper_account_id

        @paper_account_id.setter
        def paper_account_id(self, value: UUID) -> None:
            if value != self._paper_account_id:
                raise RuntimeError("explode-before-profile-binding")
            self._paper_account_id = value

    old_account_id = uuid4()
    runtime = _runtime(paper_account_id=old_account_id)
    profile = _ExplodingProfile(old_account_id)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=runtime, live_profile=profile)

    with pytest.raises(RuntimeError, match="explode-before-profile-binding"):
        await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="explode-2"))

    assert runtime.paper_account_id == old_account_id
    assert profile.paper_account_id == old_account_id
    assert sum(1 for item in db.added if item.__class__.__name__ == "PaperAccount") == 0
    assert sum(1 for item in db.added if item.__class__.__name__ == "AuditLog") == 0


@pytest.mark.asyncio
async def test_concurrent_attempts_resolve_to_single_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SerializedTx(_Tx):
        async def __aenter__(self):
            await self._db._tx_lock.acquire()
            return await super().__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await super().__aexit__(exc_type, exc, tb)
            finally:
                self._db._tx_lock.release()

    class _SerializedDb(_FakeDb):
        def __init__(self, *, runtime, live_profile) -> None:
            super().__init__(runtime=runtime, live_profile=live_profile)
            self._tx_lock = asyncio.Lock()

        def begin(self):
            return _SerializedTx(self)

    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _SerializedDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))

    first = await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="race-key"))
    audit = SimpleNamespace(after_state={"idempotency_key": "race-key", "new_paper_account_id": first.after["new_paper_account_id"]})
    monkeypatch.setattr(binding, "_latest_proving_transition_audit_for_update", _async_return(audit))

    async def _call():
        return await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="race-key"))

    second, third = await asyncio.gather(_call(), _call())

    assert first.changed is True
    assert second.idempotent is True
    assert third.idempotent is True
    assert sum(1 for item in db.added if item.__class__.__name__ == "PaperAccount") == 1
    assert sum(1 for item in db.added if item.__class__.__name__ == "AuditLog") == 1


@pytest.mark.asyncio
async def test_complete_audit_evidence_created(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.transition_canonical_proving_account(db=db, request=_request(confirm=True, idempotency_key="evidence-1"))
    assert result.audit_created is True
    assert any(item.__class__.__name__ == "AuditLog" for item in db.added)
    assert "provider_balance_evidence" in result.readiness.snapshot


@pytest.mark.asyncio
async def test_missing_evidence_timestamp_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.last_successful_sync_at = None
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "provider_balance_evidence_fresh" in result.blockers


@pytest.mark.asyncio
async def test_stale_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.last_successful_sync_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "get_settings", lambda: SimpleNamespace(canonical_proving_provider_evidence_max_age_seconds=30))
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "provider_balance_evidence_fresh" in result.blockers


@pytest.mark.asyncio
async def test_fresh_evidence_passes_freshness_check(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.last_successful_sync_at = datetime.now(timezone.utc)
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    codes = {item.code: item.passed for item in result.checks}
    assert codes["provider_balance_evidence_fresh"] is True


@pytest.mark.asyncio
async def test_unsupported_readiness_verdict_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.last_readiness_verdict = "READY_FOR_PREVIEW"
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "readiness_verdict_accepted" in result.blockers


@pytest.mark.asyncio
async def test_credentials_invalid_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.credentials_valid = False
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "exchange_connection_credentials_valid" in result.blockers


@pytest.mark.asyncio
async def test_provider_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.provider = "coinbase_advanced"
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "exchange_connection_provider_matches" in result.blockers


@pytest.mark.asyncio
async def test_environment_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.environment = "sandbox"
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "exchange_connection_environment_matches" in result.blockers


@pytest.mark.asyncio
async def test_usd_btc_and_other_holdings_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    evidence = result.snapshot["provider_balance_evidence"]
    assert evidence["usd_available"] == "25"
    assert evidence["usd_reserved"] == "2"
    assert evidence["usd_total"] == "27"
    assert evidence["btc_total_quantity"] == "0.0001"
    assert evidence["btc_market_value"] == "10.0000"
    assert any(item["currency"] == "ETH" for item in evidence["other_holdings"])


@pytest.mark.asyncio
async def test_unpriced_holdings_are_explicit_and_block(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    conn.balances.append({"currency": "SOL", "available": "1", "reserved": "0", "total": "1"})
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert "SOL" in result.snapshot["provider_balance_evidence"]["unpriced_holdings"]
    assert "no_unpriced_holdings" in result.blockers


@pytest.mark.asyncio
async def test_cash_initialization_uses_only_usd_available(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    proposed = result.snapshot["proposed_new_account"]
    assert proposed["starting_balance"] == "25"
    assert proposed["current_cash_balance"] == "25"


@pytest.mark.asyncio
async def test_old_account_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    req = replace(_request(), old_paper_account_id=uuid4())
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=req)
    assert "runtime_old_account_matches_requested" in result.blockers


@pytest.mark.asyncio
async def test_runtime_profile_disagreement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_old = uuid4()
    profile_old = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=runtime_old)
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(paper_account_id=profile_old)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_old_account(profile_old)))
    db = _FakeDb(runtime=_runtime(paper_account_id=runtime_old), live_profile=_profile(paper_account_id=profile_old))
    req = replace(_request(), old_paper_account_id=runtime_old)
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=req)
    assert "runtime_profile_old_account_agree" in result.blockers


@pytest.mark.asyncio
async def test_missing_and_whitespace_actor_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    bad = replace(_request(), actor="")
    with pytest.raises(PermissionError, match="actor is required"):
        await binding.inspect_canonical_proving_account_transition(db=db, request=bad)
    whitespace = replace(_request(), actor="   ")
    with pytest.raises(PermissionError, match="actor is required"):
        await binding.transition_canonical_proving_account(db=db, request=replace(whitespace, confirm=True, idempotency_key="actor-check"))


@pytest.mark.asyncio
async def test_reserved_usd_remains_evidence_only(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    result = await binding.inspect_canonical_proving_account_transition(db=db, request=_request())
    assert result.snapshot["provider_balance_evidence"]["usd_reserved"] == "2"
    assert result.snapshot["proposed_new_account"]["current_cash_balance"] == "25"


@pytest.mark.asyncio
async def test_changed_expected_evidence_between_preview_and_execute_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    old_account_id = uuid4()
    conn = _connection()
    _configure_happy_path(monkeypatch, old_account_id=old_account_id)
    monkeypatch.setattr(binding, "_latest_exchange_connection", _async_return(conn))
    db = _FakeDb(runtime=_runtime(paper_account_id=old_account_id), live_profile=_profile(paper_account_id=old_account_id))
    req = replace(
        _request(confirm=True, idempotency_key="evidence-drift"),
        expected_evidence_source_id=str(uuid4()),
        expected_evidence_observed_at=conn.last_successful_sync_at.isoformat(),
    )
    with pytest.raises(PermissionError, match="expected_evidence_source_matches_current"):
        await binding.transition_canonical_proving_account(db=db, request=req)
