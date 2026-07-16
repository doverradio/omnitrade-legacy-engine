from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import ast
from pathlib import Path

import pytest

from app.services import canonical_campaign_binding as binding


class _NullTx:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db

    async def __aenter__(self):
        self._db._in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        self._db._in_transaction = False
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0
        self.begin_calls = 0
        self._in_transaction = False

    def begin(self):
        self.begin_calls += 1
        return _NullTx(self)

    def in_transaction(self) -> bool:
        return self._in_transaction

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def scalar(self, _statement):
        return None


def _definition(*, campaign_id: UUID, version: int) -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        deployed_capital=Decimal("0"),
        current_campaign_equity=Decimal("25"),
    )


def _runtime(*, campaign_id: UUID, paper_account_id: UUID | None, exchange: str = "kraken_spot") -> SimpleNamespace:
    return SimpleNamespace(
        id=22,
        uuid=campaign_id,
        status="DRAFT",
        paper_account_id=paper_account_id,
        exchange=exchange,
        definition_campaign_id=campaign_id,
        definition_version=1,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _legacy_runtime(*, campaign_id: UUID, paper_account_id: UUID | None, exchange: str = "kraken_spot", status: str = "READY") -> SimpleNamespace:
    item = _runtime(campaign_id=campaign_id, paper_account_id=paper_account_id, exchange=exchange)
    item.status = status
    item.id = 11
    return item


def _paper_account(account_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=account_id,
        asset_class="crypto",
        is_active=True,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
    )


def _profile(profile_id: UUID, paper_account_id: UUID, *, provider: str = "kraken_spot", environment: str = "production") -> SimpleNamespace:
    return SimpleNamespace(
        id=profile_id,
        paper_account_id=paper_account_id,
        operating_mode="paper",
        lifecycle_state="pending_approval",
        approval_state="pending",
        provenance_metadata={"provider": provider, "exchange_environment": environment},
    )


def _connection(*, provider: str = "kraken_spot", environment: str = "production") -> SimpleNamespace:
    return SimpleNamespace(
        exchange_connection_id=uuid4(),
        provider=provider,
        environment=environment,
        credentials_valid=True,
        created_at=datetime.now(timezone.utc),
    )


def _asset(*, exchange: str = "kraken_spot") -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), symbol="BTC", exchange=exchange, is_active=True, created_at=datetime.now(timezone.utc))


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_updates_runtime_row_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None)

    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    result = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert result.idempotent is False
    assert runtime.paper_account_id == paper_account_id
    assert runtime.exchange == "kraken_spot"
    assert runtime.current_equity == Decimal("25")
    assert runtime.starting_capital == Decimal("25")
    assert len(db.added) == 1
    assert db.added[0].__class__.__name__ == "AuditLog"


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_is_idempotent_on_exact_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=paper_account_id)

    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    first = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )
    second = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert first.idempotent is True
    assert second.idempotent is True
    assert first.before == first.after
    assert second.before == second.after
    assert db.added == []
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_rejects_conflicting_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()

    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([_runtime(campaign_id=UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542"), paper_account_id=paper_account_id)]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    with pytest.raises(PermissionError, match="canonical campaign binding prerequisites failed"):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_rejects_wrong_version_or_relationship(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    monkeypatch.setattr(binding, "_load_definition", _async_return(None))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(None))
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    with pytest.raises(PermissionError):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=2,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_rejects_open_order_or_reconciliation_uncertainty(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(1))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(1))

    with pytest.raises(PermissionError):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_autobegun_session_reuses_existing_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    db._in_transaction = True
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None, exchange=None)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    result = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert db.begin_calls == 0


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_fails_closed_when_runtime_already_bound_to_different_account_or_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    runtime = _runtime(
        campaign_id=campaign_id,
        paper_account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        exchange="coinbase",
    )

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    with pytest.raises(PermissionError, match="canonical campaign binding prerequisites failed"):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_rollback_on_exception_reverts_fields_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _RollbackAwareTx:
        def __init__(self, db: "_FailingBindDb") -> None:
            self._db = db

        async def __aenter__(self):
            self._db._in_transaction = True
            self._db._paper_before_tx = self._db.runtime.paper_account_id
            self._db._exchange_before_tx = self._db.runtime.exchange
            self._db._added_before_tx = len(self._db.added)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is not None:
                self._db.runtime.paper_account_id = self._db._paper_before_tx
                self._db.runtime.exchange = self._db._exchange_before_tx
                del self._db.added[self._db._added_before_tx :]
            self._db._in_transaction = False
            _ = exc, tb
            return False

    class _FailingBindDb(_FakeDb):
        def __init__(self, runtime: SimpleNamespace) -> None:
            super().__init__()
            self.runtime = runtime
            self._paper_before_tx = None
            self._exchange_before_tx = None
            self._added_before_tx = 0

        def begin(self):
            self.begin_calls += 1
            return _RollbackAwareTx(self)

        async def flush(self) -> None:
            self.flushes += 1
            raise RuntimeError("flush failed")

    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None, exchange=None)
    db = _FailingBindDb(runtime)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    with pytest.raises(RuntimeError, match="flush failed"):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )

    assert runtime.paper_account_id is None
    assert runtime.exchange is None
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_retry_is_idempotent_without_duplicate_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None, exchange=None)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    first = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )
    second = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert first.changed is True
    assert second.changed is False
    assert second.idempotent is True
    assert sum(1 for item in db.added if getattr(item, "action", "") == "capital_campaign.bind_runtime") == 1


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_concurrent_style_retries_resolve_to_single_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _SerializedTx:
        def __init__(self, db: "_SerializedDb") -> None:
            self._db = db

        async def __aenter__(self):
            await self._db.tx_lock.acquire()
            self._db._in_transaction = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._db._in_transaction = False
            self._db.tx_lock.release()
            _ = exc_type, exc, tb
            return False

    class _SerializedDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.tx_lock = asyncio.Lock()

        def begin(self):
            self.begin_calls += 1
            return _SerializedTx(self)

    db = _SerializedDb()
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None, exchange=None)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    async def _call() -> binding.BindingMutationResult:
        return await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )

    first, second = await asyncio.gather(_call(), _call())

    assert sum(1 for item in [first, second] if item.changed) == 1
    assert sum(1 for item in db.added if getattr(item, "action", "") == "capital_campaign.bind_runtime") == 1


@pytest.mark.asyncio
async def test_bind_canonical_campaign_runtime_archived_legacy_requirement_via_conflict_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    runtime = _runtime(campaign_id=campaign_id, paper_account_id=None, exchange=None)
    active_legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_load_runtime_for_update", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(live_profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset()))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([active_legacy]))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))

    with pytest.raises(PermissionError, match="canonical campaign binding prerequisites failed"):
        await binding.bind_canonical_campaign_runtime(
            db=db,
            request=binding.CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


def test_binding_module_does_not_call_provider_order_submission() -> None:
    module_path = Path(__file__).resolve().parents[3] / "app" / "services" / "canonical_campaign_binding.py"
    tree = ast.parse(module_path.read_text(), filename=str(module_path))

    called_names = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Attribute) and node.func.attr) or isinstance(node.func, ast.Name))
    }

    assert "submit_order" not in called_names
    assert "create_order" not in called_names
    assert "get_exchange_provider" not in called_names


@pytest.mark.asyncio
async def test_fetch_canonical_campaign_binding_audit_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _Scalars(self._rows)

    class _AuditDb(_FakeDb):
        async def execute(self, _statement):
            row = SimpleNamespace(
                actor="operator:human",
                action="capital_campaign.bind_runtime",
                before_state={"paper_account_id": None, "exchange": None},
                after_state={"paper_account_id": "905a408c-7d8e-4fc7-ad3b-9ff637005d73", "exchange": "kraken_spot"},
                created_at=datetime.now(timezone.utc),
            )
            return _Result([row])

    db = _AuditDb()
    payload = await binding.fetch_canonical_campaign_binding_audit(db=db, campaign_id=campaign_id, limit=5)

    assert payload["campaign_id"] == str(campaign_id)
    assert payload["total"] == 1
    assert db.added == []
    assert db.flushes == 0
    assert db.begin_calls == 0


@pytest.mark.asyncio
async def test_legacy_transition_execute_archives_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=True,
                blockers=[],
                checks=[],
                snapshot={"legacy_campaign": {"status": "READY"}},
            )
        ),
    )

    result = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert result.idempotent is False
    assert legacy.status == "ARCHIVED"
    assert len(db.added) == 1
    assert db.added[0].action == "capital_campaign.transition_to_successor"
    assert db.flushes == 1
    assert db.begin_calls == 1


@pytest.mark.asyncio
async def test_legacy_transition_execute_is_idempotent_when_already_superseded(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="ARCHIVED")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=["legacy_already_superseded_by_requested_successor"],
                checks=[],
                snapshot={
                    "legacy_campaign": {"status": "ARCHIVED"},
                    "latest_transition_audit": {
                        "after_state": {
                            "successor_campaign_id": str(canonical_id),
                            "successor_campaign_version": 1,
                        }
                    },
                },
            )
        ),
    )

    result = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is False
    assert result.idempotent is True
    assert db.added == []


@pytest.mark.asyncio
async def test_legacy_transition_execute_autobegun_session_reuses_existing_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    db._in_transaction = True
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=True,
                blockers=[],
                checks=[],
                snapshot={"legacy_campaign": {"status": "READY"}},
            )
        ),
    )

    result = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert db.begin_calls == 0


@pytest.mark.asyncio
async def test_legacy_transition_execute_fails_closed_for_conflicting_successor(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="ARCHIVED")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=["legacy_already_superseded_by_different_successor"],
                checks=[],
                snapshot={
                    "legacy_campaign": {"status": "ARCHIVED"},
                    "latest_transition_audit": {
                        "after_state": {
                            "successor_campaign_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                            "successor_campaign_version": 1,
                        }
                    },
                },
            )
        ),
    )

    with pytest.raises(PermissionError, match="legacy transition prerequisites failed"):
        await binding.transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=binding.LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_id,
                canonical_campaign_id=canonical_id,
                canonical_campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_inspect_legacy_transition_blocks_on_open_orders_positions_and_pending_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")))
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
        return _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime_router)
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(1))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(2))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(1))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(1))
    monkeypatch.setattr(binding, "_load_transition_conflicting_campaigns", _async_return([]))

    readiness = await binding.inspect_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=False,
        ),
    )

    assert readiness.ready is False
    assert "legacy_no_open_provider_order" in readiness.blockers
    assert "legacy_clean_reconciliation_state" in readiness.blockers
    assert "legacy_no_open_live_position" in readiness.blockers
    assert "legacy_no_pending_accounting_closure" in readiness.blockers


@pytest.mark.asyncio
async def test_inspect_legacy_transition_allows_unbound_canonical_binding_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=None, exchange=None)
    canonical.status = "DRAFT"

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset(exchange="kraken_spot")))
    monkeypatch.setattr(binding, "_load_transition_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))

    readiness = await binding.inspect_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=False,
        ),
    )

    assert readiness.ready is True
    assert "canonical_paper_account_unbound_or_matches" not in readiness.blockers
    assert "canonical_exchange_unbound_or_matches" not in readiness.blockers
    assert "canonical_successor_eligible_for_binding" not in readiness.blockers


@pytest.mark.asyncio
async def test_inspect_legacy_transition_fails_closed_on_conflicting_canonical_account_or_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(
        campaign_id=canonical_id,
        paper_account_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        exchange="coinbase",
    )
    canonical.status = "DRAFT"

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset(exchange="kraken_spot")))
    monkeypatch.setattr(binding, "_load_transition_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))

    readiness = await binding.inspect_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=False,
        ),
    )

    assert readiness.ready is False
    assert "canonical_paper_account_unbound_or_matches" in readiness.blockers
    assert "canonical_exchange_unbound_or_matches" in readiness.blockers
    assert "canonical_successor_eligible_for_binding" in readiness.blockers


@pytest.mark.asyncio
async def test_readiness_and_execution_share_identical_prerequisite_evaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=None, exchange=None)

    calls: list[str] = []

    async def _load_runtime(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    async def _eval(*, db, request, legacy, canonical, canonical_definition):
        calls.append("eval")
        return binding.LegacyCampaignTransitionReadinessResult(
            ready=True,
            blockers=[],
            checks=[],
            snapshot={"legacy_campaign": {"status": str(legacy.status if legacy is not None else "UNKNOWN")}},
        )

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime)
    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime)
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_inspect_legacy_campaign_transition_locked", _eval)

    readiness = await binding.inspect_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=False,
        ),
    )
    assert readiness.ready is True

    result = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert calls == ["eval", "eval"]


@pytest.mark.asyncio
async def test_readiness_true_guarantees_execution_prereqs_on_unchanged_state_and_canonical_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=None, exchange=None)
    canonical.status = "DRAFT"

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset(exchange="kraken_spot")))
    monkeypatch.setattr(binding, "_load_transition_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))

    readiness = await binding.inspect_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=False,
        ),
    )
    assert readiness.ready is True

    transition = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert transition.changed is True
    assert canonical.paper_account_id is None
    assert canonical.exchange is None


@pytest.mark.asyncio
async def test_subsequent_canonical_bind_succeeds_after_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=None, exchange=None)
    canonical.status = "DRAFT"

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_paper_account_for_update", _async_return(_paper_account(paper_account_id)))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_connection()))
    monkeypatch.setattr(binding, "_load_asset", _async_return(_asset(exchange="kraken_spot")))
    monkeypatch.setattr(binding, "_load_transition_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_load_conflicting_campaigns", _async_return([]))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_live_orders", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))

    transition = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )
    assert transition.changed is True
    assert canonical.paper_account_id is None
    assert canonical.exchange is None

    bind_result = await binding.bind_canonical_campaign_runtime(
        db=db,
        request=binding.CanonicalCampaignBindingRequest(
            campaign_id=canonical_id,
            campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )
    assert bind_result.changed is True
    assert canonical.paper_account_id == paper_account_id
    assert canonical.exchange == "kraken_spot"


@pytest.mark.asyncio
async def test_legacy_transition_rollback_restores_previous_status(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _RollbackDb(_FakeDb):
        async def scalar(self, _statement):
            return SimpleNamespace(
                after_state={"successor_campaign_id": str(canonical_id), "successor_campaign_version": 1},
                before_state={"status": "READY"},
            )

    db = _RollbackDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="ARCHIVED")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=[],
                checks=[],
                snapshot={},
            )
        ),
    )

    result = await binding.rollback_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert result.idempotent is False
    assert legacy.status == "READY"
    assert len(db.added) == 1
    assert db.added[0].action == "capital_campaign.transition_rollback"
    assert db.begin_calls == 1


@pytest.mark.asyncio
async def test_legacy_transition_rollback_rejects_after_canonical_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _RollbackDb(_FakeDb):
        async def scalar(self, _statement):
            return SimpleNamespace(
                after_state={"successor_campaign_id": str(canonical_id), "successor_campaign_version": 1},
                before_state={"status": "READY"},
            )

    db = _RollbackDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="ARCHIVED")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(1))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=[],
                checks=[],
                snapshot={},
            )
        ),
    )

    with pytest.raises(PermissionError, match="rollback blocked after canonical campaign activity"):
        await binding.rollback_legacy_campaign_transition(
            db=db,
            request=binding.LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_id,
                canonical_campaign_id=canonical_id,
                canonical_campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_legacy_transition_rollback_autobegun_session_reuses_existing_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _RollbackDb(_FakeDb):
        async def scalar(self, _statement):
            return SimpleNamespace(
                after_state={"successor_campaign_id": str(canonical_id), "successor_campaign_version": 1},
                before_state={"status": "READY"},
            )

    db = _RollbackDb()
    db._in_transaction = True
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="ARCHIVED")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_router(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_router)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_pending_accounting_closure_for_campaign", _async_return(0))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=[],
                checks=[],
                snapshot={},
            )
        ),
    )

    result = await binding.rollback_legacy_campaign_transition(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert db.begin_calls == 0


@pytest.mark.asyncio
async def test_legacy_transition_retry_is_idempotent_without_duplicate_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    async def _readiness(*, db, request, legacy, canonical, canonical_definition):
        if legacy is not None and legacy.status == "ARCHIVED":
            return binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=["legacy_already_superseded_by_requested_successor"],
                checks=[],
                snapshot={
                    "legacy_campaign": {"status": "ARCHIVED"},
                    "latest_transition_audit": {
                        "after_state": {
                            "successor_campaign_id": str(request.canonical_campaign_id),
                            "successor_campaign_version": request.canonical_campaign_version,
                        }
                    },
                },
            )
        return binding.LegacyCampaignTransitionReadinessResult(
            ready=True,
            blockers=[],
            checks=[],
            snapshot={"legacy_campaign": {"status": "READY"}},
        )

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_inspect_legacy_campaign_transition_locked", _readiness)

    first = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )
    second = await binding.transition_legacy_campaign_to_canonical_successor(
        db=db,
        request=binding.LegacyCampaignTransitionRequest(
            legacy_campaign_id=legacy_id,
            canonical_campaign_id=canonical_id,
            canonical_campaign_version=1,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            actor="operator:human",
            confirm=True,
        ),
    )

    assert first.changed is True
    assert first.idempotent is False
    assert second.changed is False
    assert second.idempotent is True
    assert sum(1 for item in db.added if getattr(item, "action", "") == "capital_campaign.transition_to_successor") == 1


@pytest.mark.asyncio
async def test_legacy_transition_rollback_on_exception_reverts_status_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _RollbackAwareTx:
        def __init__(self, db: "_FailingDb") -> None:
            self._db = db

        async def __aenter__(self):
            self._db._in_transaction = True
            if self._db.legacy is not None:
                self._db._legacy_status_before_tx = self._db.legacy.status
            self._db._added_before_tx = len(self._db.added)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is not None and self._db.legacy is not None:
                self._db.legacy.status = self._db._legacy_status_before_tx
                del self._db.added[self._db._added_before_tx :]
            self._db._in_transaction = False
            _ = exc, tb
            return False

    class _FailingDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.legacy: SimpleNamespace | None = None
            self._legacy_status_before_tx = None
            self._added_before_tx = 0

        def begin(self):
            self.begin_calls += 1
            return _RollbackAwareTx(self)

        async def flush(self) -> None:
            self.flushes += 1
            raise RuntimeError("flush failed")

    db = _FailingDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)
    db.legacy = legacy

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(
        binding,
        "_inspect_legacy_campaign_transition_locked",
        _async_return(
            binding.LegacyCampaignTransitionReadinessResult(
                ready=True,
                blockers=[],
                checks=[],
                snapshot={"legacy_campaign": {"status": "READY"}},
            )
        ),
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        await binding.transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=binding.LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_id,
                canonical_campaign_id=canonical_id,
                canonical_campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )

    assert legacy.status == "READY"
    assert len(db.added) == 0


def test_binding_module_does_not_call_live_approval_or_capital_mutation_services() -> None:
    module_path = Path(__file__).resolve().parents[3] / "app" / "services" / "canonical_campaign_binding.py"
    tree = ast.parse(module_path.read_text(), filename=str(module_path))

    called_names = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Attribute) and node.func.attr) or isinstance(node.func, ast.Name))
    }

    assert "record_live_approval_checkpoint" not in called_names
    assert "revoke_live_approval" not in called_names
    assert "suspend_live_approval" not in called_names
    assert "activate_venue_commission_run" not in called_names


@pytest.mark.asyncio
async def test_legacy_transition_concurrent_style_retries_resolve_to_single_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    canonical_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    legacy = _legacy_runtime(campaign_id=legacy_id, paper_account_id=paper_account_id, status="READY")
    canonical = _runtime(campaign_id=canonical_id, paper_account_id=paper_account_id)
    gate = asyncio.Lock()

    async def _load_runtime_for_update(*, db, campaign_id):
        if campaign_id == legacy_id:
            async with gate:
                await asyncio.sleep(0)
                return legacy
        if campaign_id == canonical_id:
            return canonical
        return None

    async def _readiness(*, db, request, legacy, canonical, canonical_definition):
        if legacy is not None and legacy.status == "ARCHIVED":
            return binding.LegacyCampaignTransitionReadinessResult(
                ready=False,
                blockers=["legacy_already_superseded_by_requested_successor"],
                checks=[],
                snapshot={
                    "legacy_campaign": {"status": "ARCHIVED"},
                    "latest_transition_audit": {
                        "after_state": {
                            "successor_campaign_id": str(request.canonical_campaign_id),
                            "successor_campaign_version": request.canonical_campaign_version,
                        }
                    },
                },
            )
        return binding.LegacyCampaignTransitionReadinessResult(
            ready=True,
            blockers=[],
            checks=[],
            snapshot={"legacy_campaign": {"status": "READY"}},
        )

    monkeypatch.setattr(binding, "_load_runtime_for_update", _load_runtime_for_update)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_definition(campaign_id=canonical_id, version=1)))
    monkeypatch.setattr(binding, "_inspect_legacy_campaign_transition_locked", _readiness)

    async def _call() -> binding.LegacyCampaignTransitionMutationResult:
        return await binding.transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=binding.LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_id,
                canonical_campaign_id=canonical_id,
                canonical_campaign_version=1,
                paper_account_id=paper_account_id,
                live_trading_profile_id=profile_id,
                provider="kraken_spot",
                environment="production",
                product_id="BTC-USD",
                actor="operator:human",
                confirm=True,
            ),
        )

    first, second = await asyncio.gather(_call(), _call())

    assert sum(1 for item in [first, second] if item.changed) == 1
    assert sum(1 for item in db.added if getattr(item, "action", "") == "capital_campaign.transition_to_successor") == 1


def _status_definition(*, campaign_id: UUID, version: int, status: str = "DRAFT") -> SimpleNamespace:
    item = _definition(campaign_id=campaign_id, version=version)
    item.status = status
    item.allowed_instruments = ["BTC-USD"]
    item.allowed_venues = ["kraken_spot"]
    item.maximum_open_positions = 1
    item.minimum_position_size = Decimal("5")
    item.maximum_position_size = Decimal("5")
    item.maximum_total_exposure = Decimal("5")
    item.updated_at = datetime.now(timezone.utc)
    return item


def _status_runtime(*, campaign_id: UUID, runtime_campaign_id: int, paper_account_id: UUID, status: str = "DRAFT") -> SimpleNamespace:
    item = _runtime(campaign_id=campaign_id, paper_account_id=paper_account_id)
    item.id = runtime_campaign_id
    item.status = status
    return item


def _status_connection() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        status="connected",
        last_readiness_verdict="READY_FOR_OPERATOR_REVIEW",
        last_successful_sync_at=now,
        last_verified_at=now,
        updated_at=now,
        balances=[{"currency": "USD", "available": "25", "reserved": "0", "total": "25"}],
    )


def _status_request(
    *,
    campaign_id: UUID,
    paper_account_id: UUID,
    profile_id: UUID,
    idempotency_key: str | None,
    confirm: bool,
    actor: str = "operator:human",
    expected_current_status: str = "DRAFT",
    target_status: str = "READY",
) -> binding.CanonicalCampaignStatusTransitionRequest:
    return binding.CanonicalCampaignStatusTransitionRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        runtime_campaign_id=2,
        expected_current_status=expected_current_status,
        target_status=target_status,
        paper_account_id=paper_account_id,
        live_trading_profile_id=profile_id,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        actor=actor,
        idempotency_key=idempotency_key,
        confirm=confirm,
    )


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_clean_draft_to_ready_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    definition = _status_definition(campaign_id=campaign_id, version=1)
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(definition))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_status_connection()))
    monkeypatch.setattr(binding, "_latest_definition_version", _async_return(1))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_btc_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_active_proving_activations", _async_return(0))
    monkeypatch.setattr(binding, "_count_package_conflicts", _async_return(0))
    monkeypatch.setattr(binding, "_find_status_transition_audit_by_idempotency_key_for_update", _async_return(None))

    async def _scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    monkeypatch.setattr(db, "scalar", _scalar)

    result = await binding.transition_canonical_campaign_status(
        db=db,
        request=_status_request(
            campaign_id=campaign_id,
            paper_account_id=paper_account_id,
            profile_id=profile_id,
            idempotency_key="status-1",
            confirm=True,
        ),
    )

    assert result.changed is True
    assert result.idempotent is False
    assert definition.status == "READY"
    assert runtime.status == "READY"
    assert any(getattr(item, "action", "") == "capital_campaign.status_transition" for item in db.added)


@pytest.mark.asyncio
async def test_canonical_status_transition_readiness_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    definition = _status_definition(campaign_id=campaign_id, version=1)
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)

    monkeypatch.setattr(binding, "_load_definition", _async_return(definition))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_status_connection()))
    monkeypatch.setattr(binding, "_latest_definition_version", _async_return(1))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_btc_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_active_proving_activations", _async_return(0))
    monkeypatch.setattr(binding, "_count_package_conflicts", _async_return(0))

    async def _scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    monkeypatch.setattr(db, "scalar", _scalar)

    readiness = await binding.inspect_canonical_campaign_status_transition(
        db=db,
        request=_status_request(
            campaign_id=campaign_id,
            paper_account_id=paper_account_id,
            profile_id=profile_id,
            idempotency_key=None,
            confirm=False,
        ),
    )

    assert readiness.ready is True
    assert db.flushes == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_requires_confirm_actor_and_idempotency() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")
    db = _FakeDb()

    with pytest.raises(PermissionError, match="confirm=true"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="x",
                confirm=False,
            ),
        )
    with pytest.raises(PermissionError, match="actor is required"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="x",
                confirm=True,
                actor="  ",
            ),
        )
    with pytest.raises(PermissionError, match="idempotency_key"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_detects_definition_and_runtime_status_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")
    db = _FakeDb()

    definition = _status_definition(campaign_id=campaign_id, version=1, status="READY")
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(definition))
    monkeypatch.setattr(binding, "_inspect_canonical_campaign_status_transition_locked", _async_return(binding.CanonicalCampaignStatusTransitionReadinessResult(ready=True, blockers=[], checks=[], snapshot={})))
    monkeypatch.setattr(binding, "_find_status_transition_audit_by_idempotency_key_for_update", _async_return(None))

    async def _scalar_definition_drift(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    monkeypatch.setattr(db, "scalar", _scalar_definition_drift)
    with pytest.raises(PermissionError, match="definition status drifted"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="status-2",
                confirm=True,
            ),
        )

    definition.status = "DRAFT"
    runtime.status = "READY"
    with pytest.raises(PermissionError, match="runtime status drifted"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="status-3",
                confirm=True,
            ),
        )


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_audit_failure_rolls_back_status_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _TxRollback:
        def __init__(self, db, definition, runtime):
            self._db = db
            self._definition = definition
            self._runtime = runtime
            self._definition_before = definition.status
            self._runtime_before = runtime.status

        async def __aenter__(self):
            self._db._in_transaction = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is not None:
                self._definition.status = self._definition_before
                self._runtime.status = self._runtime_before
            self._db._in_transaction = False
            _ = exc, tb
            return False

    class _AuditFailDb(_FakeDb):
        def __init__(self, definition, runtime) -> None:
            super().__init__()
            self._definition = definition
            self._runtime = runtime

        def begin(self):
            self.begin_calls += 1
            return _TxRollback(self, self._definition, self._runtime)

        def add(self, obj) -> None:
            if getattr(obj, "action", "") == "capital_campaign.status_transition":
                raise RuntimeError("audit write failed")
            self.added.append(obj)

    definition = _status_definition(campaign_id=campaign_id, version=1)
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)
    db = _AuditFailDb(definition, runtime)

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(definition))
    monkeypatch.setattr(binding, "_load_live_profile", _async_return(_profile(profile_id, paper_account_id)))
    monkeypatch.setattr(binding, "_load_connection", _async_return(_status_connection()))
    monkeypatch.setattr(binding, "_latest_definition_version", _async_return(1))
    monkeypatch.setattr(binding, "_count_open_live_orders_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_unresolved_reconciliation_events_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_open_btc_positions_for_campaign", _async_return(0))
    monkeypatch.setattr(binding, "_count_active_proving_activations", _async_return(0))
    monkeypatch.setattr(binding, "_count_package_conflicts", _async_return(0))
    monkeypatch.setattr(binding, "_find_status_transition_audit_by_idempotency_key_for_update", _async_return(None))

    async def _scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    monkeypatch.setattr(db, "scalar", _scalar)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="status-audit-fail",
                confirm=True,
            ),
        )

    assert definition.status == "DRAFT"
    assert runtime.status == "DRAFT"


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_idempotent_replay_and_conflicting_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    db = _FakeDb()
    request = _status_request(
        campaign_id=campaign_id,
        paper_account_id=paper_account_id,
        profile_id=profile_id,
        idempotency_key="status-replay",
        confirm=True,
    )
    fp = binding._build_canonical_status_transition_request_fingerprint(request=request)
    audit = SimpleNamespace(
        before_state={"definition_status": "DRAFT", "runtime_status": "DRAFT"},
        after_state={
            "idempotency_key": "status-replay",
            "request_fingerprint": fp,
            "definition_new_status": "READY",
            "runtime_new_status": "READY",
        },
    )

    definition = _status_definition(campaign_id=campaign_id, version=1)
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)
    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(definition))
    monkeypatch.setattr(binding, "_inspect_canonical_campaign_status_transition_locked", _async_return(binding.CanonicalCampaignStatusTransitionReadinessResult(ready=True, blockers=[], checks=[], snapshot={})))
    monkeypatch.setattr(binding, "_find_status_transition_audit_by_idempotency_key_for_update", _async_return(audit))

    async def _scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    monkeypatch.setattr(db, "scalar", _scalar)

    replay = await binding.transition_canonical_campaign_status(db=db, request=request)
    assert replay.changed is False
    assert replay.idempotent is True

    conflicting = _status_request(
        campaign_id=campaign_id,
        paper_account_id=paper_account_id,
        profile_id=profile_id,
        idempotency_key="status-replay",
        confirm=True,
        target_status="PAUSED",
    )
    with pytest.raises(PermissionError, match="conflicting retry"):
        await binding.transition_canonical_campaign_status(db=db, request=conflicting)


@pytest.mark.asyncio
async def test_canonical_status_transition_execute_unsafe_readiness_gate_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")
    db = _FakeDb()

    monkeypatch.setattr(binding, "_load_definition_for_update", _async_return(_status_definition(campaign_id=campaign_id, version=1)))
    monkeypatch.setattr(binding, "_find_status_transition_audit_by_idempotency_key_for_update", _async_return(None))
    monkeypatch.setattr(binding, "_inspect_canonical_campaign_status_transition_locked", _async_return(binding.CanonicalCampaignStatusTransitionReadinessResult(ready=False, blockers=["no_open_live_btc_order"], checks=[], snapshot={})))

    async def _scalar(_statement):
        return _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id)

    monkeypatch.setattr(db, "scalar", _scalar)

    with pytest.raises(PermissionError, match="prerequisites failed"):
        await binding.transition_canonical_campaign_status(
            db=db,
            request=_status_request(
                campaign_id=campaign_id,
                paper_account_id=paper_account_id,
                profile_id=profile_id,
                idempotency_key="status-blocked",
                confirm=True,
            ),
        )


def test_canonical_status_transition_has_no_package_auth_activation_order_or_capital_calls() -> None:
    source = binding.transition_canonical_campaign_status.__code__.co_names
    assert "create_canonical_preview_package" not in source
    assert "authorize_canonical_preview_package" not in source
    assert "activate_canonical_proving_campaign" not in source
    assert "create_order" not in source
    assert "submit_alpaca_paper_order" not in source
    assert "execute_internal_crypto_fill" not in source


@pytest.mark.asyncio
async def test_canonical_status_transition_post_audit_reports_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _Scalars(self._rows)

    db = _FakeDb()
    definition = _status_definition(campaign_id=campaign_id, version=1, status="READY")
    runtime = _status_runtime(campaign_id=campaign_id, runtime_campaign_id=2, paper_account_id=paper_account_id, status="READY")
    audit_row = SimpleNamespace(
        actor="operator:human",
        action="capital_campaign.status_transition",
        before_state={"definition_status": "DRAFT", "runtime_status": "DRAFT"},
        after_state={"definition_new_status": "READY", "runtime_new_status": "READY"},
        created_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(binding, "_load_definition", _async_return(definition))
    monkeypatch.setattr(binding, "_inspect_canonical_campaign_status_transition_locked", _async_return(binding.CanonicalCampaignStatusTransitionReadinessResult(ready=True, blockers=[], checks=[], snapshot={})))

    async def _scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "CapitalCampaign":
            return runtime
        return None

    async def _execute(_statement):
        return _Result([audit_row])

    monkeypatch.setattr(db, "scalar", _scalar)
    monkeypatch.setattr(db, "execute", _execute, raising=False)

    payload = await binding.fetch_canonical_campaign_status_transition_audit(
        db=db,
        campaign_id=campaign_id,
        campaign_version=1,
        runtime_campaign_id=2,
        expected_current_status="DRAFT",
        target_status="READY",
        paper_account_id=paper_account_id,
        live_trading_profile_id=profile_id,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        actor="operator:human",
        limit=5,
    )

    assert payload["definition_status"] == "READY"
    assert payload["runtime_status"] == "READY"
    assert payload["audit"]["total"] == 1
    assert payload["unattended_eligibility"]["eligible"] is True
    assert payload["unattended_eligibility"]["appears_in_candidate_list"] is True
