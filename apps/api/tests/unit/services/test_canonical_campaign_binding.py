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
    monkeypatch.setattr(binding, "_load_runtime", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
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
    monkeypatch.setattr(binding, "_load_runtime", _async_return(runtime))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
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
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
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
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
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
    monkeypatch.setattr(binding, "_load_runtime", _async_return(_runtime(campaign_id=campaign_id, paper_account_id=None)))
    monkeypatch.setattr(binding, "_load_paper_account", _async_return(_paper_account(paper_account_id)))
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
