from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import ast
from pathlib import Path

import pytest

from app.services import canonical_campaign_binding as binding


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0

    def begin(self):
        return _NullTx()

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1


def _definition(*, campaign_id: UUID, version: int) -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        deployed_capital=Decimal("0"),
        current_campaign_equity=Decimal("25"),
    )


def _runtime(*, campaign_id: UUID, paper_account_id: UUID | None, exchange: str = "kraken_spot") -> SimpleNamespace:
    return SimpleNamespace(
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
