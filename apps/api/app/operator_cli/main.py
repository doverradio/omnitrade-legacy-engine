from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.operator_cli.formatting import (
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
    bind_canonical_campaign_runtime,
    activate_venue_commission_run,
    fetch_canonical_campaign_binding_audit,
    fetch_canonical_campaign_binding_status,
    fetch_legacy_campaign_transition_audit,
    fetch_campaign_orchestration_history,
    fetch_campaign_orchestration_preview,
    fetch_campaign_orchestration_readiness,
    fetch_campaign_orchestration_status,
    execute_preview_cycle,
    fetch_venue_commission_readiness,
    fetch_venue_commission_status,
    fetch_candle_readiness,
    fetch_operator_status,
    fetch_preview_evidence,
    fetch_execution_forensics,
    fetch_strategy_scorecards_summary,
    fetch_strategy_roster_summary,
    fetch_risk_ledger_diagnosis,
    fetch_watch_status,
    inspect_legacy_campaign_transition,
    rollback_legacy_campaign_transition,
    revoke_venue_commission_run,
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
            "  ./operator legacy-campaign-transition-readiness --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --json\n"
            "  ./operator legacy-campaign-transition-execute --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --confirm --json\n"
            "  ./operator legacy-campaign-transition-audit --legacy-campaign-id <legacy_uuid> --json\n"
            "  ./operator legacy-campaign-transition-rollback --legacy-campaign-id <legacy_uuid> --canonical-campaign-id <canonical_uuid> --canonical-campaign-version 1 --paper-account-id <paper_uuid> --live-trading-profile-id <profile_uuid> --provider kraken_spot --environment production --product BTC-USD --actor operator:human --confirm --json\n"
            "  ./operator risk-ledger-diagnosis --account-id <paper_uuid> --json\n"
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

    canonical_audit = subparsers.add_parser(
        "canonical-campaign-binding-audit",
        parents=[common],
        help="Show immutable audit evidence for canonical campaign binding",
        description="Read-only binding audit evidence for one canonical campaign.",
    )
    canonical_audit.add_argument("--campaign-id", type=UUID, required=True)
    canonical_audit.add_argument("--limit", type=int, default=20)
    canonical_audit.add_argument("--json", action="store_true", dest="json_output")

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

    return parser


def _resolve_preview_idempotency_seed(args: argparse.Namespace) -> str | None:
    if getattr(args, "reuse_idempotency_key", False):
        return None
    explicit_seed = getattr(args, "idempotency_seed", None)
    if explicit_seed:
        return explicit_seed
    return uuid4().hex


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

    if args.command == "canonical-campaign-binding-audit":
        payload = await fetch_canonical_campaign_binding_audit(campaign_id=args.campaign_id, limit=args.limit)
        return 0, payload, render_json(payload)

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
