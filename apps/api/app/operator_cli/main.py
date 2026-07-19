from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.operator_cli.formatting import (
    render_buy_opportunity_diagnostic_text,
    render_canonical_proving_commission_status_text,
    render_hold_decision_diagnostic_text,
    render_candles_text,
    render_execution_forensics_text,
    render_json,
    render_roster_text,
    render_scorecards_text,
    render_watch_text,
    render_preview_show_text,
    render_preview_text,
    resolve_render_options,
    render_status_text,
    render_venue_commission_text,
)
from app.operator_cli.service import (
        buy_opportunity_diagnostic,
    hold_decision_diagnostic,
    activate_canonical_proving_campaign_bundle,
    bind_canonical_campaign_runtime,
    authorize_canonical_preview_package_bundle,
    canonical_campaign_authority_audit,
    canonical_paper_cash_causality_audit,
    canonical_proving_account_transition_execute,
    canonical_proving_account_transition_preview,
    canonical_proving_cap_transition_execute,
    canonical_proving_cap_transition_preview,
    canonical_preview_package_history,
    canonical_preview_package_readiness,
    canonical_proving_commission_bundle,
    canonical_proving_commission_status,
    canonical_proving_activation_status,
    mandate_identity_diagnosis,
    canonical_campaign_status_transition_audit,
    canonical_campaign_status_transition_execute,
    canonical_campaign_status_transition_readiness,
    campaign_unattended_eligibility_audit,
    create_canonical_preview_package_bundle,
    activate_venue_commission_run,
    fetch_canonical_campaign_binding_audit,
    fetch_canonical_campaign_binding_status,
    fetch_legacy_campaign_transition_audit,
    fetch_campaign_orchestration_history,
    fetch_campaign_orchestration_preview,
    fetch_campaign_orchestration_readiness,
    fetch_campaign_orchestration_status,
    fetch_commissioned_control_plane_status,
    mutate_commissioned_control_plane_action,
    execute_preview_cycle,
    fetch_venue_commission_readiness,
    fetch_venue_commission_status,
    fetch_candle_readiness,
    fetch_operator_status,
    fetch_preview_evidence,
    fetch_execution_forensics,
    fetch_strategy_scorecards_summary,
    fetch_strategy_roster_summary,
    first_autonomous_profit_status,
    historical_buy_campaign_replay_audit,
    fetch_risk_ledger_diagnosis,
    fetch_watch_status,
    mandate_bootstrap,
    mandate_bootstrap_create,
    mandate_bootstrap_create_status,
    mandate_bootstrap_export,
    mandate_bootstrap_session_validate,
    mandate_governance_readiness_audit,
    refresh_provider_balance_evidence,
    inspect_legacy_campaign_transition,
    pause_canonical_proving_activation_bundle,
    rollback_legacy_campaign_transition,
    revoke_canonical_proving_activation_bundle,
    revoke_venue_commission_run,
    show_canonical_preview_package_bundle,
    dry_run_canonical_preview_package_bundle,
    start_venue_commission_run,
    transition_legacy_campaign_to_canonical_successor,
)


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-color", action="store_true", help="Disable ANSI colors. NO_COLOR=1 is also respected.")
    common.add_argument("--verbose", action="store_true", help="Show raw deterministic codes and extended details.")

    parser = argparse.ArgumentParser(
        prog="operator",
        description="Primary operational interface for OmniTrade preview operations, status, and read-only evidence diagnostics.",
        epilog=(
            "Examples:\n"
            "  ./operator preview\n"
            "  ./operator preview --mandate-id <mandate_uuid> --product-id BTC-USD\n"
            "  ./operator status --symbol BTC --interval 15m\n"
            "  ./operator candles --symbol BTC --exchange kraken_spot\n"
            "  ./operator preview-show --preview-id <preview_uuid>\n"
            "  ./operator watch --symbol BTC --interval 15m\n"
            "  ./operator roster\n"
            "  ./operator scorecards\n"
            "  ./operator execution-forensics --latest\n"
            "  ./operator execution-forensics --since '2 hours ago'\n"
            "  ./operator execution-forensics --cycle <cycle_uuid>\n"
            "  ./operator venue-commission-readiness --provider kraken_spot --product BTC-USD --environment production --amount-usd 5 --hold-minutes 30 --json\n"
            "  ./operator venue-commission-activate --provider kraken_spot --product BTC-USD --environment production --amount-usd 5 --hold-minutes 30 --confirm --json\n"
            "  ./operator venue-commission-start --commissioning-run-id <run_uuid> --confirm --json\n"
            "  ./operator venue-commission-status --commissioning-run-id <run_uuid> --json\n"
            "  ./operator venue-commission-revoke --commissioning-run-id <run_uuid> --confirm --json\n"
            "  ./operator canonical-campaign-readiness --campaign-id <campaign_uuid> --campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --json\n"
            "  ./operator canonical-campaign-bind --campaign-id <campaign_uuid> --campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --confirm --json\n"
            "  ./operator canonical-campaign-authority-audit --campaign-id <campaign_uuid> --campaign-version 1 --cycle-id <cycle_uuid> --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --json\n"
            "  ./operator canonical-paper-cash-causality-audit --campaign-id <campaign_uuid> --campaign-version 1 --runtime-campaign-id <runtime_id> --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --json\n"
            "  ./operator canonical-proving-account-transition-preview --campaign-id <campaign_uuid> --campaign-version 1 --runtime-campaign-id 2 --old-paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --json\n"
            "  ./operator canonical-proving-account-transition-execute --campaign-id <campaign_uuid> --campaign-version 1 --runtime-campaign-id 2 --old-paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --idempotency-key prove-acct-1 --confirm --json\n"
            "  ./operator legacy-campaign-transition-readiness --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --json\n"
            "  ./operator legacy-campaign-transition-execute --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --confirm --json\n"
            "  ./operator legacy-campaign-transition-audit --legacy-campaign-id <legacy_uuid> --json\n"
            "  ./operator legacy-campaign-transition-rollback --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --confirm --json\n"
            "  ./operator risk-ledger-diagnosis --account-id <paper_uuid> --json\n"
            "  ./operator mandate-bootstrap --owner-actor-id operator:human --autonomy-level LEVEL_2 --provider kraken_spot --environment production --exchange-connection-id <conn_uuid> --live-trading-profile-id <profile_uuid> --capital-campaign-id 2 --authorized-capital-usd 25 --max-order-notional-usd 5 --max-open-exposure-usd 10 --max-daily-deployed-usd 10 --max-daily-realized-loss-usd 3 --max-campaign-drawdown-usd 5 --max-consecutive-losses 2 --position-limit 1 --price-evidence-max-age-seconds 30 --max-slippage-bps 25 --max-fee-bps 10 --allowed-products BTC-USD --allowed-order-sides BUY,SELL,HOLD --allowed-strategy-versions ma_crossover@1.0.0 --approval-policy MANDATE_ALLOWED --policy-bundle-json @campaign-2-mandate-policy.json --authorization-method owner_signature --actor operator:human --reason campaign_2_bootstrap --idempotency-key mandate-bootstrap-campaign-2 --confirm --json\n"
            "  ./operator status --json\n"
            "  ./operator status --no-color --verbose"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser(
        "preview",
        parents=[common],
        help="Run one autonomous preview cycle (never submits a live order)",
        description="Run one autonomous preview cycle in read-only submission boundary mode.",
    )
    preview.add_argument("--mandate-id", type=UUID, default=None)
    preview.add_argument("--actor", type=str, default="operator:human")
    preview.add_argument("--product-id", type=str, default="BTC-USD")
    preview.add_argument("--strategy-interval", type=str, default="15m")
    preview.add_argument("--trigger", type=str, default="operator_cli")
    preview.add_argument("--idempotency-seed", type=str, default=None)
    preview.add_argument("--reuse-idempotency-key", action="store_true", help="Reuse the orchestrator-derived idempotency key instead of minting a fresh preview seed.")
    preview.add_argument("--software-build-version", type=str, default=None)
    preview.add_argument("--forced-action", choices=["BUY", "SELL", "HOLD"], default=None)
    preview.add_argument("--json", action="store_true", dest="json_output")

    preview_show = subparsers.add_parser(
        "preview-show",
        parents=[common],
        help="Show persisted preview evidence and linked decision records",
        description="Inspect one persisted preview and linked decision intelligence evidence.",
    )
    preview_show.add_argument("--preview-id", type=UUID, required=True)
    preview_show.add_argument("--json", action="store_true", dest="json_output")

    candles = subparsers.add_parser(
        "candles",
        parents=[common],
        help="Inspect candle freshness/readiness for one symbol",
        description="Read-only candle freshness and readiness inspection.",
    )
    candles.add_argument("--symbol", type=str, required=True)
    candles.add_argument("--interval", type=str, default="15m")
    candles.add_argument("--exchange", type=str, default=None)
    candles.add_argument("--max-age-minutes", type=int, default=30)
    candles.add_argument("--lookback-limit", type=int, default=200)
    candles.add_argument("--json", action="store_true", dest="json_output")

    status = subparsers.add_parser(
        "status",
        parents=[common],
        help="Show current operator-safe status summary",
        description="Mission Control status view for operational health and safety boundaries.",
    )
    status.add_argument("--mandate-id", type=UUID, default=None)
    status.add_argument("--symbol", type=str, default=None)
    status.add_argument("--interval", type=str, default="15m")
    status.add_argument("--exchange", type=str, default=None)
    status.add_argument("--max-age-minutes", type=int, default=30)
    status.add_argument("--json", action="store_true", dest="json_output")

    watch = subparsers.add_parser(
        "watch",
        parents=[common],
        help="Continuously refresh operator status in read-only mode",
        description="Read-only watch loop for latest decisions, heartbeat, candles, and system health.",
    )
    watch.add_argument("--mandate-id", type=UUID, default=None)
    watch.add_argument("--symbol", type=str, default="BTC")
    watch.add_argument("--interval", type=str, default="15m")
    watch.add_argument("--exchange", type=str, default=None)
    watch.add_argument("--max-age-minutes", type=int, default=30)
    watch.add_argument("--refresh-seconds", type=int, default=5)
    watch.add_argument("--json", action="store_true", dest="json_output")

    roster = subparsers.add_parser(
        "roster",
        parents=[common],
        help="Show latest shadow strategy roster run and proposals",
        description="Read-only Strategy Roster summary for the latest completed candle.",
    )
    roster.add_argument("--provider", type=str, default="kraken_spot")
    roster.add_argument("--product-id", type=str, default="BTC-USD")
    roster.add_argument("--interval", type=str, default="15m")
    roster.add_argument("--json", action="store_true", dest="json_output")

    scorecards = subparsers.add_parser(
        "scorecards",
        parents=[common],
        help="Show deterministic strategy outcome scorecards",
        description="Read-only Strategy Outcome scorecards across evaluated horizons.",
    )
    scorecards.add_argument("--provider", type=str, default="kraken_spot")
    scorecards.add_argument("--product-id", type=str, default="BTC-USD")
    scorecards.add_argument("--interval", type=str, default="15m")
    scorecards.add_argument("--json", action="store_true", dest="json_output")

    execution_forensics = subparsers.add_parser(
        "execution-forensics",
        parents=[common],
        help="Trace why capital did or did not move for autonomous cycles",
        description="Read-only autonomous-cycle signal-to-execution-to-accounting forensics.",
    )
    selector = execution_forensics.add_mutually_exclusive_group(required=True)
    selector.add_argument("--since", type=str, default=None)
    selector.add_argument("--cycle", type=UUID, default=None)
    selector.add_argument("--latest", action="store_true")
    execution_forensics.add_argument("--json", action="store_true", dest="json_output")

    venue_readiness = subparsers.add_parser(
        "venue-commission-readiness",
        parents=[common],
        help="Evaluate whether Kraken First Flight commissioning can be activated safely",
        description="Read-only readiness checks for a bounded Kraken First Flight commissioning run.",
    )
    venue_readiness.add_argument("--provider", type=str, required=True)
    venue_readiness.add_argument("--product", type=str, required=True)
    venue_readiness.add_argument("--environment", type=str, required=True)
    venue_readiness.add_argument("--amount-usd", type=Decimal, required=True)
    venue_readiness.add_argument("--hold-minutes", type=int, required=True)
    venue_readiness.add_argument("--json", action="store_true", dest="json_output")

    venue_activate = subparsers.add_parser(
        "venue-commission-activate",
        parents=[common],
        help="Create one bounded Kraken First Flight commissioning run in ACTIVE state",
        description="Operator-confirmed activation for one bounded Kraken First Flight commissioning run.",
    )
    venue_activate.add_argument("--provider", type=str, required=True)
    venue_activate.add_argument("--product", type=str, required=True)
    venue_activate.add_argument("--environment", type=str, required=True)
    venue_activate.add_argument("--amount-usd", type=Decimal, required=True)
    venue_activate.add_argument("--hold-minutes", type=int, required=True)
    venue_activate.add_argument("--actor", type=str, default="operator:human")
    venue_activate.add_argument("--confirm", action="store_true")
    venue_activate.add_argument("--json", action="store_true", dest="json_output")

    venue_start = subparsers.add_parser(
        "venue-commission-start",
        parents=[common],
        help="Explicitly authorize the forced Kraken commissioning BUY",
        description="Starts a previously activated commissioning run and advances its state machine.",
    )
    venue_start.add_argument("--commissioning-run-id", type=UUID, required=True)
    venue_start.add_argument("--actor", type=str, default="operator:human")
    venue_start.add_argument("--confirm", action="store_true")
    venue_start.add_argument("--json", action="store_true", dest="json_output")

    venue_status = subparsers.add_parser(
        "venue-commission-status",
        parents=[common],
        help="Show bounded Kraken First Flight commissioning status",
        description="Read the current persisted state for one commissioning run.",
    )
    venue_status.add_argument("--commissioning-run-id", type=UUID, required=True)
    venue_status.add_argument("--json", action="store_true", dest="json_output")

    venue_revoke = subparsers.add_parser(
        "venue-commission-revoke",
        parents=[common],
        help="Emergency revoke for bounded Kraken First Flight commissioning",
        description="Revokes an active commissioning run or fail-closes it to manual review when unsafe.",
    )
    venue_revoke.add_argument("--commissioning-run-id", type=UUID, required=True)
    venue_revoke.add_argument("--actor", type=str, default="operator:human")
    venue_revoke.add_argument("--confirm", action="store_true")
    venue_revoke.add_argument("--json", action="store_true", dest="json_output")

    canonical_readiness = subparsers.add_parser(
        "canonical-campaign-readiness",
        parents=[common],
        help="Evaluate whether the canonical campaign can be bound safely",
        description="Read-only canonical campaign binding readiness diagnostics.",
    )
    canonical_readiness.add_argument("--campaign-id", type=UUID, required=True)
    canonical_readiness.add_argument("--campaign-version", type=int, required=True)
    canonical_readiness.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_readiness.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_readiness.add_argument("--provider", type=str, required=True)
    canonical_readiness.add_argument("--environment", type=str, required=True)
    canonical_readiness.add_argument("--product", type=str, required=True)
    canonical_readiness.add_argument("--actor", type=str, default="operator:human")
    canonical_readiness.add_argument("--confirm", action="store_true")
    canonical_readiness.add_argument("--json", action="store_true", dest="json_output")

    canonical_bind = subparsers.add_parser(
        "canonical-campaign-bind",
        parents=[common],
        help="Bind the canonical runtime campaign to the intended paper account",
        description="Operator-confirmed canonical campaign binding for a single runtime row.",
    )
    canonical_bind.add_argument("--campaign-id", type=UUID, required=True)
    canonical_bind.add_argument("--campaign-version", type=int, required=True)
    canonical_bind.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_bind.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_bind.add_argument("--provider", type=str, required=True)
    canonical_bind.add_argument("--environment", type=str, required=True)
    canonical_bind.add_argument("--product", type=str, required=True)
    canonical_bind.add_argument("--actor", type=str, default="operator:human")
    canonical_bind.add_argument("--confirm", action="store_true")
    canonical_bind.add_argument("--json", action="store_true", dest="json_output")

    canonical_status_transition_readiness = subparsers.add_parser(
        "canonical-campaign-status-transition-readiness",
        parents=[common],
        help="Read-only readiness for canonical campaign status transition",
        description="Read-only sanctioned readiness checks for canonical campaign DRAFT to READY status transition.",
    )
    canonical_status_transition_readiness.add_argument("--campaign-id", type=UUID, required=True)
    canonical_status_transition_readiness.add_argument("--campaign-version", type=int, required=True)
    canonical_status_transition_readiness.add_argument("--runtime-campaign-id", type=int, required=True)
    canonical_status_transition_readiness.add_argument("--expected-current-status", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--target-status", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_status_transition_readiness.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_status_transition_readiness.add_argument("--provider", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--environment", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--product", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--actor", type=str, required=True)
    canonical_status_transition_readiness.add_argument("--json", action="store_true", dest="json_output")

    canonical_status_transition_execute = subparsers.add_parser(
        "canonical-campaign-status-transition-execute",
        parents=[common],
        help="Execute canonical campaign status transition",
        description="Operator-confirmed sanctioned canonical campaign status transition.",
    )
    canonical_status_transition_execute.add_argument("--campaign-id", type=UUID, required=True)
    canonical_status_transition_execute.add_argument("--campaign-version", type=int, required=True)
    canonical_status_transition_execute.add_argument("--runtime-campaign-id", type=int, required=True)
    canonical_status_transition_execute.add_argument("--expected-current-status", type=str, required=True)
    canonical_status_transition_execute.add_argument("--target-status", type=str, required=True)
    canonical_status_transition_execute.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_status_transition_execute.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_status_transition_execute.add_argument("--provider", type=str, required=True)
    canonical_status_transition_execute.add_argument("--environment", type=str, required=True)
    canonical_status_transition_execute.add_argument("--product", type=str, required=True)
    canonical_status_transition_execute.add_argument("--actor", type=str, required=True)
    canonical_status_transition_execute.add_argument("--idempotency-key", type=str, required=True)
    canonical_status_transition_execute.add_argument("--confirm", action="store_true")
    canonical_status_transition_execute.add_argument("--json", action="store_true", dest="json_output")

    canonical_status_transition_audit = subparsers.add_parser(
        "canonical-campaign-status-transition-audit",
        parents=[common],
        help="Show canonical campaign status transition audit",
        description="Read-only immutable audit and post-transition unattended eligibility for canonical status transition.",
    )
    canonical_status_transition_audit.add_argument("--campaign-id", type=UUID, required=True)
    canonical_status_transition_audit.add_argument("--campaign-version", type=int, required=True)
    canonical_status_transition_audit.add_argument("--runtime-campaign-id", type=int, required=True)
    canonical_status_transition_audit.add_argument("--expected-current-status", type=str, required=True)
    canonical_status_transition_audit.add_argument("--target-status", type=str, required=True)
    canonical_status_transition_audit.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_status_transition_audit.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_status_transition_audit.add_argument("--provider", type=str, required=True)
    canonical_status_transition_audit.add_argument("--environment", type=str, required=True)
    canonical_status_transition_audit.add_argument("--product", type=str, required=True)
    canonical_status_transition_audit.add_argument("--actor", type=str, required=True)
    canonical_status_transition_audit.add_argument("--limit", type=int, default=20)
    canonical_status_transition_audit.add_argument("--json", action="store_true", dest="json_output")

    canonical_audit = subparsers.add_parser(
        "canonical-campaign-binding-audit",
        parents=[common],
        help="Show immutable audit evidence for canonical campaign binding",
        description="Read-only binding audit evidence for one canonical campaign.",
    )
    canonical_audit.add_argument("--campaign-id", type=UUID, required=True)
    canonical_audit.add_argument("--limit", type=int, default=20)
    canonical_audit.add_argument("--json", action="store_true", dest="json_output")

    canonical_authority = subparsers.add_parser(
        "canonical-campaign-authority-audit",
        parents=[common],
        help="Read-only authority and identity audit for one canonical campaign and cycle",
        description="Read-only canonical campaign authority audit using exact identities.",
    )
    canonical_authority.add_argument("--campaign-id", type=UUID, required=True)
    canonical_authority.add_argument("--campaign-version", type=int, required=True)
    canonical_authority.add_argument("--cycle-id", type=UUID, required=True)
    canonical_authority.add_argument("--paper-account-id", type=UUID, required=True)
    canonical_authority.add_argument("--live-trading-profile-id", type=UUID, required=True)
    canonical_authority.add_argument("--provider", type=str, required=True)
    canonical_authority.add_argument("--environment", type=str, required=True)
    canonical_authority.add_argument("--product", type=str, required=True)
    canonical_authority.add_argument("--json", action="store_true", dest="json_output")

    paper_cash_causality = subparsers.add_parser(
        "canonical-paper-cash-causality-audit",
        parents=[common],
        help="Read-only causality reconstruction for canonical paper-account cash",
        description="Read-only canonical paper-account cash causality audit with exact identities.",
    )
    paper_cash_causality.add_argument("--campaign-id", type=UUID, required=True)
    paper_cash_causality.add_argument("--campaign-version", type=int, required=True)
    paper_cash_causality.add_argument("--runtime-campaign-id", type=int, required=True)
    paper_cash_causality.add_argument("--paper-account-id", type=UUID, required=True)
    paper_cash_causality.add_argument("--live-trading-profile-id", type=UUID, required=True)
    paper_cash_causality.add_argument("--provider", type=str, required=True)
    paper_cash_causality.add_argument("--environment", type=str, required=True)
    paper_cash_causality.add_argument("--product", type=str, required=True)
    paper_cash_causality.add_argument("--json", action="store_true", dest="json_output")

    proving_account_preview = subparsers.add_parser(
        "canonical-proving-account-transition-preview",
        parents=[common],
        help="Read-only preview for dedicated canonical proving paper-account transition",
        description="Read-only preview for dedicated canonical proving account transition.",
    )
    proving_account_preview.add_argument("--campaign-id", type=UUID, required=True)
    proving_account_preview.add_argument("--campaign-version", type=int, required=True)
    proving_account_preview.add_argument("--runtime-campaign-id", type=int, required=True)
    proving_account_preview.add_argument("--old-paper-account-id", type=UUID, required=True)
    proving_account_preview.add_argument("--live-trading-profile-id", type=UUID, required=True)
    proving_account_preview.add_argument("--provider", type=str, required=True)
    proving_account_preview.add_argument("--environment", type=str, required=True)
    proving_account_preview.add_argument("--product", type=str, required=True)
    proving_account_preview.add_argument("--actor", type=str, required=True)
    proving_account_preview.add_argument("--confirm", action="store_true")
    proving_account_preview.add_argument("--json", action="store_true", dest="json_output")

    proving_account_execute = subparsers.add_parser(
        "canonical-proving-account-transition-execute",
        parents=[common],
        help="Execute dedicated canonical proving paper-account transition",
        description="Operator-confirmed dedicated canonical proving account transition.",
    )
    proving_account_execute.add_argument("--campaign-id", type=UUID, required=True)
    proving_account_execute.add_argument("--campaign-version", type=int, required=True)
    proving_account_execute.add_argument("--runtime-campaign-id", type=int, required=True)
    proving_account_execute.add_argument("--old-paper-account-id", type=UUID, required=True)
    proving_account_execute.add_argument("--live-trading-profile-id", type=UUID, required=True)
    proving_account_execute.add_argument("--provider", type=str, required=True)
    proving_account_execute.add_argument("--environment", type=str, required=True)
    proving_account_execute.add_argument("--product", type=str, required=True)
    proving_account_execute.add_argument("--actor", type=str, required=True)
    proving_account_execute.add_argument("--idempotency-key", type=str, required=True)
    proving_account_execute.add_argument("--expected-evidence-source-id", type=str, default=None)
    proving_account_execute.add_argument("--expected-evidence-observed-at", type=str, default=None)
    proving_account_execute.add_argument("--confirm", action="store_true")
    proving_account_execute.add_argument("--json", action="store_true", dest="json_output")

    refresh_provider_balances = subparsers.add_parser(
        "exchange-connection-refresh-balances",
        parents=[common],
        help="Refresh provider balance evidence through sanctioned exchange-connection flow",
        description="Refreshes persisted provider balance evidence without any order submission.",
    )
    refresh_provider_balances.add_argument("--provider", type=str, required=True)
    refresh_provider_balances.add_argument("--environment", type=str, required=True)
    refresh_provider_balances.add_argument("--actor", type=str, default="operator:human")
    refresh_provider_balances.add_argument("--json", action="store_true", dest="json_output")

    proving_cap_preview = subparsers.add_parser(
        "canonical-proving-cap-transition-preview",
        parents=[common],
        help="Preview exact $5 proving cap transition for canonical campaign definition",
        description="Read-only preview for exact proving cap transition to maximum_position_size=5 and maximum_total_exposure=5.",
    )
    proving_cap_preview.add_argument("--campaign-id", type=UUID, required=True)
    proving_cap_preview.add_argument("--campaign-version", type=int, required=True)
    proving_cap_preview.add_argument("--json", action="store_true", dest="json_output")

    proving_cap_execute = subparsers.add_parser(
        "canonical-proving-cap-transition-execute",
        parents=[common],
        help="Execute exact $5 proving cap transition for canonical campaign definition",
        description="Operator-confirmed exact proving cap transition with idempotent audit.",
    )
    proving_cap_execute.add_argument("--campaign-id", type=UUID, required=True)
    proving_cap_execute.add_argument("--campaign-version", type=int, required=True)
    proving_cap_execute.add_argument("--actor", type=str, default="operator:human")
    proving_cap_execute.add_argument("--idempotency-key", type=str, required=True)
    proving_cap_execute.add_argument("--confirm", action="store_true")
    proving_cap_execute.add_argument("--json", action="store_true", dest="json_output")

    legacy_transition_readiness = subparsers.add_parser(
        "legacy-campaign-transition-readiness",
        parents=[common],
        help="Read-only safety checks before superseding a legacy campaign",
        description="Read-only legacy-to-canonical campaign transition readiness diagnostics.",
    )
    legacy_transition_readiness.add_argument("--legacy-campaign-id", type=UUID, required=True)
    legacy_transition_readiness.add_argument("--canonical-campaign-id", type=UUID, required=True)
    legacy_transition_readiness.add_argument("--canonical-campaign-version", type=int, required=True)
    legacy_transition_readiness.add_argument("--paper-account-id", type=UUID, required=True)
    legacy_transition_readiness.add_argument("--live-trading-profile-id", type=UUID, required=True)
    legacy_transition_readiness.add_argument("--provider", type=str, required=True)
    legacy_transition_readiness.add_argument("--environment", type=str, required=True)
    legacy_transition_readiness.add_argument("--product", type=str, required=True)
    legacy_transition_readiness.add_argument("--actor", type=str, default="operator:human")
    legacy_transition_readiness.add_argument("--confirm", action="store_true")
    legacy_transition_readiness.add_argument("--json", action="store_true", dest="json_output")

    legacy_transition_execute = subparsers.add_parser(
        "legacy-campaign-transition-execute",
        parents=[common],
        help="Supersede a legacy campaign after strict safety checks",
        description="Operator-confirmed immutable transition from legacy campaign to canonical successor.",
    )
    legacy_transition_execute.add_argument("--legacy-campaign-id", type=UUID, required=True)
    legacy_transition_execute.add_argument("--canonical-campaign-id", type=UUID, required=True)
    legacy_transition_execute.add_argument("--canonical-campaign-version", type=int, required=True)
    legacy_transition_execute.add_argument("--paper-account-id", type=UUID, required=True)
    legacy_transition_execute.add_argument("--live-trading-profile-id", type=UUID, required=True)
    legacy_transition_execute.add_argument("--provider", type=str, required=True)
    legacy_transition_execute.add_argument("--environment", type=str, required=True)
    legacy_transition_execute.add_argument("--product", type=str, required=True)
    legacy_transition_execute.add_argument("--actor", type=str, default="operator:human")
    legacy_transition_execute.add_argument("--confirm", action="store_true")
    legacy_transition_execute.add_argument("--json", action="store_true", dest="json_output")

    legacy_transition_audit = subparsers.add_parser(
        "legacy-campaign-transition-audit",
        parents=[common],
        help="Show immutable audit evidence for legacy campaign transitions",
        description="Read-only transition/rollback audit evidence for one legacy campaign.",
    )
    legacy_transition_audit.add_argument("--legacy-campaign-id", type=UUID, required=True)
    legacy_transition_audit.add_argument("--limit", type=int, default=20)
    legacy_transition_audit.add_argument("--json", action="store_true", dest="json_output")

    legacy_transition_rollback = subparsers.add_parser(
        "legacy-campaign-transition-rollback",
        parents=[common],
        help="Emergency rollback of a prior legacy transition when safety checks pass",
        description="Operator-confirmed emergency rollback for a specific prior canonical successor transition.",
    )
    legacy_transition_rollback.add_argument("--legacy-campaign-id", type=UUID, required=True)
    legacy_transition_rollback.add_argument("--canonical-campaign-id", type=UUID, required=True)
    legacy_transition_rollback.add_argument("--canonical-campaign-version", type=int, required=True)
    legacy_transition_rollback.add_argument("--paper-account-id", type=UUID, required=True)
    legacy_transition_rollback.add_argument("--live-trading-profile-id", type=UUID, required=True)
    legacy_transition_rollback.add_argument("--provider", type=str, required=True)
    legacy_transition_rollback.add_argument("--environment", type=str, required=True)
    legacy_transition_rollback.add_argument("--product", type=str, required=True)
    legacy_transition_rollback.add_argument("--actor", type=str, default="operator:human")
    legacy_transition_rollback.add_argument("--confirm", action="store_true")
    legacy_transition_rollback.add_argument("--json", action="store_true", dest="json_output")

    risk_diagnosis = subparsers.add_parser(
        "risk-ledger-diagnosis",
        parents=[common],
        help="Explain the persisted risk inputs, formulas, and snapshot deltas for one paper account",
        description="Read-only risk ledger diagnosis for one paper account.",
    )
    risk_diagnosis.add_argument("--account-id", type=UUID, required=True)
    risk_diagnosis.add_argument("--json", action="store_true", dest="json_output")

    campaign_readiness = subparsers.add_parser(
        "campaign-orchestration-readiness",
        parents=[common],
        help="Show campaign orchestration readiness for the Kraken 15m trigger",
        description="Read-only campaign orchestration readiness diagnostics.",
    )
    campaign_readiness.add_argument("--campaign-id", type=UUID, default=None)
    campaign_readiness.add_argument("--version", type=int, default=None)
    campaign_readiness.add_argument("--json", action="store_true", dest="json_output")

    campaign_preview = subparsers.add_parser(
        "campaign-orchestration-preview",
        parents=[common],
        help="Run the read-only campaign orchestration preview cycle",
        description="Preview-only campaign orchestration run on the latest Kraken BTC 15m candle.",
    )
    campaign_preview.add_argument("--campaign-id", type=UUID, default=None)
    campaign_preview.add_argument("--version", type=int, default=None)
    campaign_preview.add_argument("--json", action="store_true", dest="json_output")

    campaign_status = subparsers.add_parser(
        "campaign-orchestration-status",
        parents=[common],
        help="Show the latest campaign orchestration status",
        description="Read-only campaign orchestration status summary.",
    )
    campaign_status.add_argument("--campaign-id", type=UUID, required=True)
    campaign_status.add_argument("--version", type=int, default=None)
    campaign_status.add_argument("--json", action="store_true", dest="json_output")

    campaign_history = subparsers.add_parser(
        "campaign-orchestration-history",
        parents=[common],
        help="Show persisted campaign orchestration history",
        description="Read-only campaign orchestration history.",
    )
    campaign_history.add_argument("--campaign-id", type=UUID, required=True)
    campaign_history.add_argument("--version", type=int, default=None)
    campaign_history.add_argument("--limit", type=int, default=20)
    campaign_history.add_argument("--json", action="store_true", dest="json_output")

    commissioned_status = subparsers.add_parser(
        "commissioned-control-plane-status",
        parents=[common],
        help="Show commissioned campaign control-plane status",
        description="Read-only commissioned control-plane view with lifecycle, risk, reconciliation, and pending actions.",
    )
    commissioned_status.add_argument("--campaign-id", type=UUID, required=True)
    commissioned_status.add_argument("--version", type=int, required=True)
    commissioned_status.add_argument("--json", action="store_true", dest="json_output")

    commissioned_action = subparsers.add_parser(
        "commissioned-control-plane-action",
        parents=[common],
        help="Apply commissioned operator control action",
        description="Mutates operator control metadata only (acknowledge/cancel/pause/resume) with no trade execution.",
    )
    commissioned_action.add_argument("--campaign-id", type=UUID, required=True)
    commissioned_action.add_argument("--version", type=int, required=True)
    commissioned_action.add_argument("--actor", type=str, default="operator:human")
    commissioned_action.add_argument("--action", type=str, required=True, choices=["acknowledge", "cancel", "pause", "resume"])
    commissioned_action.add_argument("--idempotency-key", type=str, required=True)
    commissioned_action.add_argument("--reason", type=str, default=None)
    commissioned_action.add_argument("--json", action="store_true", dest="json_output")

    package_create = subparsers.add_parser(
        "canonical-preview-package-create",
        parents=[common],
        help="Create immutable canonical preview package with strict identity chain",
        description="Creates one canonical preview package and linked crypto order preview evidence.",
    )
    package_create.add_argument("--campaign-id", type=UUID, required=True)
    package_create.add_argument("--campaign-version", type=int, required=True)
    package_create.add_argument("--paper-account-id", type=UUID, required=True)
    package_create.add_argument("--live-trading-profile-id", type=UUID, required=True)
    package_create.add_argument("--provider", type=str, required=True)
    package_create.add_argument("--environment", type=str, required=True)
    package_create.add_argument("--product", type=str, required=True)
    package_create.add_argument("--max-proposed-order-amount", type=Decimal, default=Decimal("5"))
    package_create.add_argument(
        "--commissioning-entry-mode",
        type=str,
        choices=["initial_proving_entry"],
        default=None,
    )
    package_create.add_argument("--actor", type=str, default="operator:human")
    package_create.add_argument("--idempotency-key", type=str, required=True)
    package_create.add_argument("--json", action="store_true", dest="json_output")

    package_show = subparsers.add_parser(
        "canonical-preview-package-show",
        parents=[common],
        help="Show one canonical preview package",
        description="Reads one immutable canonical preview package and readiness checks.",
    )
    package_show.add_argument("--package-id", type=UUID, required=True)
    package_show.add_argument("--json", action="store_true", dest="json_output")

    package_readiness = subparsers.add_parser(
        "canonical-preview-package-readiness",
        parents=[common],
        help="Run readiness checks for one canonical preview package",
        description="Read-only readiness checks for a canonical preview package.",
    )
    package_readiness.add_argument("--package-id", type=UUID, required=True)
    package_readiness.add_argument("--json", action="store_true", dest="json_output")

    package_history = subparsers.add_parser(
        "canonical-preview-package-history",
        parents=[common],
        help="Show canonical preview package history for a campaign",
        description="Read-only package history, newest first.",
    )
    package_history.add_argument("--campaign-id", type=UUID, required=True)
    package_history.add_argument("--campaign-version", type=int, default=None)
    package_history.add_argument("--limit", type=int, default=20)
    package_history.add_argument("--json", action="store_true", dest="json_output")

    package_authorize = subparsers.add_parser(
        "canonical-preview-package-authorize",
        parents=[common],
        help="Record campaign-scoped approval bound to canonical preview package",
        description="Writes first-live approval with strict package identity scope and no leverage boundary.",
    )
    package_authorize.add_argument("--package-id", type=UUID, required=True)
    package_authorize.add_argument("--actor", type=str, default="operator:human")
    package_authorize.add_argument("--approver-role", type=str, default="operator")
    package_authorize.add_argument("--rationale", type=str, required=True)
    package_authorize.add_argument("--expires-at", type=str, required=True)
    package_authorize.add_argument("--max-order-usd", type=Decimal, default=Decimal("5"))
    package_authorize.add_argument("--max-total-deployed-campaign-capital-usd", type=Decimal, default=Decimal("5"))
    package_authorize.add_argument("--no-leverage", action="store_true")
    package_authorize.add_argument("--idempotency-key", type=str, required=True)
    package_authorize.add_argument("--json", action="store_true", dest="json_output")

    package_dry_run = subparsers.add_parser(
        "canonical-preview-package-dry-run",
        parents=[common],
        help="Execute dry-run from canonical package and approval evidence",
        description="Runs live crypto dry-run using package-bound preview and approval identities.",
    )
    package_dry_run.add_argument("--package-id", type=UUID, required=True)
    package_dry_run.add_argument("--approval-event-id", type=UUID, required=True)
    package_dry_run.add_argument("--operator-identity", type=str, default="operator:human")
    package_dry_run.add_argument("--idempotency-token", type=str, required=True)
    package_dry_run.add_argument("--json", action="store_true", dest="json_output")

    proving_activate = subparsers.add_parser(
        "canonical-proving-activate",
        parents=[common],
        help="Bounded proving activation for one canonical campaign",
        description="Activates proving campaign status only after package, approval, and dry-run evidence pass checks.",
    )
    proving_activate.add_argument("--package-id", type=UUID, required=True)
    proving_activate.add_argument("--approval-event-id", type=UUID, required=True)
    proving_activate.add_argument("--dry-run-live-crypto-order-id", type=UUID, required=True)
    proving_activate.add_argument("--actor", type=str, default="operator:human")
    proving_activate.add_argument("--expires-at", type=str, required=True)
    proving_activate.add_argument("--idempotency-key", type=str, required=True)
    proving_activate.add_argument("--confirm", action="store_true")
    proving_activate.add_argument("--json", action="store_true", dest="json_output")

    proving_status = subparsers.add_parser(
        "canonical-proving-activation-status",
        parents=[common],
        help="Show latest canonical proving activation status",
        description="Read-only status for bounded canonical proving activation.",
    )
    proving_status.add_argument("--package-id", type=UUID, required=True)
    proving_status.add_argument("--json", action="store_true", dest="json_output")

    proving_commission = subparsers.add_parser(
        "canonical-proving-commission",
        parents=[common],
        help="Complete governed commissioned proving from canonical package chain to ACTIVE_POSITION",
        description="Creates or refreshes canonical proving evidence if needed, then commissions, submits one bounded BUY, reconciles ownership, and hands off to autonomous lifecycle management.",
    )
    proving_commission.add_argument("--campaign-id", type=UUID, required=True)
    proving_commission.add_argument("--campaign-version", type=int, required=True)
    proving_commission.add_argument("--paper-account-id", type=UUID, required=True)
    proving_commission.add_argument("--live-trading-profile-id", type=UUID, required=True)
    proving_commission.add_argument("--provider", type=str, required=True)
    proving_commission.add_argument("--environment", type=str, required=True)
    proving_commission.add_argument("--product", type=str, required=True)
    proving_commission.add_argument("--amount-usd", type=Decimal, default=Decimal("5"))
    proving_commission.add_argument("--actor", type=str, default="operator:human")
    proving_commission.add_argument("--approver-role", type=str, default="operator")
    proving_commission.add_argument("--rationale", type=str, required=True)
    proving_commission.add_argument("--no-leverage", action="store_true")
    proving_commission.add_argument("--confirm", action="store_true")
    proving_commission.add_argument("--idempotency-key", type=str, required=True)
    proving_commission.add_argument("--json", action="store_true", dest="json_output")

    proving_commission_status = subparsers.add_parser(
        "canonical-proving-commission-status",
        parents=[common],
        help="Show read-only commissioned proving chain status",
        description="Inspects canonical package, proving activation, commissioned state, live order, and autonomous lifecycle handoff status without executing anything.",
    )
    proving_commission_status.add_argument("--campaign-id", type=UUID, required=True)
    proving_commission_status.add_argument("--campaign-version", type=int, required=True)
    proving_commission_status.add_argument("--paper-account-id", type=UUID, required=True)
    proving_commission_status.add_argument("--live-trading-profile-id", type=UUID, required=True)
    proving_commission_status.add_argument("--provider", type=str, required=True)
    proving_commission_status.add_argument("--environment", type=str, required=True)
    proving_commission_status.add_argument("--product", type=str, required=True)
    proving_commission_status.add_argument("--json", action="store_true", dest="json_output")

    mandate_diagnosis = subparsers.add_parser(
        "mandate-identity-diagnosis",
        parents=[common],
        help="Show the exact mandate/campaign/account identities canonical-proving-commission compares",
        description="Read-only inspection of runtime campaign id, all mandates for the profile, and their capital_campaign_id/paper_account_id scoping, using the same resolution and diagnosis logic as canonical-proving-commission. Performs no writes.",
    )
    mandate_diagnosis.add_argument("--campaign-id", type=UUID, required=True)
    mandate_diagnosis.add_argument("--paper-account-id", type=UUID, required=True)
    mandate_diagnosis.add_argument("--live-trading-profile-id", type=UUID, required=True)
    mandate_diagnosis.add_argument("--provider", type=str, required=True)
    mandate_diagnosis.add_argument("--environment", type=str, required=True)
    mandate_diagnosis.add_argument("--json", action="store_true", dest="json_output")

    mandate_bootstrap_parser = subparsers.add_parser(
        "mandate-bootstrap",
        parents=[common],
        help="Governed end-to-end creation of a new Autonomous Capital Mandate",
        description=(
            "Orchestrates the existing governed mandate lifecycle service functions in order -- "
            "create_mandate, create_mandate_version, apply_mandate_lifecycle_action(SUBMIT_FOR_AUTHORIZATION), "
            "authorize_mandate_version, apply_mandate_lifecycle_action(ACTIVATE) -- the same functions "
            "app/api/routes/autonomous_capital_mandates.py calls. No business logic, validation, or "
            "governance rule is added or bypassed here; this only sequences the existing calls. "
            "Fully idempotent: rerunning with the same --idempotency-key resumes at whatever stage "
            "already completed instead of creating duplicates. Never mutates any pre-existing mandate."
        ),
    )
    mandate_bootstrap_parser.add_argument("--owner-actor-id", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--autonomy-level", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--provider", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--environment", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--exchange-connection-id", type=UUID, required=True)
    mandate_bootstrap_parser.add_argument("--live-trading-profile-id", type=UUID, required=True)
    mandate_bootstrap_parser.add_argument("--paper-account-id", type=UUID, default=None)
    mandate_bootstrap_parser.add_argument("--capital-campaign-id", type=int, default=None, help="capital_campaign_id to scope the new mandate to, e.g. 2 for campaign 2")
    mandate_bootstrap_parser.add_argument("--mandate-expires-at", type=str, default=None)
    mandate_bootstrap_parser.add_argument("--base-currency", type=str, default="USD")
    mandate_bootstrap_parser.add_argument("--authorized-capital-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-order-notional-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-open-exposure-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-daily-deployed-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-daily-realized-loss-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-campaign-drawdown-usd", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-consecutive-losses", type=int, required=True)
    mandate_bootstrap_parser.add_argument("--position-limit", type=int, required=True)
    mandate_bootstrap_parser.add_argument("--price-evidence-max-age-seconds", type=int, required=True)
    mandate_bootstrap_parser.add_argument("--max-slippage-bps", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--max-fee-bps", type=Decimal, required=True)
    mandate_bootstrap_parser.add_argument("--allowed-products", type=_parse_csv_argument, required=True, help="comma-separated, e.g. BTC-USD,ETH-USD")
    mandate_bootstrap_parser.add_argument("--allowed-order-sides", type=_parse_csv_argument, required=True, help="comma-separated, e.g. BUY,SELL,HOLD")
    mandate_bootstrap_parser.add_argument("--allowed-strategy-versions", type=_parse_csv_argument, required=True)
    mandate_bootstrap_parser.add_argument("--approval-policy", type=str, required=True, help="HUMAN_REQUIRED or MANDATE_ALLOWED")
    mandate_bootstrap_parser.add_argument(
        "--policy-bundle-json",
        type=_parse_mandate_policy_bundle,
        required=True,
        help=(
            "JSON object (or @path/to/file.json) with keys: entry_policy, exit_policy, cooldown_policy, "
            "operating_schedule, reconciliation_policy, kill_switch_policy, owner_acknowledgements, "
            "authorization_evidence_summary, authorization_evidence, deterministic_explanation"
        ),
    )
    mandate_bootstrap_parser.add_argument("--authorization-method", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--authorization-expires-at", type=str, default=None)
    mandate_bootstrap_parser.add_argument("--actor", type=str, default="operator:human")
    mandate_bootstrap_parser.add_argument("--reason", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--idempotency-key", type=str, required=True)
    mandate_bootstrap_parser.add_argument("--audit-correlation-id", type=UUID, default=None)
    mandate_bootstrap_parser.add_argument("--confirm", action="store_true")
    mandate_bootstrap_parser.add_argument("--json", action="store_true", dest="json_output")

    mandate_bootstrap_export_parser = subparsers.add_parser(
        "mandate-bootstrap-export",
        parents=[common],
        help="Read-only export of authoritative mandate-bootstrap inputs for one capital campaign (Stages 1-6: campaign, definition, paper account, live trading profile, exchange connection, strategy evidence, owner-input manifest, owner decision worksheet)",
        description=(
            "Stages 1-6 of the read-only mandate-bootstrap export design. Resolves "
            "CapitalCampaign identity, (if pinned) CapitalCampaignDefinition evidence, the "
            "campaign's PaperAccount, LiveTradingProfile candidates strictly scoped to "
            "LiveTradingProfile.paper_account_id == CapitalCampaign.paper_account_id, "
            "ExchangeConnection candidates resolved from the campaign's own exchange label "
            "(provider+environment together, never provider alone) gated on the live "
            "trading profile having already resolved uniquely, non-authoritative strategy "
            "evidence, the complete remaining mandate-bootstrap owner-input manifest "
            "(risk limits, execution scope, policies, authorization evidence, operational "
            "inputs), and a deterministic owner_decision_worksheet, for one "
            "--capital-campaign-id. Performs no writes -- no lifecycle action, no "
            "authorization, no mandate creation. Every field is classified as "
            "DATABASE_DERIVED, CONFIGURATION_DERIVED, OWNER_INPUT_REQUIRED, RUNTIME_DERIVED, "
            "NOT_REQUIRED, MISSING, or CONFLICTING; this stage adds owner_actor_id, "
            "autonomy_level, every risk-limit field, allowed_products, allowed_order_sides, "
            "approval_policy, the six policy fields, owner_acknowledgements, "
            "authorization_evidence_summary, authorization_evidence, "
            "deterministic_explanation, authorization_method, actor, reason, "
            "idempotency_key, confirm, audit_correlation_id, mandate_expires_at, and "
            "authorization_expires_at -- none of it ever filled from campaign-definition "
            "limits, strategy evidence, prior mandates, or any other record. A compact "
            "owner_input_summary (informational only) tallies unresolved OWNER_INPUT_REQUIRED "
            "fields; owner_decision_worksheet gives one entry per such field describing HOW "
            "to supply it (input_type/accepted_values/example_format, sourced only from real "
            "validators and CHECK constraints) -- never WHAT to supply; current_value is "
            "always null. worksheet_summary tallies the worksheet by input_type. Never reuses "
            "another campaign's, another mandate's, or a conversational value; multiple "
            "candidate live trading profiles or exchange connections fail closed as "
            "CONFLICTING rather than picking the newest. Always reports executable=false "
            "and overall_status=BLOCKED."
        ),
    )
    mandate_bootstrap_export_parser.add_argument("--capital-campaign-id", type=int, required=True)
    mandate_bootstrap_export_parser.add_argument("--json", action="store_true", dest="json_output")

    mandate_bootstrap_session_validate_parser = subparsers.add_parser(
        "mandate-bootstrap-session-validate",
        parents=[common],
        help="Read-only validation of an owner-supplied mandate-bootstrap input document against the current campaign's export and the real mandate-bootstrap contract",
        description=(
            "Reuses mandate_bootstrap_export()'s read-only resolution for one "
            "--capital-campaign-id, merges it with the owner-supplied "
            "--owner-input-json document, and validates the result against the actual "
            "mandate_bootstrap() contract (validate_mandate_version(), "
            "validate_autonomy_level(), is_strategy_identity(), and the same numeric "
            "CHECK-constraint bounds AutonomousCapitalMandateVersion enforces). Performs "
            "no writes -- no lifecycle action, no authorization, no mandate creation, and "
            "never calls mandate_bootstrap() itself. The owner-input document must supply "
            "every OWNER_INPUT_REQUIRED field except confirm (which this command always "
            "forces to false) and must never attempt to override a database-derived field "
            "(provider/environment/exchange_connection_id/live_trading_profile_id/"
            "paper_account_id/capital_campaign_id/base_currency/campaign_uuid) -- doing so "
            "fails closed with OWNER_INPUT_ATTEMPTED_DATABASE_OVERRIDE. Never infers a "
            "missing owner decision from strategy evidence, campaign-definition limits, or "
            "any other record. Returns session_status of INVALID or "
            "COMPLETE_FOR_OWNER_REVIEW -- never executable=true. Exit code 0 for "
            "COMPLETE_FOR_OWNER_REVIEW, 1 for INVALID, 2 only for an infrastructure or "
            "unexpected failure."
        ),
    )
    mandate_bootstrap_session_validate_parser.add_argument("--capital-campaign-id", type=int, required=True)
    mandate_bootstrap_session_validate_parser.add_argument("--owner-input-json", type=_parse_owner_input_json, required=True)
    mandate_bootstrap_session_validate_parser.add_argument("--json", action="store_true", dest="json_output")

    mandate_governance_readiness_audit_parser = subparsers.add_parser(
        "mandate-governance-readiness-audit",
        parents=[common],
        help="Read-only inspection of whether the mandate-bootstrap pipeline is safe to allow mandate creation/authorization (Stage 9)",
        description=(
            "Inspects the mandate-bootstrap pipeline (campaign/exchange/paper-account/"
            "live-trading-profile resolution, strategy evidence, the owner worksheet, the "
            "session validator, and the mandate_bootstrap() write path) and reports whether "
            "the repository is safe to allow Stage 9 (mandate creation/authorization). "
            "Performs ONLY inspection -- zero writes, zero rows created, no lifecycle "
            "method called. Every property is checked against the real running code via "
            "inspect.getsource()/inspect.signature()/ast, and via read-only probe calls "
            "into mandate_bootstrap_export()/mandate_bootstrap_session_validate() -- never a "
            "hand-written description of what the code is assumed to do. This command "
            "never itself authorizes, triggers, or blocks Stage 9; overall_status is a "
            "report for a human operator to act on. Exit code 0 for READY_FOR_STAGE9, 1 "
            "for NOT_READY, 2 only for an infrastructure or unexpected failure."
        ),
    )
    mandate_governance_readiness_audit_parser.add_argument("--capital-campaign-id", type=int, required=True)
    mandate_governance_readiness_audit_parser.add_argument("--json", action="store_true", dest="json_output")

    mandate_bootstrap_create_parser = subparsers.add_parser(
        "mandate-bootstrap-create",
        parents=[common],
        help="Governed mandate creation: validates an owner-input document and, only on success, creates the mandate and its initial version -- never authorizes or activates",
        description=(
            "Stage 9A: reuses mandate_bootstrap_export()'s read-only resolution and "
            "mandate_bootstrap_session_validate()'s validation for one "
            "--capital-campaign-id/--owner-input-json pair. If validation fails, performs "
            "zero writes and returns overall_status=FAILED_VALIDATION. If validation "
            "succeeds, reuses the same create_mandate()/create_mandate_version() functions "
            "mandate_bootstrap() itself already calls to create exactly one mandate and its "
            "initial version, then stops -- never calling "
            "apply_mandate_lifecycle_action(), authorize_mandate_version(), or "
            "mandate_bootstrap() itself. The created mandate is left DRAFT/unauthorized/"
            "inactive; a human operator must separately authorize and activate it. Both "
            "writes are idempotent on the owner-supplied idempotency_key -- rerunning with "
            "the same owner-input document resumes onto the same mandate_id/"
            "mandate_version_id rather than creating duplicates. Exit code 0 for CREATED, "
            "1 for FAILED_VALIDATION, 2 only for an infrastructure or unexpected failure."
        ),
    )
    mandate_bootstrap_create_parser.add_argument("--capital-campaign-id", type=int, required=True)
    mandate_bootstrap_create_parser.add_argument("--owner-input-json", type=_parse_owner_input_json, required=True)
    mandate_bootstrap_create_parser.add_argument("--json", action="store_true", dest="json_output")

    mandate_bootstrap_create_status_parser = subparsers.add_parser(
        "mandate-bootstrap-create-status",
        parents=[common],
        help="Read-only inspection of mandate-bootstrap-create's real database state for one campaign + idempotency key -- detects partial/conflicting/incoherent creation",
        description=(
            "Stage 9A.1: read-only inspection of the real, committed database state left "
            "behind by mandate-bootstrap-create for one --capital-campaign-id + "
            "--idempotency-key (the same root idempotency_key originally supplied inside "
            "the owner-input document). Performs zero writes. Distinguishes NOT_STARTED "
            "(nothing created yet) from PARTIAL_RECOVERABLE (mandate created, initial "
            "version still missing -- safely repairable by rerunning mandate-bootstrap-"
            "create with the same owner-input document) from COMPLETE_DRAFT (both exist, "
            "coherent, mandate still unauthorized/inactive) from CONFLICT (idempotency key "
            "reused with materially different input, or the campaign's underlying database "
            "identity has drifted since creation) from INCOHERENT (the audit trail itself "
            "is broken/self-contradictory -- never silently continue past this). Exit code "
            "0 for NOT_STARTED/COMPLETE_DRAFT, 1 for PARTIAL_RECOVERABLE/CONFLICT/"
            "INCOHERENT (all require operator attention), 2 only for an infrastructure or "
            "unexpected failure."
        ),
    )
    mandate_bootstrap_create_status_parser.add_argument("--capital-campaign-id", type=int, required=True)
    mandate_bootstrap_create_status_parser.add_argument("--idempotency-key", type=str, required=True)
    mandate_bootstrap_create_status_parser.add_argument("--json", action="store_true", dest="json_output")

    proving_pause = subparsers.add_parser(
        "canonical-proving-pause",
        parents=[common],
        help="Pause an active canonical proving activation",
        description="Idempotently pauses a canonical proving activation without deleting history.",
    )
    proving_pause.add_argument("--package-id", type=UUID, required=True)
    proving_pause.add_argument("--actor", type=str, default="operator:human")
    proving_pause.add_argument("--reason", type=str, required=True)
    proving_pause.add_argument("--idempotency-key", type=str, required=True)
    proving_pause.add_argument("--json", action="store_true", dest="json_output")

    proving_revoke = subparsers.add_parser(
        "canonical-proving-revoke",
        parents=[common],
        help="Revoke an active canonical proving activation",
        description="Idempotently revokes a canonical proving activation without deleting history.",
    )
    proving_revoke.add_argument("--package-id", type=UUID, required=True)
    proving_revoke.add_argument("--actor", type=str, default="operator:human")
    proving_revoke.add_argument("--reason", type=str, required=True)
    proving_revoke.add_argument("--idempotency-key", type=str, required=True)
    proving_revoke.add_argument("--json", action="store_true", dest="json_output")

    first_profit = subparsers.add_parser(
        "first-autonomous-profit-status",
        parents=[common],
        help="Read-only progress status for first autonomous net profit milestone",
        description="Reads authoritative evidence and reports deterministic milestone progress without writing any state.",
    )
    first_profit.add_argument("--campaign-id", type=UUID, required=True)
    first_profit.add_argument("--campaign-version", type=int, required=True)
    first_profit.add_argument("--runtime-campaign-id", type=int, required=True)
    first_profit.add_argument("--paper-account-id", type=UUID, required=True)
    first_profit.add_argument("--live-trading-profile-id", type=UUID, required=True)
    first_profit.add_argument("--provider", type=str, required=True)
    first_profit.add_argument("--environment", type=str, required=True)
    first_profit.add_argument("--product", type=str, required=True)
    first_profit.add_argument("--json", action="store_true", dest="json_output")

    unattended_eligibility = subparsers.add_parser(
        "campaign-unattended-eligibility-audit",
        parents=[common],
        help="Read-only audit for unattended campaign orchestration eligibility",
        description="Audits exact unattended campaign-selection gates for one campaign/version without writing state.",
    )
    unattended_eligibility.add_argument("--campaign-id", type=UUID, required=True)
    unattended_eligibility.add_argument("--campaign-version", type=int, required=True)
    unattended_eligibility.add_argument("--provider", type=str, required=True)
    unattended_eligibility.add_argument("--environment", type=str, required=True)
    unattended_eligibility.add_argument("--product", type=str, required=True)
    unattended_eligibility.add_argument("--json", action="store_true", dest="json_output")

    historical_buy_replay = subparsers.add_parser(
        "historical-buy-campaign-replay-audit",
        parents=[common],
        help="Read-only replay of a historical BUY decision through current campaign gates",
        description="Deterministic as-of-time read-only replay for one historical BUY decision against current campaign constraints.",
    )
    historical_buy_replay.add_argument("--decision-id", type=UUID, required=True)
    historical_buy_replay.add_argument("--campaign-id", type=UUID, required=True)
    historical_buy_replay.add_argument("--campaign-version", type=int, required=True)
    historical_buy_replay.add_argument("--runtime-campaign-id", type=int, required=True)
    historical_buy_replay.add_argument("--paper-account-id", type=UUID, required=True)
    historical_buy_replay.add_argument("--live-trading-profile-id", type=UUID, required=True)
    historical_buy_replay.add_argument("--provider", type=str, required=True)
    historical_buy_replay.add_argument("--environment", type=str, required=True)
    historical_buy_replay.add_argument("--product", type=str, required=True)
    historical_buy_replay.add_argument("--matching-sell-decision-id", type=UUID, default=None)
    historical_buy_replay.add_argument("--json", action="store_true", dest="json_output")

    buy_opportunity = subparsers.add_parser(
        "buy-opportunity-diagnostic",
        parents=[common],
        help="Read-only 24h diagnostic for canonical proving BUY opportunity outcomes",
        description="Analyzes last 24h canonical proving campaign evaluations, BUY/SELL/HOLD counts, BUY blockers, and READY package summary.",
    )
    buy_opportunity.add_argument("--json", action="store_true", dest="json_output")

    hold_diagnostic = subparsers.add_parser(
        "hold-decision-diagnostic",
        parents=[common],
        help="Read-only 24h diagnostic for canonical proving HOLD decisions",
        description="Analyzes last 24h canonical proving campaign HOLD decisions with persisted condition evidence and missing evidence reporting.",
    )
    hold_diagnostic.add_argument("--json", action="store_true", dest="json_output")

    return parser


_MANDATE_POLICY_BUNDLE_REQUIRED_KEYS = (
    "entry_policy",
    "exit_policy",
    "cooldown_policy",
    "operating_schedule",
    "reconciliation_policy",
    "kill_switch_policy",
    "owner_acknowledgements",
    "authorization_evidence_summary",
    "authorization_evidence",
    "deterministic_explanation",
)


def _parse_csv_argument(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("expected at least one comma-separated value")
    return items


def _read_json_argument(value: str, *, flag_name: str) -> Any:
    """Reads --{flag_name}: either a raw JSON string or an @-prefixed path to a JSON file
    (mirroring curl's `-d @file.json` convention). Shared by every CLI argument that
    accepts a JSON document this way (--policy-bundle-json, --owner-input-json)."""
    raw = value
    if value.startswith("@"):
        raw = Path(value[1:]).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--{flag_name}: invalid JSON: {exc}") from exc


def _parse_mandate_policy_bundle(value: str) -> dict[str, dict[str, Any]]:
    """Parses --policy-bundle-json, containing every nested policy/evidence dict
    mandate-bootstrap needs. These are inherently unstructured JSON objects, not flat
    scalars, so one JSON blob is far less error-prone here than a dozen more --flag
    values -- especially for owner_acknowledgements/authorization_evidence/
    deterministic_explanation, which are the actual audit evidence for a live-money
    authorization decision and must be supplied deliberately, never defaulted."""
    parsed = _read_json_argument(value, flag_name="policy-bundle-json")
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--policy-bundle-json: expected a JSON object")
    missing = [key for key in _MANDATE_POLICY_BUNDLE_REQUIRED_KEYS if key not in parsed]
    if missing:
        raise argparse.ArgumentTypeError(f"--policy-bundle-json: missing required keys: {', '.join(missing)}")
    non_dict_keys = [key for key in _MANDATE_POLICY_BUNDLE_REQUIRED_KEYS if not isinstance(parsed[key], dict)]
    if non_dict_keys:
        raise argparse.ArgumentTypeError(f"--policy-bundle-json: expected JSON objects for keys: {', '.join(non_dict_keys)}")
    return parsed


def _parse_owner_input_json(value: str) -> dict[str, Any]:
    """Parses --owner-input-json for mandate-bootstrap-session-validate: either a raw
    JSON object string or an @-prefixed path to a JSON file, containing the
    owner-controlled mandate-bootstrap fields. Only checks it's a JSON object here --
    which keys are required/forbidden depends on this specific campaign's own export
    resolution, and is validated inside mandate_bootstrap_session_validate(), not at
    CLI parse time."""
    parsed = _read_json_argument(value, flag_name="owner-input-json")
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--owner-input-json: expected a JSON object")
    return parsed


def _resolve_preview_idempotency_seed(args: argparse.Namespace) -> str | None:
    if getattr(args, "reuse_idempotency_key", False):
        return None
    explicit_seed = getattr(args, "idempotency_seed", None)
    if explicit_seed:
        return explicit_seed
    return uuid4().hex


def _parse_iso_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _run_async(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    options = resolve_render_options(no_color=bool(args.no_color), verbose=bool(args.verbose))

    if args.command == "preview":
        payload = await execute_preview_cycle(
            mandate_id=args.mandate_id,
            actor=args.actor,
            product_id=args.product_id,
            strategy_interval=args.strategy_interval,
            trigger=args.trigger,
            idempotency_seed=_resolve_preview_idempotency_seed(args),
            software_build_version=args.software_build_version,
            forced_action=args.forced_action,
        )
        text = render_json(payload) if args.json_output else render_preview_text(payload, options)
        state = str(payload.get("state") or "")
        code = 0 if state in {"COMPLETE", "PREVIEW_READY"} else 1
        return code, payload, text

    if args.command == "campaign-orchestration-readiness":
        payload = await fetch_campaign_orchestration_readiness(campaign_id=args.campaign_id, version=args.version)
        return 0, payload, render_json(payload)

    if args.command == "campaign-orchestration-preview":
        payload = await fetch_campaign_orchestration_preview(campaign_id=args.campaign_id, version=args.version)
        return 0, payload, render_json(payload)

    if args.command == "campaign-orchestration-status":
        payload = await fetch_campaign_orchestration_status(campaign_id=args.campaign_id, version=args.version)
        return 0, payload, render_json(payload)

    if args.command == "campaign-orchestration-history":
        payload = await fetch_campaign_orchestration_history(campaign_id=args.campaign_id, version=args.version, limit=args.limit)
        return 0, payload, render_json(payload)

    if args.command == "commissioned-control-plane-status":
        payload = await fetch_commissioned_control_plane_status(campaign_id=args.campaign_id, version=args.version)
        return 0, payload, render_json(payload)

    if args.command == "commissioned-control-plane-action":
        payload = await mutate_commissioned_control_plane_action(
            campaign_id=args.campaign_id,
            version=args.version,
            actor=args.actor,
            action=args.action,
            idempotency_key=args.idempotency_key,
            reason=args.reason,
        )
        return (0 if bool(payload.get("accepted", False)) else 1), payload, render_json(payload)

    if args.command == "canonical-preview-package-create":
        payload = await create_canonical_preview_package_bundle(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            max_proposed_order_amount=args.max_proposed_order_amount,
            commissioning_entry_mode=args.commissioning_entry_mode,
            actor=args.actor,
            idempotency_key=args.idempotency_key,
        )
        return (0 if bool((payload.get("readiness") or {}).get("ready")) else 1), payload, render_json(payload)

    if args.command == "canonical-preview-package-show":
        payload = await show_canonical_preview_package_bundle(package_id=args.package_id)
        return 0, payload, render_json(payload)

    if args.command == "canonical-preview-package-readiness":
        payload = await canonical_preview_package_readiness(package_id=args.package_id)
        return (0 if bool((payload.get("readiness") or {}).get("ready")) else 1), payload, render_json(payload)

    if args.command == "canonical-preview-package-history":
        payload = await canonical_preview_package_history(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            limit=args.limit,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-preview-package-authorize":
        payload = await authorize_canonical_preview_package_bundle(
            package_id=args.package_id,
            actor=args.actor,
            approver_role=args.approver_role,
            rationale=args.rationale,
            expires_at=_parse_iso_datetime(args.expires_at),
            max_order_usd=args.max_order_usd,
            max_total_deployed_campaign_capital_usd=args.max_total_deployed_campaign_capital_usd,
            no_leverage=bool(args.no_leverage),
            idempotency_key=args.idempotency_key,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-preview-package-dry-run":
        payload = await dry_run_canonical_preview_package_bundle(
            package_id=args.package_id,
            approval_event_id=args.approval_event_id,
            operator_identity=args.operator_identity,
            idempotency_token=args.idempotency_token,
        )
        status = str(payload.get("dry_run_status") or "")
        return (0 if status == "DRY_RUN_READY" else 1), payload, render_json(payload)

    if args.command == "canonical-proving-activate":
        payload = await activate_canonical_proving_campaign_bundle(
            package_id=args.package_id,
            approval_event_id=args.approval_event_id,
            dry_run_live_crypto_order_id=args.dry_run_live_crypto_order_id,
            actor=args.actor,
            expires_at=_parse_iso_datetime(args.expires_at),
            idempotency_key=args.idempotency_key,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-activation-status":
        payload = await canonical_proving_activation_status(package_id=args.package_id)
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-commission":
        payload = await canonical_proving_commission_bundle(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product=args.product,
            amount_usd=args.amount_usd,
            actor=args.actor,
            approver_role=args.approver_role,
            rationale=args.rationale,
            no_leverage=bool(args.no_leverage),
            confirm=bool(args.confirm),
            idempotency_key=args.idempotency_key,
        )
        current_state = str(payload.get("current_state") or "")
        return (0 if current_state == "ACTIVE_POSITION" else 1), payload, render_json(payload)

    if args.command == "canonical-proving-commission-status":
        payload = await canonical_proving_commission_status(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product=args.product,
        )
        text = render_json(payload) if args.json_output else render_canonical_proving_commission_status_text(payload, options)
        return 0, payload, text

    if args.command == "mandate-identity-diagnosis":
        payload = await mandate_identity_diagnosis(
            campaign_id=args.campaign_id,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
        )
        return 0, payload, render_json(payload)

    if args.command == "mandate-bootstrap":
        policy_bundle = args.policy_bundle_json
        payload = await mandate_bootstrap(
            owner_actor_id=args.owner_actor_id,
            autonomy_level=args.autonomy_level,
            provider=args.provider,
            environment=args.environment,
            exchange_connection_id=args.exchange_connection_id,
            live_trading_profile_id=args.live_trading_profile_id,
            paper_account_id=args.paper_account_id,
            capital_campaign_id=args.capital_campaign_id,
            mandate_expires_at=_parse_iso_datetime(args.mandate_expires_at) if args.mandate_expires_at else None,
            base_currency=args.base_currency,
            authorized_capital_usd=args.authorized_capital_usd,
            max_order_notional_usd=args.max_order_notional_usd,
            max_open_exposure_usd=args.max_open_exposure_usd,
            max_daily_deployed_usd=args.max_daily_deployed_usd,
            max_daily_realized_loss_usd=args.max_daily_realized_loss_usd,
            max_campaign_drawdown_usd=args.max_campaign_drawdown_usd,
            max_consecutive_losses=args.max_consecutive_losses,
            position_limit=args.position_limit,
            price_evidence_max_age_seconds=args.price_evidence_max_age_seconds,
            max_slippage_bps=args.max_slippage_bps,
            max_fee_bps=args.max_fee_bps,
            allowed_products=args.allowed_products,
            allowed_order_sides=args.allowed_order_sides,
            allowed_strategy_versions=args.allowed_strategy_versions,
            approval_policy=args.approval_policy,
            entry_policy=policy_bundle["entry_policy"],
            exit_policy=policy_bundle["exit_policy"],
            cooldown_policy=policy_bundle["cooldown_policy"],
            operating_schedule=policy_bundle["operating_schedule"],
            reconciliation_policy=policy_bundle["reconciliation_policy"],
            kill_switch_policy=policy_bundle["kill_switch_policy"],
            owner_acknowledgements=policy_bundle["owner_acknowledgements"],
            authorization_evidence_summary=policy_bundle["authorization_evidence_summary"],
            authorization_method=args.authorization_method,
            authorization_evidence=policy_bundle["authorization_evidence"],
            deterministic_explanation=policy_bundle["deterministic_explanation"],
            authorization_expires_at=_parse_iso_datetime(args.authorization_expires_at) if args.authorization_expires_at else None,
            actor=args.actor,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
            audit_correlation_id=args.audit_correlation_id,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "mandate-bootstrap-export":
        payload = await mandate_bootstrap_export(capital_campaign_id=args.capital_campaign_id)
        return 0, payload, render_json(payload)

    if args.command == "mandate-bootstrap-session-validate":
        payload = await mandate_bootstrap_session_validate(
            capital_campaign_id=args.capital_campaign_id,
            owner_input=args.owner_input_json,
        )
        exit_code = 0 if payload["session_status"] == "COMPLETE_FOR_OWNER_REVIEW" else 1
        return exit_code, payload, render_json(payload)

    if args.command == "mandate-governance-readiness-audit":
        payload = await mandate_governance_readiness_audit(capital_campaign_id=args.capital_campaign_id)
        exit_code = 0 if payload["overall_status"] == "READY_FOR_STAGE9" else 1
        return exit_code, payload, render_json(payload)

    if args.command == "mandate-bootstrap-create":
        payload = await mandate_bootstrap_create(
            capital_campaign_id=args.capital_campaign_id,
            owner_input=args.owner_input_json,
        )
        exit_code = 0 if payload["overall_status"] == "CREATED" else 1
        return exit_code, payload, render_json(payload)

    if args.command == "mandate-bootstrap-create-status":
        payload = await mandate_bootstrap_create_status(
            capital_campaign_id=args.capital_campaign_id,
            idempotency_key=args.idempotency_key,
        )
        exit_code = 0 if payload["overall_status"] in {"NOT_STARTED", "COMPLETE_DRAFT"} else 1
        return exit_code, payload, render_json(payload)

    if args.command == "canonical-proving-pause":
        payload = await pause_canonical_proving_activation_bundle(
            package_id=args.package_id,
            actor=args.actor,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-revoke":
        payload = await revoke_canonical_proving_activation_bundle(
            package_id=args.package_id,
            actor=args.actor,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
        return 0, payload, render_json(payload)

    if args.command == "preview-show":
        payload = await fetch_preview_evidence(preview_id=args.preview_id)
        text = render_json(payload) if args.json_output else render_preview_show_text(payload, options)
        return 0, payload, text

    if args.command == "candles":
        payload = await fetch_candle_readiness(
            symbol=args.symbol,
            interval=args.interval,
            exchange=args.exchange,
            max_age_minutes=args.max_age_minutes,
            lookback_limit=args.lookback_limit,
        )
        text = render_json(payload) if args.json_output else render_candles_text(payload, options)
        return (0 if payload.get("ready") else 1), payload, text

    if args.command == "watch":
        payload = await fetch_watch_status(
            mandate_id=args.mandate_id,
            candle_symbol=args.symbol,
            candle_interval=args.interval,
            candle_exchange=args.exchange,
            candle_max_age_minutes=args.max_age_minutes,
        )
        payload = {
            **payload,
            "watch_refreshed_at": datetime.now(timezone.utc),
        }
        text = render_json(payload) if args.json_output else render_watch_text(payload, options)
        return 0, payload, text

    if args.command == "roster":
        payload = await fetch_strategy_roster_summary(
            provider=args.provider,
            product_id=args.product_id,
            interval=args.interval,
        )
        text = render_json(payload) if args.json_output else render_roster_text(payload, options)
        return 0, payload, text

    if args.command == "scorecards":
        payload = await fetch_strategy_scorecards_summary(
            provider=args.provider,
            product_id=args.product_id,
            interval=args.interval,
        )
        text = render_json(payload) if args.json_output else render_scorecards_text(payload, options)
        return 0, payload, text

    if args.command == "execution-forensics":
        payload = await fetch_execution_forensics(
            since=args.since,
            cycle_id=args.cycle,
            latest=bool(args.latest),
        )
        text = render_json(payload) if args.json_output else render_execution_forensics_text(payload, options)
        return 0, payload, text

    if args.command == "venue-commission-readiness":
        payload = await fetch_venue_commission_readiness(
            provider=args.provider,
            product_id=args.product,
            environment=args.environment,
            amount_usd=args.amount_usd,
            hold_minutes=args.hold_minutes,
        )
        text = render_json(payload) if args.json_output else render_venue_commission_text(payload, options)
        return (0 if payload.get("would_activate_safely") else 1), payload, text

    if args.command == "venue-commission-activate":
        payload = await activate_venue_commission_run(
            actor=args.actor,
            provider=args.provider,
            product_id=args.product,
            environment=args.environment,
            amount_usd=args.amount_usd,
            hold_minutes=args.hold_minutes,
            confirm=bool(args.confirm),
        )
        text = render_json(payload) if args.json_output else render_venue_commission_text(payload, options)
        return 0, payload, text

    if args.command == "venue-commission-start":
        payload = await start_venue_commission_run(
            actor=args.actor,
            commissioning_run_id=args.commissioning_run_id,
            confirm=bool(args.confirm),
        )
        text = render_json(payload) if args.json_output else render_venue_commission_text(payload, options)
        return 0, payload, text

    if args.command == "venue-commission-status":
        payload = await fetch_venue_commission_status(commissioning_run_id=args.commissioning_run_id)
        text = render_json(payload) if args.json_output else render_venue_commission_text(payload, options)
        return 0, payload, text

    if args.command == "venue-commission-revoke":
        payload = await revoke_venue_commission_run(
            actor=args.actor,
            commissioning_run_id=args.commissioning_run_id,
            confirm=bool(args.confirm),
        )
        text = render_json(payload) if args.json_output else render_venue_commission_text(payload, options)
        return 0, payload, text

    if args.command == "canonical-campaign-readiness":
        payload = await fetch_canonical_campaign_binding_status(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-campaign-bind":
        payload = await bind_canonical_campaign_runtime(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-campaign-status-transition-readiness":
        payload = await canonical_campaign_status_transition_readiness(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            expected_current_status=args.expected_current_status,
            target_status=args.target_status,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
        )
        return (0 if bool(payload.get("ready")) else 1), payload, render_json(payload)

    if args.command == "canonical-campaign-status-transition-execute":
        payload = await canonical_campaign_status_transition_execute(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            expected_current_status=args.expected_current_status,
            target_status=args.target_status,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            idempotency_key=args.idempotency_key,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-campaign-status-transition-audit":
        payload = await canonical_campaign_status_transition_audit(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            expected_current_status=args.expected_current_status,
            target_status=args.target_status,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            limit=args.limit,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-campaign-binding-audit":
        payload = await fetch_canonical_campaign_binding_audit(campaign_id=args.campaign_id, limit=args.limit)
        return 0, payload, render_json(payload)

    if args.command == "canonical-campaign-authority-audit":
        payload = await canonical_campaign_authority_audit(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            cycle_id=args.cycle_id,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-paper-cash-causality-audit":
        payload = await canonical_paper_cash_causality_audit(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-account-transition-preview":
        payload = await canonical_proving_account_transition_preview(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            old_paper_account_id=args.old_paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-account-transition-execute":
        payload = await canonical_proving_account_transition_execute(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            old_paper_account_id=args.old_paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
            idempotency_key=args.idempotency_key,
            expected_evidence_source_id=args.expected_evidence_source_id,
            expected_evidence_observed_at=args.expected_evidence_observed_at,
        )
        return 0, payload, render_json(payload)

    if args.command == "exchange-connection-refresh-balances":
        payload = await refresh_provider_balance_evidence(
            provider=args.provider,
            environment=args.environment,
            actor=args.actor,
        )
        return 0, payload, render_json(payload)

    if args.command == "canonical-proving-cap-transition-preview":
        payload = await canonical_proving_cap_transition_preview(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
        )
        return (0 if bool(payload.get("ready")) else 1), payload, render_json(payload)

    if args.command == "canonical-proving-cap-transition-execute":
        payload = await canonical_proving_cap_transition_execute(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            actor=args.actor,
            confirm=bool(args.confirm),
            idempotency_key=args.idempotency_key,
        )
        return 0, payload, render_json(payload)

    if args.command == "first-autonomous-profit-status":
        payload = await first_autonomous_profit_status(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
        )
        return 0, payload, render_json(payload)

    if args.command == "campaign-unattended-eligibility-audit":
        payload = await campaign_unattended_eligibility_audit(
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
        )
        return 0, payload, render_json(payload)

    if args.command == "historical-buy-campaign-replay-audit":
        payload = await historical_buy_campaign_replay_audit(
            decision_id=args.decision_id,
            campaign_id=args.campaign_id,
            campaign_version=args.campaign_version,
            runtime_campaign_id=args.runtime_campaign_id,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            matching_sell_decision_id=args.matching_sell_decision_id,
        )
        return 0, payload, render_json(payload)

    if args.command == "buy-opportunity-diagnostic":
        payload = await buy_opportunity_diagnostic()
        text = render_json(payload) if args.json_output else render_buy_opportunity_diagnostic_text(payload, options)
        return 0, payload, text

    if args.command == "hold-decision-diagnostic":
        payload = await hold_decision_diagnostic()
        text = render_json(payload) if args.json_output else render_hold_decision_diagnostic_text(payload, options)
        return 0, payload, text

    if args.command == "legacy-campaign-transition-readiness":
        payload = await inspect_legacy_campaign_transition(
            legacy_campaign_id=args.legacy_campaign_id,
            canonical_campaign_id=args.canonical_campaign_id,
            canonical_campaign_version=args.canonical_campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "legacy-campaign-transition-execute":
        payload = await transition_legacy_campaign_to_canonical_successor(
            legacy_campaign_id=args.legacy_campaign_id,
            canonical_campaign_id=args.canonical_campaign_id,
            canonical_campaign_version=args.canonical_campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "legacy-campaign-transition-audit":
        payload = await fetch_legacy_campaign_transition_audit(legacy_campaign_id=args.legacy_campaign_id, limit=args.limit)
        return 0, payload, render_json(payload)

    if args.command == "legacy-campaign-transition-rollback":
        payload = await rollback_legacy_campaign_transition(
            legacy_campaign_id=args.legacy_campaign_id,
            canonical_campaign_id=args.canonical_campaign_id,
            canonical_campaign_version=args.canonical_campaign_version,
            paper_account_id=args.paper_account_id,
            live_trading_profile_id=args.live_trading_profile_id,
            provider=args.provider,
            environment=args.environment,
            product_id=args.product,
            actor=args.actor,
            confirm=bool(args.confirm),
        )
        return 0, payload, render_json(payload)

    if args.command == "risk-ledger-diagnosis":
        payload = await fetch_risk_ledger_diagnosis(account_id=args.account_id)
        return 0, payload, render_json(payload)

    payload = await fetch_operator_status(
        mandate_id=args.mandate_id,
        candle_symbol=args.symbol,
        candle_interval=args.interval,
        candle_exchange=args.exchange,
        candle_max_age_minutes=args.max_age_minutes,
    )
    text = render_json(payload) if args.json_output else render_status_text(payload, options)
    return 0, payload, text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "watch":
        try:
            while True:
                code, _payload, text = asyncio.run(_run_async(args))
                print("\033[2J\033[H", end="")
                print(text)
                print(f"\nRefreshed at {datetime.now(timezone.utc).isoformat()} | Ctrl+C to exit")
                asyncio.run(asyncio.sleep(max(1, int(args.refresh_seconds))))
        except KeyboardInterrupt:
            print("\nWatch mode stopped by operator.")
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    try:
        code, _payload, text = asyncio.run(_run_async(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
