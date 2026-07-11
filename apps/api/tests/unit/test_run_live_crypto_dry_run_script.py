from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services import live_crypto_orders as live_crypto_orders_service
from scripts import run_live_crypto_dry_run as script


class _FakeSession:
    pass


class _AsyncSessionLocal:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _settings(**overrides):
    payload = {
        "live_crypto_order_submission_enabled": False,
        "live_crypto_dry_run_enabled": True,
        "live_crypto_preparation_enabled": True,
        "live_crypto_max_order_usd": Decimal("5"),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _response(*, status: str = "DRY_RUN_READY", failure_reason: str | None = None):
    live_order = SimpleNamespace(
        live_crypto_order_id=UUID("11111111-1111-1111-1111-111111111111"),
        client_order_id="client-order-id",
        product_id="BTC-USD",
        side="BUY",
        requested_quote_size=Decimal("5.00"),
        failure_reason=failure_reason,
        safe_provider_response={
            "mode": "dry_run",
            "operator_identity": "operator:human",
            "approval_event_id": "22222222-2222-2222-2222-222222222222",
            "risk_event_id": "33333333-3333-3333-3333-333333333333",
            "readiness_result": "ready",
            "kill_switch_result": "clear",
            "preview_age_seconds": 1,
            "readiness_age_seconds": 1,
            "heartbeat_age_seconds": 1,
            "balance_age_seconds": 1,
            "price_age_seconds": 1,
            "max_order_usd": "5",
        },
    )
    return SimpleNamespace(
        live_crypto_order=live_order,
        dry_run_status=status,
        dry_run_message="Dry run blocked. No Coinbase order was submitted." if status != "DRY_RUN_READY" else "Dry run completed. No Coinbase order was submitted.",
        safe_request_summary={"product_id": "BTC-USD"},
        provider_create_order_called=False,
        order_submitted=False,
        submission_skipped=True,
        submission_skip_reason="Coinbase order submission intentionally skipped (LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false, LIVE_CRYPTO_DRY_RUN_ENABLED=true)",
    )


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_success_prints_only_safe_fields(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))
    monkeypatch.setattr(
        live_crypto_orders_service.service,
        "dry_run",
        lambda **_kwargs: _response(status="DRY_RUN_READY"),
    )

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-1",
        )
    )

    captured = capsys.readouterr().out
    assert result == 0
    assert "dry_run_mode=dry_run" in captured
    assert "submission_skipped=true" in captured
    assert "local_order_id=11111111-1111-1111-1111-111111111111" in captured
    assert "client_order_id=client-order-id" in captured
    assert "BTC-USD" in captured
    assert "secret" not in captured.lower()


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_refuses_when_submission_flag_is_true(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings(live_crypto_order_submission_enabled=True))
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-2",
        )
    )

    captured = capsys.readouterr().out
    assert result == 2
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false" in captured


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_refuses_when_dry_run_disabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings(live_crypto_dry_run_enabled=False))
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-3",
        )
    )

    captured = capsys.readouterr().out
    assert result == 2
    assert "LIVE_CRYPTO_DRY_RUN_ENABLED must be true" in captured


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_refuses_when_preparation_disabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings(live_crypto_preparation_enabled=False))
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-4",
        )
    )

    captured = capsys.readouterr().out
    assert result == 2
    assert "LIVE_CRYPTO_PREPARATION_ENABLED must be true" in captured


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_returns_nonzero_for_blocked_service_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))
    monkeypatch.setattr(
        live_crypto_orders_service.service,
        "dry_run",
        lambda **_kwargs: _response(status="DRY_RUN_BLOCKED", failure_reason="approval gate rejected"),
    )

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-5",
        )
    )

    captured = capsys.readouterr().out
    assert result == 1
    assert "safe_failure_reason=approval gate rejected" in captured


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_returns_nonzero_for_audit_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))

    async def _raise(**_kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(live_crypto_orders_service.service, "dry_run", _raise)

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-6",
        )
    )

    captured = capsys.readouterr().out
    assert result == 1
    assert "safe_failure_reason=audit write failed" in captured


@pytest.mark.asyncio
async def test_safe_vps_dry_run_script_does_not_call_create_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(_FakeSession()))
    called = {"count": 0}

    async def _raise_if_called(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("create_order must not be called by the safe VPS script")

    monkeypatch.setattr(live_crypto_orders_service.CoinbaseAdvancedClient, "create_order", _raise_if_called)
    monkeypatch.setattr(live_crypto_orders_service.service, "dry_run", lambda **_kwargs: _response(status="DRY_RUN_READY"))

    result = await script._run_dry_run(
        SimpleNamespace(
            live_trading_profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            crypto_order_preview_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            operator_identity="operator:human",
            idempotency_token="token-7",
        )
    )

    assert result == 0
    assert called["count"] == 0
