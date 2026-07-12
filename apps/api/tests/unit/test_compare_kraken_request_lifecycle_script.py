from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import compare_kraken_request_lifecycle as script


def test_parse_args_defaults() -> None:
    args = script.parse_args([])
    assert args.mode == "fixed"
    assert args.provider == "kraken_spot"
    assert args.nonce == "1700000000000"
    assert args.environment == "production"
    assert args.discover_kraken_production_connection is False


@pytest.mark.asyncio
async def test_fixed_mode_no_passphrase_reports_no_divergence(capsys: pytest.CaptureFixture[str]) -> None:
    result = await script._run(
        SimpleNamespace(
            mode="fixed",
            api_key="API_KEY_PLACEHOLDER",
            api_secret_b64="c2VjcmV0LWtleS1mb3ItdGVzdHM=",
            nonce="1700000000000",
            provider_passphrase="",
            exchange_connection_id="",
            provider="kraken_spot",
            environment="production",
            paper_account_id="905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            discover_kraken_production_connection=False,
        )
    )
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert result == 0
    assert payload["first_differing_stage"] is None
    assert all(payload["stage_equality"].values())
    assert payload["provider"]["lifecycle"]["request_meta"]["prepared_url_path"] == "/0/private/Balance"
    assert payload["verifier"]["lifecycle"]["request_meta"]["prepared_url_path"] == "/0/private/Balance"


@pytest.mark.asyncio
async def test_fixed_mode_with_passphrase_detects_first_divergence(capsys: pytest.CaptureFixture[str]) -> None:
    result = await script._run(
        SimpleNamespace(
            mode="fixed",
            api_key="API_KEY_PLACEHOLDER",
            api_secret_b64="c2VjcmV0LWtleS1mb3ItdGVzdHM=",
            nonce="1700000000000",
            provider_passphrase="OTP123",
            exchange_connection_id="",
            provider="kraken_spot",
            environment="production",
            paper_account_id="905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            discover_kraken_production_connection=False,
        )
    )
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert result == 0
    assert payload["first_differing_stage"] in {"passphrase_presence", "encoded_body_hash", "payload_key_order", "otp_field_presence"}
    assert payload["stage_equality"]["passphrase_presence"] is False
    assert payload["provider"]["lifecycle"]["otp_field_present"] is True
    assert payload["verifier"]["lifecycle"]["otp_field_present"] is False


@pytest.mark.asyncio
async def test_output_does_not_contain_raw_secret_or_signature(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "c2VjcmV0LWtleS1mb3ItdGVzdHM="
    result = await script._run(
        SimpleNamespace(
            mode="fixed",
            api_key="API_KEY_PLACEHOLDER",
            api_secret_b64=secret,
            nonce="1700000000000",
            provider_passphrase="",
            exchange_connection_id="",
            provider="kraken_spot",
            environment="production",
            paper_account_id="905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            discover_kraken_production_connection=False,
        )
    )
    out = capsys.readouterr().out

    assert result == 0
    assert secret not in out
    assert "API_KEY_PLACEHOLDER" not in out
    assert '"API-Sign"' not in out
    assert '"api_sign_header_fingerprint"' in out


@pytest.mark.asyncio
async def test_runtime_discovery_requires_kraken_production(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    async def _load_creds():
        return (
            {
                "api_key": "API_KEY_PLACEHOLDER",
                "api_secret": "c2VjcmV0LWtleS1mb3ItdGVzdHM=",
                "passphrase": "",
            },
            {"credential_source": "test"},
            None,
        )

    monkeypatch.setattr(script.verifier, "_load_production_credentials", _load_creds)

    result = await script._run(
        SimpleNamespace(
            mode="runtime",
            provider="kraken_spot",
            api_key="API_KEY_PLACEHOLDER",
            api_secret_b64="c2VjcmV0LWtleS1mb3ItdGVzdHM=",
            nonce="1700000000000",
            provider_passphrase="",
            exchange_connection_id="",
            environment="sandbox",
            paper_account_id="905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            discover_kraken_production_connection=True,
        )
    )
    payload = json.loads(capsys.readouterr().out.strip())

    assert result == 2
    assert payload["error"] == "discover_mode_requires_kraken_spot_production"
