from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.operator_cli import service


def _case():
    now = datetime.now(timezone.utc)
    package_id, campaign_id, mandate_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    account_id, profile_id, connection_id, claim_id, activation_id, order_id, preview_id = (uuid4() for _ in range(7))
    package = SimpleNamespace(
        package_id=package_id, campaign_id=campaign_id, campaign_version=1, mandate_id=mandate_id,
        mandate_version_id=version_id, paper_account_id=account_id, live_trading_profile_id=profile_id,
        side="BUY", risk_approved_amount=5, crypto_order_preview_id=preview_id, risk_event_id=uuid4(),
        package_state="ACTIVATED",
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    activation = SimpleNamespace(
        activation_id=activation_id, activation_state="ACTIVE", activated_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5), max_order_amount=5, no_leverage=True,
        paper_account_id=account_id, live_trading_profile_id=profile_id,
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    claim = SimpleNamespace(
        claim_id=claim_id, live_order_id=order_id, claim_status="SAFETY_DISABLED", claim_owner="worker:test",
        claimed_at=now, attempt_count=1, reconciliation_state=None, last_error_code="live_submission_disabled",
        campaign_id=campaign_id, campaign_version=1, mandate_id=mandate_id, mandate_version_id=version_id,
        account_id=account_id, profile_id=profile_id, connection_id=connection_id,
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    binding = {
        "package_id": str(package_id), "activation_id": str(activation_id), "claim_id": str(claim_id),
        "live_order_id": str(order_id), "campaign_id": str(campaign_id), "campaign_version": 1,
        "mandate_id": str(mandate_id), "mandate_version_id": str(version_id),
        "account_id": str(account_id), "profile_id": str(profile_id), "connection_id": str(connection_id),
        "provider": "kraken_spot", "environment": "production", "product": "BTC-USD",
        "side": "BUY", "quantity": "0.05", "order_type": "MARKET", "crypto_order_preview_id": str(preview_id),
    }
    order = SimpleNamespace(
        live_crypto_order_id=order_id, client_order_id="cpp-one-shot", status="PENDING_CONFIRMATION",
        side="BUY", order_type="MARKET", requested_quote_size=5, submitted_at=None, provider_order_id=None,
        safe_provider_response={"commissioned_preview_identity_hash": "hash", "commissioned_preview_identity_binding": binding},
        exchange_connection_id=connection_id, provider="kraken_spot", environment="production", product_id="BTC-USD",
    )
    campaign = SimpleNamespace(status="RUNNING")
    mandate = SimpleNamespace(
        status="ACTIVE", expires_at=now + timedelta(days=1), paper_account_id=account_id,
        live_trading_profile_id=profile_id, exchange_connection_id=connection_id,
        provider="kraken_spot", exchange_environment="production",
    )
    version = SimpleNamespace(is_active=True, is_authorized=True, price_evidence_max_age_seconds=300)
    profile = SimpleNamespace(id=profile_id, paper_account_id=account_id)
    connection = SimpleNamespace(exchange_connection_id=connection_id, provider="kraken_spot", environment="production")
    risk = SimpleNamespace(created_at=now, paper_account_id=account_id)
    settings = SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=campaign_id,
        automatic_mandate_package_activation_campaign_version=1,
        automatic_mandate_package_activation_mandate_id=mandate_id,
        automatic_mandate_package_activation_mandate_version_id=version_id,
        live_crypto_order_submission_enabled=False,
    )
    rows = [package, activation, claim, order, "20260724_0048", campaign, mandate, version, profile, connection, risk, None, None, 0]
    return package_id, claim, settings, rows


@pytest.mark.asyncio
async def test_preflight_ready_only_when_every_gate_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id, _claim, settings, rows = _case()
    db = SimpleNamespace(scalar=AsyncMock(side_effect=rows))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(service, "AsyncSessionLocal", session)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    result = await service.autonomous_execution_status(package_id=package_id)
    assert result["verdict"] == "READY_FOR_ONE_SHOT_LIVE_BUY"
    assert not result["failed_gates"]
    assert not hasattr(db, "add")


@pytest.mark.asyncio
async def test_consumed_one_shot_is_blocked_after_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id, claim, settings, rows = _case()
    claim.claim_status = "SUBMISSION_PENDING"
    db = SimpleNamespace(scalar=AsyncMock(side_effect=rows))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(service, "AsyncSessionLocal", session)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    result = await service.autonomous_execution_status(package_id=package_id)
    assert result["verdict"] == "BLOCKED"
    assert "one_shot_available" in {row["gate"] for row in result["failed_gates"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "mutate"),
    [
        ("migration_0048_applied", lambda r, s: r.__setitem__(4, None)),
        ("configured_campaign_mandate_scope", lambda r, s: setattr(s, "automatic_mandate_package_activation_campaign_version", 2)),
        ("campaign_active", lambda r, s: setattr(r[5], "status", "PAUSED")),
        ("mandate_active", lambda r, s: setattr(r[6], "status", "REVOKED")),
        ("mandate_version_active", lambda r, s: setattr(r[7], "is_active", False)),
        ("activation_active", lambda r, s: setattr(r[1], "activation_state", "EXPIRED")),
        ("one_shot_available", lambda r, s: setattr(r[2], "claim_status", "COMPLETED")),
        ("buy_only", lambda r, s: setattr(r[0], "side", "SELL")),
        ("bounded_notional", lambda r, s: setattr(r[3], "requested_quote_size", 6)),
        ("no_leverage", lambda r, s: setattr(r[1], "no_leverage", False)),
        ("identity_binding", lambda r, s: r[3].safe_provider_response.update({"commissioned_preview_identity_hash": ""})),
        ("no_previous_submission", lambda r, s: setattr(r[3], "submitted_at", datetime.now(timezone.utc))),
        ("no_reconciliation_obligation", lambda r, s: r.__setitem__(12, SimpleNamespace(reconciliation_status="unknown"))),
        ("no_open_position", lambda r, s: r.__setitem__(13, 0.05)),
        ("kill_switch_clear", lambda r, s: r.__setitem__(11, uuid4())),
        ("risk_evidence_fresh", lambda r, s: setattr(r[10], "created_at", datetime.now(timezone.utc) - timedelta(hours=1))),
        ("profile_connection_scope", lambda r, s: setattr(r[8], "paper_account_id", uuid4())),
        ("live_submission_currently_disabled", lambda r, s: setattr(s, "live_crypto_order_submission_enabled", True)),
    ],
)
async def test_every_failed_safety_gate_blocks(monkeypatch: pytest.MonkeyPatch, gate: str, mutate) -> None:
    package_id, _claim, settings, rows = _case()
    mutate(rows, settings)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=rows))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(service, "AsyncSessionLocal", session)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    result = await service.autonomous_execution_status(package_id=package_id)
    assert result["verdict"] == "BLOCKED"
    assert gate in {row["gate"] for row in result["failed_gates"]}
