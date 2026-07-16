from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

import app.operator_cli.service as service


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

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
@pytest.mark.parametrize(
    "wrapper_name,patch_name,kwargs",
    [
        (
            "create_canonical_preview_package_bundle",
            "create_canonical_preview_package",
            {
                "campaign_id": uuid4(),
                "campaign_version": 1,
                "paper_account_id": uuid4(),
                "live_trading_profile_id": uuid4(),
                "provider": "kraken_spot",
                "environment": "production",
                "product_id": "BTC-USD",
                "max_proposed_order_amount": Decimal("5"),
                "actor": "operator:human",
                "idempotency_key": "pkg-1",
            },
        ),
        (
            "authorize_canonical_preview_package_bundle",
            "authorize_canonical_preview_package",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "approver_role": "risk_owner",
                "rationale": "bounded proving",
                "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "max_order_usd": Decimal("5"),
                "max_total_deployed_campaign_capital_usd": Decimal("5"),
                "no_leverage": True,
                "idempotency_key": "auth-1",
            },
        ),
        (
            "dry_run_canonical_preview_package_bundle",
            "run_dry_run_for_canonical_preview_package",
            {
                "package_id": uuid4(),
                "approval_event_id": uuid4(),
                "operator_identity": "operator:human",
                "idempotency_token": "dry-1",
            },
        ),
        (
            "activate_canonical_proving_campaign_bundle",
            "activate_canonical_proving_campaign",
            {
                "package_id": uuid4(),
                "approval_event_id": uuid4(),
                "dry_run_live_crypto_order_id": uuid4(),
                "actor": "operator:human",
                "expires_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "idempotency_key": "act-1",
                "confirm": True,
            },
        ),
        (
            "pause_canonical_proving_activation_bundle",
            "pause_canonical_proving_activation",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "reason": "pause",
                "idempotency_key": "pause-1",
            },
        ),
        (
            "revoke_canonical_proving_activation_bundle",
            "revoke_canonical_proving_activation",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "reason": "revoke",
                "idempotency_key": "revoke-1",
            },
        ),
    ],
)
async def test_canonical_mutating_bundle_wrappers_commit_on_success(monkeypatch: pytest.MonkeyPatch, wrapper_name: str, patch_name: str, kwargs: dict) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_success(**_kwargs):
        return {"ok": True, "wrapper": wrapper_name}

    monkeypatch.setattr(service, patch_name, _fake_success)

    wrapper = getattr(service, wrapper_name)
    result = await wrapper(**kwargs)

    assert result["ok"] is True
    assert db.commits == 1
    assert db.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrapper_name,patch_name,kwargs",
    [
        (
            "create_canonical_preview_package_bundle",
            "create_canonical_preview_package",
            {
                "campaign_id": uuid4(),
                "campaign_version": 1,
                "paper_account_id": uuid4(),
                "live_trading_profile_id": uuid4(),
                "provider": "kraken_spot",
                "environment": "production",
                "product_id": "BTC-USD",
                "max_proposed_order_amount": Decimal("5"),
                "actor": "operator:human",
                "idempotency_key": "pkg-rollback-1",
            },
        ),
        (
            "authorize_canonical_preview_package_bundle",
            "authorize_canonical_preview_package",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "approver_role": "risk_owner",
                "rationale": "bounded proving",
                "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "max_order_usd": Decimal("5"),
                "max_total_deployed_campaign_capital_usd": Decimal("5"),
                "no_leverage": True,
                "idempotency_key": "auth-rollback-1",
            },
        ),
        (
            "dry_run_canonical_preview_package_bundle",
            "run_dry_run_for_canonical_preview_package",
            {
                "package_id": uuid4(),
                "approval_event_id": uuid4(),
                "operator_identity": "operator:human",
                "idempotency_token": "dry-rollback-1",
            },
        ),
        (
            "activate_canonical_proving_campaign_bundle",
            "activate_canonical_proving_campaign",
            {
                "package_id": uuid4(),
                "approval_event_id": uuid4(),
                "dry_run_live_crypto_order_id": uuid4(),
                "actor": "operator:human",
                "expires_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "idempotency_key": "act-rollback-1",
                "confirm": True,
            },
        ),
        (
            "pause_canonical_proving_activation_bundle",
            "pause_canonical_proving_activation",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "reason": "pause",
                "idempotency_key": "pause-rollback-1",
            },
        ),
        (
            "revoke_canonical_proving_activation_bundle",
            "revoke_canonical_proving_activation",
            {
                "package_id": uuid4(),
                "actor": "operator:human",
                "reason": "revoke",
                "idempotency_key": "revoke-rollback-1",
            },
        ),
    ],
)
async def test_canonical_mutating_bundle_wrappers_rollback_on_failure(monkeypatch: pytest.MonkeyPatch, wrapper_name: str, patch_name: str, kwargs: dict) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_fail(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, patch_name, _fake_fail)

    wrapper = getattr(service, wrapper_name)
    with pytest.raises(RuntimeError, match="boom"):
        await wrapper(**kwargs)

    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_canonical_read_only_bundle_wrappers_do_not_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_show(**_kwargs):
        return {"package": {"package_id": str(uuid4())}, "readiness": {"ready": True}}

    async def _fake_history(**_kwargs):
        return {"items": [], "count": 0}

    async def _fake_activation_status(**_kwargs):
        return {"activated": False}

    async def _fake_authority_audit(**_kwargs):
        return {"command": "canonical-campaign-authority-audit", "ok": True}

    async def _fake_cash_causality_audit(**_kwargs):
        return {"command": "canonical-paper-cash-causality-audit", "ok": True}

    monkeypatch.setattr(service, "get_canonical_preview_package", _fake_show)
    monkeypatch.setattr(service, "list_canonical_preview_package_history", _fake_history)
    monkeypatch.setattr(service, "get_canonical_proving_activation_status", _fake_activation_status)
    monkeypatch.setattr(service, "run_canonical_campaign_authority_audit", _fake_authority_audit)
    monkeypatch.setattr(service, "run_canonical_paper_cash_causality_audit", _fake_cash_causality_audit)

    show_payload = await service.show_canonical_preview_package_bundle(package_id=uuid4())
    readiness_payload = await service.canonical_preview_package_readiness(package_id=uuid4())
    history_payload = await service.canonical_preview_package_history(campaign_id=uuid4(), campaign_version=1, limit=5)
    status_payload = await service.canonical_proving_activation_status(package_id=uuid4())
    authority_payload = await service.canonical_campaign_authority_audit(
        campaign_id=uuid4(),
        campaign_version=1,
        cycle_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
    )
    cash_payload = await service.canonical_paper_cash_causality_audit(
        campaign_id=uuid4(),
        campaign_version=1,
        runtime_campaign_id=2,
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
    )

    assert show_payload["readiness"]["ready"] is True
    assert readiness_payload["readiness"]["ready"] is True
    assert history_payload["count"] == 0
    assert status_payload["activated"] is False
    assert authority_payload["ok"] is True
    assert cash_payload["ok"] is True
    assert db.commits == 0
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_canonical_paper_cash_causality_wrapper_failure_does_not_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _boom(**_kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(service, "run_canonical_paper_cash_causality_audit", _boom)

    with pytest.raises(RuntimeError, match="audit failed"):
        await service.canonical_paper_cash_causality_audit(
            campaign_id=uuid4(),
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=uuid4(),
            live_trading_profile_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
        )

    assert db.commits == 0
    assert db.rollbacks == 1
