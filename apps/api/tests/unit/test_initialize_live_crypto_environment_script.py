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
            paper_account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"),
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name=None,
            exchange_api_key_name_env="OT_COINBASE_API_KEY_NAME",
            exchange_private_key_env="OT_COINBASE_PRIVATE_KEY",
            exchange_passphrase_env="OT_COINBASE_PASSPHRASE",
            prompt_for_credentials=False,
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
            paper_account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"),
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name="key",
            exchange_api_key_name_env="OT_COINBASE_API_KEY_NAME",
            exchange_private_key_env="OT_COINBASE_PRIVATE_KEY",
            exchange_passphrase_env="OT_COINBASE_PASSPHRASE",
            prompt_for_credentials=False,
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
            paper_account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"),
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name=None,
            exchange_api_key_name_env="OT_COINBASE_API_KEY_NAME",
            exchange_private_key_env="OT_COINBASE_PRIVATE_KEY",
            exchange_passphrase_env="OT_COINBASE_PASSPHRASE",
            prompt_for_credentials=False,
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


def test_parse_args_includes_secure_credential_options() -> None:
    args = script.parse_args([])
    assert str(args.paper_account_id) == "905a408c-7d8e-4fc7-ad3b-9ff637005d73"
    assert args.exchange_api_key_name_env == "OT_COINBASE_API_KEY_NAME"
    assert args.exchange_private_key_env == "OT_COINBASE_PRIVATE_KEY"
    assert args.exchange_passphrase_env == "OT_COINBASE_PASSPHRASE"


def test_parse_args_rejects_plaintext_secret_flags() -> None:
    with pytest.raises(SystemExit):
        script.parse_args(["--exchange-private-key", "plaintext-secret"])


def test_resolve_credentials_prefers_env_and_never_echoes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_API_KEY_NAME", "api-key-name")
    monkeypatch.setenv("OT_COINBASE_PRIVATE_KEY", "private-key")
    monkeypatch.setenv("OT_COINBASE_PASSPHRASE", "passphrase")

    api_key, private_key, passphrase = script._resolve_credentials(
        SimpleNamespace(
            exchange_api_key_name=None,
            exchange_api_key_name_env="OT_COINBASE_API_KEY_NAME",
            exchange_private_key_env="OT_COINBASE_PRIVATE_KEY",
            exchange_passphrase_env="OT_COINBASE_PASSPHRASE",
            prompt_for_credentials=False,
        )
    )

    assert api_key == "api-key-name"
    assert private_key == "private-key"
    assert passphrase == "passphrase"


@pytest.mark.asyncio
async def test_script_failure_output_redacts_secret_values(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(script, "get_settings", lambda: _settings())
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionLocal(object()))
    monkeypatch.setenv("OT_COINBASE_PRIVATE_KEY", "super-secret-private-key")

    async def _raise(**_kwargs):
        raise RuntimeError("operation failed with super-secret-private-key")

    monkeypatch.setattr(script, "initialize_live_crypto_environment", _raise)

    result = await script._run(
        SimpleNamespace(
            apply=True,
            create_preview=False,
            create_approval=False,
            exchange_environment="production",
            actor="operator:human",
            paper_account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"),
            exchange_connection_name="coinbase-production-primary",
            exchange_api_key_name=None,
            exchange_api_key_name_env="OT_COINBASE_API_KEY_NAME",
            exchange_private_key_env="OT_COINBASE_PRIVATE_KEY",
            exchange_passphrase_env="OT_COINBASE_PASSPHRASE",
            prompt_for_credentials=False,
            registration_source="human_production_initializer",
            campaign_owner="operator",
            exchange_connection_id=None,
            live_trading_profile_id=None,
        )
    )

    captured = capsys.readouterr().out
    assert result == 1
    assert "initialization_failed" in captured
    assert "super-secret-private-key" not in captured
