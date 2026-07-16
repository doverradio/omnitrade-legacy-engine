from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.operator_cli.service as service


class _Tx:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db
        self._definition_before = None
        self._added_before = 0

    async def __aenter__(self) -> "_Tx":
        await self._db._tx_lock.acquire()
        self._definition_before = (
            self._db.definition.maximum_position_size,
            self._db.definition.maximum_total_exposure,
            self._db.definition.updated_at,
        )
        self._added_before = len(self._db.added)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._db.definition.maximum_position_size = self._definition_before[0]
            self._db.definition.maximum_total_exposure = self._definition_before[1]
            self._db.definition.updated_at = self._definition_before[2]
            del self._db.added[self._added_before :]
            await self._db.rollback()
        self._db._tx_lock.release()
        _ = exc, tb
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.connection = SimpleNamespace(
            exchange_connection_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            updated_at=datetime.now(timezone.utc),
        )
        self.definition = SimpleNamespace(
            campaign_id=uuid4(),
            version=1,
            maximum_position_size=Decimal("10"),
            maximum_total_exposure=Decimal("12"),
            minimum_position_size=Decimal("5"),
            maximum_open_positions=2,
            deployed_capital=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        self.runtime = SimpleNamespace(
            id=77,
            uuid=self.definition.campaign_id,
            definition_version=1,
        )
        self.active_package_count = 0
        self.active_activation_count = 0
        self.non_compliant_activation_count = 0
        self.open_live_order_count = 0
        self.unresolved_reconciliation_count = 0
        self.audits: list[SimpleNamespace] = []
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self._tx_lock = asyncio.Lock()

    def begin(self) -> _Tx:
        return _Tx(self)

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        sql = str(statement)
        if entity is not None:
            if entity.__name__ == "ExchangeConnection":
                return self.connection
            if entity.__name__ == "CapitalCampaignDefinition":
                return self.definition
            if entity.__name__ == "CapitalCampaign":
                return self.runtime
            if entity.__name__ == "AuditLog":
                return self.audits[-1] if self.audits else None
        if "FROM canonical_preview_packages" in sql:
            return self.active_package_count
        if "FROM canonical_proving_activations" in sql and "no_leverage IS false" in sql:
            return self.non_compliant_activation_count
        if "FROM canonical_proving_activations" in sql and "activation_state" in sql:
            return self.active_activation_count
        if "FROM live_crypto_orders" in sql:
            return self.open_live_order_count
        if "FROM live_reconciliation_events" in sql:
            return self.unresolved_reconciliation_count
        return None

    def add(self, obj) -> None:
        self.added.append(obj)
        if obj.__class__.__name__ == "AuditLog":
            self.audits.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            await self._db.rollback()
        return False


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_refresh(**kwargs):
        assert kwargs["exchange_connection_id"] == db.connection.exchange_connection_id
        return SimpleNamespace(
            exchange_connection_id=db.connection.exchange_connection_id,
            provider="kraken_spot",
            environment="production",
            status="connected",
            readiness=SimpleNamespace(verdict="READY_FOR_OPERATOR_REVIEW", checked_at=datetime.now(timezone.utc)),
            total_equity_usd=Decimal("62.10"),
            last_successful_sync_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(service, "_refresh_exchange_balances", _fake_refresh)

    payload = await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")
    assert payload["provider"] == "kraken_spot"
    assert payload["invariants"]["no_order_submission"] is True


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_stale_to_fresh_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    refreshed_at = datetime.now(timezone.utc)

    async def _fake_refresh(**_kwargs):
        return SimpleNamespace(
            exchange_connection_id=db.connection.exchange_connection_id,
            provider="kraken_spot",
            environment="production",
            status="connected",
            readiness=SimpleNamespace(verdict="READY_FOR_OPERATOR_REVIEW", checked_at=refreshed_at),
            total_equity_usd=Decimal("63.50"),
            last_successful_sync_at=refreshed_at,
        )

    monkeypatch.setattr(service, "_refresh_exchange_balances", _fake_refresh)

    payload = await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")
    assert payload["readiness_verdict"] == "READY_FOR_OPERATOR_REVIEW"
    assert payload["last_successful_sync_at"] == refreshed_at.isoformat()


@pytest.mark.asyncio
async def test_refresh_provider_balance_evidence_provider_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _boom(**_kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(service, "_refresh_exchange_balances", _boom)

    with pytest.raises(RuntimeError, match="provider failure"):
        await service.refresh_provider_balance_evidence(provider="kraken_spot", environment="production", actor="operator:human")

    assert db.rollbacks == 1


def test_refresh_provider_balance_evidence_contains_no_order_path_calls() -> None:
    source = service.refresh_provider_balance_evidence.__code__.co_names
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source
    assert "execute_internal_crypto_fill" not in source


@pytest.mark.asyncio
async def test_proving_cap_preview_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_preview(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
    )

    assert payload["ready"] is True
    assert payload["before"]["maximum_open_positions"] == 2
    assert payload["proposed"]["maximum_open_positions"] == 1
    assert payload["proposed"]["maximum_position_size"] == "5"
    assert payload["proposed"]["maximum_total_exposure"] == "5"
    assert db.commits == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_proving_cap_execute_requires_confirm_and_idempotency_key() -> None:
    with pytest.raises(PermissionError, match="confirm=true"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=uuid4(),
            campaign_version=1,
            actor="operator:human",
            confirm=False,
            idempotency_key="key-1",
        )
    with pytest.raises(PermissionError, match="idempotency_key"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=uuid4(),
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_applies_only_cap_fields_and_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_execute(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
        actor="operator:human",
        confirm=True,
        idempotency_key="cap-1",
    )

    assert payload["changed"] is True
    assert payload["before"]["maximum_open_positions"] == 2
    assert payload["after"]["maximum_open_positions"] == 1
    assert db.definition.maximum_position_size == Decimal("5")
    assert db.definition.maximum_total_exposure == Decimal("5")
    assert db.definition.minimum_position_size == Decimal("5")
    assert db.definition.maximum_open_positions == 1
    assert any(getattr(item, "action", "") == "capital_campaign.proving_cap_transition" for item in db.added)


@pytest.mark.asyncio
async def test_proving_cap_execute_exact_retry_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    existing = SimpleNamespace(after_state={"idempotency_key": "cap-1", "maximum_position_size": "5"})
    db.audits.append(existing)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.canonical_proving_cap_transition_execute(
        campaign_id=db.definition.campaign_id,
        campaign_version=1,
        actor="operator:human",
        confirm=True,
        idempotency_key="cap-1",
    )

    assert payload["idempotent"] is True
    assert payload["changed"] is False


@pytest.mark.asyncio
async def test_proving_cap_execute_conflicting_retry_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    existing = SimpleNamespace(after_state={"idempotency_key": "cap-1", "maximum_position_size": "5"})
    db.audits.append(existing)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(PermissionError, match="conflicting retry blocked"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-2",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_blockers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.active_package_count = 1
    db.active_activation_count = 1
    db.open_live_order_count = 1
    db.unresolved_reconciliation_count = 1
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(PermissionError, match="proving cap transition prerequisites failed"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-blocked",
        )


@pytest.mark.asyncio
async def test_proving_cap_execute_rollback_restores_old_values_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    def _boom_add(obj):
        if getattr(obj, "action", "") == "capital_campaign.proving_cap_transition":
            raise RuntimeError("audit write failed")
        db.added.append(obj)

    monkeypatch.setattr(db, "add", _boom_add)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-rollback",
        )

    assert db.definition.maximum_position_size == Decimal("10")
    assert db.definition.maximum_total_exposure == Decimal("12")


@pytest.mark.asyncio
async def test_proving_cap_execute_concurrent_attempts_single_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _call():
        return await service.canonical_proving_cap_transition_execute(
            campaign_id=db.definition.campaign_id,
            campaign_version=1,
            actor="operator:human",
            confirm=True,
            idempotency_key="cap-race",
        )

    first, second = await asyncio.gather(_call(), _call())

    changed_count = sum(1 for item in [first, second] if item["changed"])
    idempotent_count = sum(1 for item in [first, second] if item["idempotent"])
    assert changed_count == 1
    assert idempotent_count == 1


def test_proving_cap_execute_contains_no_order_path_calls() -> None:
    source = service.canonical_proving_cap_transition_execute.__code__.co_names
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source
    assert "execute_internal_crypto_fill" not in source
