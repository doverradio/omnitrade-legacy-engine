from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

import app.operator_cli.main as operator_main
from app.operator_cli.main import parse_args


def test_parse_preview_command() -> None:
    args = parse_args([
        "preview",
        "--verbose",
        "--actor",
        "operator:test",
        "--product-id",
        "BTC-USD",
    ])

    assert args.command == "preview"
    assert args.mandate_id is None
    assert args.actor == "operator:test"
    assert args.product_id == "BTC-USD"
    assert args.strategy_interval == "15m"
    assert args.verbose is True


def test_resolve_preview_idempotency_seed_defaults_to_fresh_value(monkeypatch) -> None:
    monkeypatch.setattr(operator_main, "uuid4", lambda: SimpleNamespace(hex="fresh-seed"))
    args = parse_args([
        "preview",
        "--actor",
        "operator:test",
    ])

    assert operator_main._resolve_preview_idempotency_seed(args) == "fresh-seed"


def test_resolve_preview_idempotency_seed_can_reuse_existing_key() -> None:
    args = parse_args([
        "preview",
        "--reuse-idempotency-key",
        "--actor",
        "operator:test",
    ])

    assert operator_main._resolve_preview_idempotency_seed(args) is None


def test_parse_preview_show_command() -> None:
    args = parse_args([
        "preview-show",
        "--no-color",
        "--preview-id",
        "22222222-2222-2222-2222-222222222222",
        "--json",
    ])

    assert args.command == "preview-show"
    assert args.preview_id == UUID("22222222-2222-2222-2222-222222222222")
    assert args.json_output is True
    assert args.no_color is True


def test_parse_candles_command() -> None:
    args = parse_args([
        "candles",
        "--symbol",
        "BTC",
        "--interval",
        "15m",
        "--exchange",
        "kraken_spot",
        "--max-age-minutes",
        "12",
    ])

    assert args.command == "candles"
    assert args.symbol == "BTC"
    assert args.interval == "15m"
    assert args.exchange == "kraken_spot"
    assert args.max_age_minutes == 12


def test_parse_status_command() -> None:
    args = parse_args([
        "status",
        "--mandate-id",
        "33333333-3333-3333-3333-333333333333",
        "--symbol",
        "BTC",
    ])

    assert args.command == "status"
    assert args.mandate_id == UUID("33333333-3333-3333-3333-333333333333")
    assert args.symbol == "BTC"
    assert args.interval == "15m"


def test_parse_watch_command() -> None:
    args = parse_args([
        "watch",
        "--symbol",
        "BTC",
        "--refresh-seconds",
        "2",
    ])

    assert args.command == "watch"
    assert args.symbol == "BTC"
    assert args.refresh_seconds == 2


def test_parse_roster_command() -> None:
    args = parse_args([
        "roster",
        "--provider",
        "kraken_spot",
        "--product-id",
        "BTC-USD",
        "--interval",
        "15m",
    ])

    assert args.command == "roster"
    assert args.provider == "kraken_spot"
    assert args.product_id == "BTC-USD"
    assert args.interval == "15m"


def test_parse_scorecards_command() -> None:
    args = parse_args([
        "scorecards",
        "--provider",
        "kraken_spot",
        "--product-id",
        "BTC-USD",
        "--interval",
        "15m",
        "--json",
    ])

    assert args.command == "scorecards"
    assert args.provider == "kraken_spot"
    assert args.product_id == "BTC-USD"
    assert args.interval == "15m"
    assert args.json_output is True


def test_parse_campaign_orchestration_commands() -> None:
    readiness = parse_args([
        "campaign-orchestration-readiness",
        "--campaign-id",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ])
    assert readiness.command == "campaign-orchestration-readiness"
    assert readiness.campaign_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    preview = parse_args([
        "campaign-orchestration-preview",
    ])
    assert preview.command == "campaign-orchestration-preview"

    status = parse_args([
        "campaign-orchestration-status",
        "--campaign-id",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ])
    assert status.command == "campaign-orchestration-status"

    history = parse_args([
        "campaign-orchestration-history",
        "--campaign-id",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "--limit",
        "5",
    ])
    assert history.command == "campaign-orchestration-history"
    assert history.limit == 5


def test_parse_execution_forensics_command_selectors() -> None:
    latest = parse_args([
        "execution-forensics",
        "--latest",
    ])
    assert latest.command == "execution-forensics"
    assert latest.latest is True
    assert latest.since is None
    assert latest.cycle is None

    since = parse_args([
        "execution-forensics",
        "--since",
        "2 hours ago",
    ])
    assert since.command == "execution-forensics"
    assert since.latest is False
    assert since.since == "2 hours ago"
    assert since.cycle is None

    cycle = parse_args([
        "execution-forensics",
        "--cycle",
        "44444444-4444-4444-4444-444444444444",
        "--json",
    ])
    assert cycle.command == "execution-forensics"
    assert cycle.latest is False
    assert cycle.since is None
    assert cycle.cycle == UUID("44444444-4444-4444-4444-444444444444")
    assert cycle.json_output is True


def test_parse_canonical_campaign_binding_commands() -> None:
    readiness = parse_args([
        "canonical-campaign-readiness",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert readiness.command == "canonical-campaign-readiness"
    assert readiness.json_output is True

    audit = parse_args([
        "canonical-campaign-binding-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--limit",
        "10",
        "--json",
    ])
    assert audit.command == "canonical-campaign-binding-audit"
    assert audit.limit == 10
    assert audit.json_output is True


def test_parse_canonical_campaign_authority_audit_command() -> None:
    args = parse_args([
        "canonical-campaign-authority-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--cycle-id",
        "ce8c5594-c39e-4634-945c-66ef0395a7c3",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert args.command == "canonical-campaign-authority-audit"
    assert args.campaign_version == 1
    assert args.json_output is True


def test_parse_canonical_paper_cash_causality_audit_command() -> None:
    args = parse_args([
        "canonical-paper-cash-causality-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert args.command == "canonical-paper-cash-causality-audit"
    assert args.runtime_campaign_id == 2
    assert args.json_output is True


def test_parse_canonical_paper_cash_causality_audit_rejects_malformed_uuid() -> None:
    with pytest.raises(SystemExit):
        parse_args([
            "canonical-paper-cash-causality-audit",
            "--campaign-id",
            "not-a-uuid",
            "--campaign-version",
            "1",
            "--runtime-campaign-id",
            "2",
            "--paper-account-id",
            "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            "--live-trading-profile-id",
            "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
            "--provider",
            "kraken_spot",
            "--environment",
            "production",
            "--product",
            "BTC-USD",
        ])


def test_parse_canonical_proving_account_transition_commands() -> None:
    preview = parse_args([
        "canonical-proving-account-transition-preview",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--old-paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])
    assert preview.command == "canonical-proving-account-transition-preview"
    assert preview.runtime_campaign_id == 2

    execute = parse_args([
        "canonical-proving-account-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--old-paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "transition-1",
        "--confirm",
        "--json",
    ])
    assert execute.command == "canonical-proving-account-transition-execute"
    assert execute.idempotency_key == "transition-1"
    assert execute.confirm is True


def test_parse_exchange_refresh_and_proving_cap_transition_commands() -> None:
    refresh = parse_args([
        "exchange-connection-refresh-balances",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--actor",
        "operator:human",
        "--json",
    ])
    assert refresh.command == "exchange-connection-refresh-balances"
    assert refresh.provider == "kraken_spot"
    assert refresh.environment == "production"
    assert refresh.json_output is True

    preview = parse_args([
        "canonical-proving-cap-transition-preview",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--json",
    ])
    assert preview.command == "canonical-proving-cap-transition-preview"
    assert preview.campaign_version == 1

    execute = parse_args([
        "canonical-proving-cap-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "cap-1",
        "--confirm",
        "--json",
    ])
    assert execute.command == "canonical-proving-cap-transition-execute"
    assert execute.idempotency_key == "cap-1"
    assert execute.confirm is True


def test_parse_canonical_campaign_status_transition_commands() -> None:
    readiness = parse_args([
        "canonical-campaign-status-transition-readiness",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])
    assert readiness.command == "canonical-campaign-status-transition-readiness"
    assert readiness.runtime_campaign_id == 2

    execute = parse_args([
        "canonical-campaign-status-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "status-1",
        "--confirm",
        "--json",
    ])
    assert execute.command == "canonical-campaign-status-transition-execute"
    assert execute.idempotency_key == "status-1"
    assert execute.confirm is True

    audit = parse_args([
        "canonical-campaign-status-transition-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])
    assert audit.command == "canonical-campaign-status-transition-audit"


def test_parse_first_autonomous_profit_status_command() -> None:
    args = parse_args([
        "first-autonomous-profit-status",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert args.command == "first-autonomous-profit-status"
    assert args.campaign_version == 1
    assert args.runtime_campaign_id == 2
    assert args.json_output is True


def test_parse_campaign_unattended_eligibility_audit_command() -> None:
    args = parse_args([
        "campaign-unattended-eligibility-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert args.command == "campaign-unattended-eligibility-audit"
    assert args.campaign_version == 1
    assert args.provider == "kraken_spot"
    assert args.environment == "production"
    assert args.product == "BTC-USD"
    assert args.json_output is True


def test_parse_historical_buy_campaign_replay_audit_command() -> None:
    args = parse_args([
        "historical-buy-campaign-replay-audit",
        "--decision-id",
        "939b4ea0-4d25-4970-8eff-0b7596c7557d",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "8e76a2fa-ae85-45c6-95d1-798cce8f8cc9",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--matching-sell-decision-id",
        "cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28",
        "--json",
    ])
    assert args.command == "historical-buy-campaign-replay-audit"
    assert str(args.decision_id) == "939b4ea0-4d25-4970-8eff-0b7596c7557d"
    assert str(args.matching_sell_decision_id) == "cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28"
    assert args.runtime_campaign_id == 2
    assert args.json_output is True


def test_parse_buy_opportunity_diagnostic_command() -> None:
    args = parse_args([
        "buy-opportunity-diagnostic",
        "--json",
    ])
    assert args.command == "buy-opportunity-diagnostic"
    assert args.json_output is True


def test_parse_hold_decision_diagnostic_command() -> None:
    args = parse_args([
        "hold-decision-diagnostic",
        "--json",
    ])
    assert args.command == "hold-decision-diagnostic"
    assert args.json_output is True


def test_parse_canonical_campaign_authority_audit_rejects_malformed_uuid() -> None:
    with pytest.raises(SystemExit):
        parse_args([
            "canonical-campaign-authority-audit",
            "--campaign-id",
            "not-a-uuid",
            "--campaign-version",
            "1",
            "--cycle-id",
            "ce8c5594-c39e-4634-945c-66ef0395a7c3",
            "--paper-account-id",
            "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
            "--live-trading-profile-id",
            "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
            "--provider",
            "kraken_spot",
            "--environment",
            "production",
            "--product",
            "BTC-USD",
        ])


@pytest.mark.asyncio
async def test_run_async_routes_canonical_campaign_authority_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "canonical-campaign-authority-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--cycle-id",
        "ce8c5594-c39e-4634-945c-66ef0395a7c3",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])

    async def _fake_audit(**kwargs):
        assert str(kwargs["cycle_id"]) == "ce8c5594-c39e-4634-945c-66ef0395a7c3"
        return {"ok": True, "command": "canonical-campaign-authority-audit"}

    monkeypatch.setattr(operator_main, "canonical_campaign_authority_audit", _fake_audit)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["ok"] is True
    assert "canonical-campaign-authority-audit" in text


@pytest.mark.asyncio
async def test_run_async_routes_canonical_paper_cash_causality_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "canonical-paper-cash-causality-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])

    async def _fake_audit(**kwargs):
        assert kwargs["runtime_campaign_id"] == 2
        return {"ok": True, "command": "canonical-paper-cash-causality-audit"}

    monkeypatch.setattr(operator_main, "canonical_paper_cash_causality_audit", _fake_audit)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["ok"] is True
    assert "canonical-paper-cash-causality-audit" in text


@pytest.mark.asyncio
async def test_run_async_routes_canonical_proving_account_transition_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "canonical-proving-account-transition-preview",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--old-paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])

    async def _fake_preview(**kwargs):
        assert kwargs["runtime_campaign_id"] == 2
        return {"ok": True, "command": "canonical-proving-account-transition-preview"}

    monkeypatch.setattr(operator_main, "canonical_proving_account_transition_preview", _fake_preview)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["ok"] is True
    assert "canonical-proving-account-transition-preview" in text


@pytest.mark.asyncio
async def test_run_async_routes_canonical_proving_account_transition_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "canonical-proving-account-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--old-paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "transition-1",
        "--confirm",
        "--json",
    ])

    async def _fake_execute(**kwargs):
        assert kwargs["idempotency_key"] == "transition-1"
        return {"ok": True, "command": "canonical-proving-account-transition-execute"}

    monkeypatch.setattr(operator_main, "canonical_proving_account_transition_execute", _fake_execute)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["ok"] is True
    assert "canonical-proving-account-transition-execute" in text


@pytest.mark.asyncio
async def test_run_async_routes_exchange_refresh_and_proving_cap_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_args = parse_args([
        "exchange-connection-refresh-balances",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--actor",
        "operator:human",
        "--json",
    ])

    async def _fake_refresh(**kwargs):
        assert kwargs["provider"] == "kraken_spot"
        return {"ok": True, "command": "exchange-connection-refresh-balances"}

    monkeypatch.setattr(operator_main, "refresh_provider_balance_evidence", _fake_refresh)
    refresh_code, refresh_payload, refresh_text = await operator_main._run_async(refresh_args)
    assert refresh_code == 0
    assert refresh_payload["ok"] is True
    assert "exchange-connection-refresh-balances" in refresh_text

    preview_args = parse_args([
        "canonical-proving-cap-transition-preview",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--json",
    ])

    async def _fake_preview(**kwargs):
        assert kwargs["campaign_version"] == 1
        return {"ready": True, "command": "canonical-proving-cap-transition-preview"}

    monkeypatch.setattr(operator_main, "canonical_proving_cap_transition_preview", _fake_preview)
    preview_code, preview_payload, preview_text = await operator_main._run_async(preview_args)
    assert preview_code == 0
    assert preview_payload["ready"] is True
    assert "canonical-proving-cap-transition-preview" in preview_text

    execute_args = parse_args([
        "canonical-proving-cap-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "cap-1",
        "--confirm",
        "--json",
    ])

    async def _fake_execute(**kwargs):
        assert kwargs["idempotency_key"] == "cap-1"
        return {"changed": True, "command": "canonical-proving-cap-transition-execute"}

    monkeypatch.setattr(operator_main, "canonical_proving_cap_transition_execute", _fake_execute)
    execute_code, execute_payload, execute_text = await operator_main._run_async(execute_args)
    assert execute_code == 0
    assert execute_payload["changed"] is True
    assert "canonical-proving-cap-transition-execute" in execute_text


@pytest.mark.asyncio
async def test_run_async_routes_canonical_campaign_status_transition_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    readiness_args = parse_args([
        "canonical-campaign-status-transition-readiness",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])

    async def _fake_readiness(**kwargs):
        assert kwargs["runtime_campaign_id"] == 2
        return {"ready": True, "command": "canonical-campaign-status-transition-readiness"}

    monkeypatch.setattr(operator_main, "canonical_campaign_status_transition_readiness", _fake_readiness)
    readiness_code, readiness_payload, readiness_text = await operator_main._run_async(readiness_args)
    assert readiness_code == 0
    assert readiness_payload["ready"] is True
    assert "canonical-campaign-status-transition-readiness" in readiness_text

    execute_args = parse_args([
        "canonical-campaign-status-transition-execute",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--idempotency-key",
        "status-1",
        "--confirm",
        "--json",
    ])

    async def _fake_execute(**kwargs):
        assert kwargs["idempotency_key"] == "status-1"
        return {"changed": True, "command": "canonical-campaign-status-transition-execute"}

    monkeypatch.setattr(operator_main, "canonical_campaign_status_transition_execute", _fake_execute)
    execute_code, execute_payload, execute_text = await operator_main._run_async(execute_args)
    assert execute_code == 0
    assert execute_payload["changed"] is True
    assert "canonical-campaign-status-transition-execute" in execute_text

    audit_args = parse_args([
        "canonical-campaign-status-transition-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--expected-current-status",
        "DRAFT",
        "--target-status",
        "READY",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--actor",
        "operator:human",
        "--json",
    ])

    async def _fake_audit(**kwargs):
        assert kwargs["target_status"] == "READY"
        return {"audit": {"total": 1}, "command": "canonical-campaign-status-transition-audit"}

    monkeypatch.setattr(operator_main, "canonical_campaign_status_transition_audit", _fake_audit)
    audit_code, audit_payload, audit_text = await operator_main._run_async(audit_args)
    assert audit_code == 0
    assert audit_payload["audit"]["total"] == 1
    assert "canonical-campaign-status-transition-audit" in audit_text


@pytest.mark.asyncio
async def test_run_async_routes_first_autonomous_profit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "first-autonomous-profit-status",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "905a408c-7d8e-4fc7-ad3b-9ff637005d73",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])

    async def _fake_status(**kwargs):
        assert kwargs["runtime_campaign_id"] == 2
        return {"status": "WAITING_FOR_EXECUTABLE_SIGNAL", "completion_percent": 33}

    monkeypatch.setattr(operator_main, "first_autonomous_profit_status", _fake_status)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["status"] == "WAITING_FOR_EXECUTABLE_SIGNAL"
    assert "completion_percent" in text


@pytest.mark.asyncio
async def test_run_async_routes_campaign_unattended_eligibility_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "campaign-unattended-eligibility-audit",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])

    async def _fake_audit(**kwargs):
        assert kwargs["campaign_version"] == 1
        assert kwargs["provider"] == "kraken_spot"
        return {
            "root_cause_code": "ELIGIBLE",
            "unattended_scan": {"would_appear_in_unattended_candidate_list_today": True},
        }

    monkeypatch.setattr(operator_main, "campaign_unattended_eligibility_audit", _fake_audit)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["root_cause_code"] == "ELIGIBLE"
    assert "would_appear_in_unattended_candidate_list_today" in text


@pytest.mark.asyncio
async def test_run_async_routes_historical_buy_campaign_replay_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "historical-buy-campaign-replay-audit",
        "--decision-id",
        "939b4ea0-4d25-4970-8eff-0b7596c7557d",
        "--campaign-id",
        "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        "--campaign-version",
        "1",
        "--runtime-campaign-id",
        "2",
        "--paper-account-id",
        "8e76a2fa-ae85-45c6-95d1-798cce8f8cc9",
        "--live-trading-profile-id",
        "9da09ae9-475e-41e8-b2c2-717ba5acfa3d",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--matching-sell-decision-id",
        "cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28",
        "--json",
    ])

    async def _fake_audit(**kwargs):
        assert str(kwargs["decision_id"]) == "939b4ea0-4d25-4970-8eff-0b7596c7557d"
        assert str(kwargs["matching_sell_decision_id"]) == "cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28"
        return {
            "primary_blocker": "READY_PACKAGE_ELIGIBLE",
            "current_campaign_simulation": {"campaign_replay_outcome": "READY_PACKAGE_ELIGIBLE"},
        }

    monkeypatch.setattr(operator_main, "historical_buy_campaign_replay_audit", _fake_audit)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["primary_blocker"] == "READY_PACKAGE_ELIGIBLE"
    assert "campaign_replay_outcome" in text


@pytest.mark.asyncio
async def test_run_async_routes_buy_opportunity_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "buy-opportunity-diagnostic",
        "--json",
    ])

    async def _fake_diagnostic():
        return {
            "totals": {
                "strategy_evaluations": 7,
                "buy_opportunities": 2,
                "sell_opportunities": 1,
                "hold_decisions": 4,
                "ready_packages": 1,
            },
            "summary": {
                "buy_opportunities": 2,
                "ready_packages": 1,
                "primary_blocker": "Risk",
            },
        }

    monkeypatch.setattr(operator_main, "buy_opportunity_diagnostic", _fake_diagnostic)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["summary"]["primary_blocker"] == "Risk"
    assert "buy_opportunities" in text


@pytest.mark.asyncio
async def test_run_async_routes_hold_decision_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse_args([
        "hold-decision-diagnostic",
        "--json",
    ])

    async def _fake_diagnostic():
        return {
            "totals": {
                "strategy_evaluations": 7,
                "buy_opportunities": 2,
                "sell_opportunities": 1,
                "hold_decisions": 4,
            },
            "summary": {
                "strategy_evaluations": 7,
                "buy_opportunities": 2,
                "sell_opportunities": 1,
                "hold_decisions": 4,
                "most_common_hold_reason": "non_positive_net_edge",
                "most_common_unmet_buy_condition": "expected_net_dollars_positive_for_buy",
            },
        }

    monkeypatch.setattr(operator_main, "hold_decision_diagnostic", _fake_diagnostic)
    code, payload, text = await operator_main._run_async(args)

    assert code == 0
    assert payload["summary"]["most_common_hold_reason"] == "non_positive_net_edge"
    assert "hold_decisions" in text


def test_parse_rejects_nonexistent_canonical_campaign_binding_status_command() -> None:
    with pytest.raises(SystemExit):
        parse_args([
            "canonical-campaign-binding-status",
            "--campaign-id",
            "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
        ])


def test_parse_canonical_preview_package_commands() -> None:
    create_args = parse_args([
        "canonical-preview-package-create",
        "--campaign-id",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "--campaign-version",
        "1",
        "--paper-account-id",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "--live-trading-profile-id",
        "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--idempotency-key",
        "pkg-1",
        "--json",
    ])
    assert create_args.command == "canonical-preview-package-create"
    assert create_args.json_output is True

    show_args = parse_args([
        "canonical-preview-package-show",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
    ])
    assert show_args.command == "canonical-preview-package-show"

    readiness_args = parse_args([
        "canonical-preview-package-readiness",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
    ])
    assert readiness_args.command == "canonical-preview-package-readiness"

    history_args = parse_args([
        "canonical-preview-package-history",
        "--campaign-id",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "--campaign-version",
        "1",
        "--limit",
        "5",
    ])
    assert history_args.command == "canonical-preview-package-history"
    assert history_args.limit == 5


def test_parse_canonical_package_authorize_dry_run_and_activate() -> None:
    authorize_args = parse_args([
        "canonical-preview-package-authorize",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "--rationale",
        "bounded proving",
        "--expires-at",
        "2026-01-01T00:00:00Z",
        "--idempotency-key",
        "auth-1",
        "--no-leverage",
    ])
    assert authorize_args.command == "canonical-preview-package-authorize"
    assert authorize_args.no_leverage is True

    dry_run_args = parse_args([
        "canonical-preview-package-dry-run",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "--approval-event-id",
        "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "--idempotency-token",
        "dry-1",
    ])
    assert dry_run_args.command == "canonical-preview-package-dry-run"

    activate_args = parse_args([
        "canonical-proving-activate",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "--approval-event-id",
        "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "--dry-run-live-crypto-order-id",
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "--expires-at",
        "2026-01-01T00:30:00Z",
        "--idempotency-key",
        "act-1",
        "--confirm",
    ])
    assert activate_args.command == "canonical-proving-activate"
    assert activate_args.confirm is True


def test_parse_canonical_proving_pause_and_revoke() -> None:
    pause_args = parse_args([
        "canonical-proving-pause",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "--reason",
        "safety intervention",
        "--idempotency-key",
        "pause-1",
    ])
    assert pause_args.command == "canonical-proving-pause"
    assert pause_args.reason == "safety intervention"

    revoke_args = parse_args([
        "canonical-proving-revoke",
        "--package-id",
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "--reason",
        "authority revoked",
        "--idempotency-key",
        "revoke-1",
    ])
    assert revoke_args.command == "canonical-proving-revoke"
    assert revoke_args.reason == "authority revoked"


def test_parse_legacy_campaign_transition_commands() -> None:
    readiness = parse_args([
        "legacy-campaign-transition-readiness",
        "--legacy-campaign-id",
        "11111111-1111-1111-1111-111111111111",
        "--canonical-campaign-id",
        "22222222-2222-2222-2222-222222222222",
        "--canonical-campaign-version",
        "1",
        "--paper-account-id",
        "33333333-3333-3333-3333-333333333333",
        "--live-trading-profile-id",
        "44444444-4444-4444-4444-444444444444",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--json",
    ])
    assert readiness.command == "legacy-campaign-transition-readiness"
    assert readiness.legacy_campaign_id == UUID("11111111-1111-1111-1111-111111111111")
    assert readiness.canonical_campaign_id == UUID("22222222-2222-2222-2222-222222222222")
    assert readiness.canonical_campaign_version == 1
    assert readiness.confirm is False

    execute = parse_args([
        "legacy-campaign-transition-execute",
        "--legacy-campaign-id",
        "11111111-1111-1111-1111-111111111111",
        "--canonical-campaign-id",
        "22222222-2222-2222-2222-222222222222",
        "--canonical-campaign-version",
        "1",
        "--paper-account-id",
        "33333333-3333-3333-3333-333333333333",
        "--live-trading-profile-id",
        "44444444-4444-4444-4444-444444444444",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--confirm",
    ])
    assert execute.command == "legacy-campaign-transition-execute"
    assert execute.confirm is True

    audit = parse_args([
        "legacy-campaign-transition-audit",
        "--legacy-campaign-id",
        "11111111-1111-1111-1111-111111111111",
        "--limit",
        "5",
    ])
    assert audit.command == "legacy-campaign-transition-audit"
    assert audit.limit == 5

    rollback = parse_args([
        "legacy-campaign-transition-rollback",
        "--legacy-campaign-id",
        "11111111-1111-1111-1111-111111111111",
        "--canonical-campaign-id",
        "22222222-2222-2222-2222-222222222222",
        "--canonical-campaign-version",
        "1",
        "--paper-account-id",
        "33333333-3333-3333-3333-333333333333",
        "--live-trading-profile-id",
        "44444444-4444-4444-4444-444444444444",
        "--provider",
        "kraken_spot",
        "--environment",
        "production",
        "--product",
        "BTC-USD",
        "--confirm",
    ])
    assert rollback.command == "legacy-campaign-transition-rollback"
    assert rollback.confirm is True
