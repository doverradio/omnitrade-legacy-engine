from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from scripts import initialize_live_crypto_environment as script


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
        "live_crypto_max_order_usd": Decimal("5"),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _readiness(*, ready: bool) -> SimpleNamespace:
    return SimpleNamespace(
        ready=ready,
        exchange_connection_id=UUID("11111111-1111-1111-1111-111111111111"),
        live_trading_profile_id=UUID("22222222-2222-2222-2222-222222222222"),
        items=(
            SimpleNamespace(key="database", label="Database", ready=True, detail="Database ready"),
            SimpleNamespace(key="exchange_connection", label="Exchange", ready=ready, detail="Exchange ready" if ready else "Exchange missing"),
        ),
    )


@pytest.mark.asyncio
async def test_script_inspection_mode_prints_readiness(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(object()))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: _readiness(ready=False))

    result = await script._run(
        SimpleNamespace(
            apply=False,
            create_preview=False,
            create_approval=False,
            exchange_environment="production",
            actor="operator:human",
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name=None,
            exchange_private_key=None,
            exchange_passphrase=None,
            registration_source="human_production_initializer",
            campaign_owner="operator",
            exchange_connection_id=None,
            live_trading_profile_id=None,
        )
    )

    assert result == 0
    captured = capsys.readouterr().out
    assert "Live Crypto Environment Readiness" in captured
    assert "Overall Ready: false" in captured


@pytest.mark.asyncio
async def test_script_apply_mode_initializes_missing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(object()))
    monkeypatch.setattr(
        script,
        "initialize_live_crypto_environment",
        lambda **_kwargs: SimpleNamespace(
            created_exchange_connection=True,
            created_asset=True,
            created_live_trading_profile=True,
            created_capital_campaign=True,
            readiness=_readiness(ready=False),
        ),
    )

    result = await script._run(
        SimpleNamespace(
            apply=True,
            create_preview=False,
            create_approval=False,
            exchange_environment="production",
            actor="operator:human",
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name="key",
            exchange_private_key="secret",
            exchange_passphrase=None,
            registration_source="human_production_initializer",
            campaign_owner="operator",
            exchange_connection_id=None,
            live_trading_profile_id=None,
        )
    )

    assert result == 0
    captured = capsys.readouterr().out
    assert "created_exchange_connection=true" in captured
    assert "created_asset=true" in captured


@pytest.mark.asyncio
async def test_script_refuses_when_live_submission_enabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings(live_crypto_order_submission_enabled=True))

    result = await script._run(
        SimpleNamespace(
            apply=False,
            create_preview=False,
            create_approval=False,
            exchange_environment="production",
            actor="operator:human",
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name=None,
            exchange_private_key=None,
            exchange_passphrase=None,
            registration_source="human_production_initializer",
            campaign_owner="operator",
            exchange_connection_id=None,
            live_trading_profile_id=None,
        )
    )

    assert result == 2
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false" in capsys.readouterr().out


def test_parse_args_mutually_exclusive_modes() -> None:
    with pytest.raises(SystemExit):
        script.parse_args(["--apply", "--create-preview"])
