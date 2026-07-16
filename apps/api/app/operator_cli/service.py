from __future__ import annotations

import re
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
import uuid

from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.venue_commissioning_run import VenueCommissioningRun
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.models.validation_run_event import ValidationRunEvent
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.autonomous_cycle.orchestrator import normalize_product_id
from app.services.canonical_campaign_binding import (
    CanonicalProvingAccountTransitionRequest,
    CanonicalCampaignBindingRequest,
    LegacyCampaignTransitionRequest,
    bind_canonical_campaign_runtime as _bind_canonical_campaign_runtime,
    inspect_canonical_proving_account_transition as _inspect_canonical_proving_account_transition,
    transition_canonical_proving_account as _transition_canonical_proving_account,
    fetch_legacy_campaign_transition_audit as _fetch_legacy_campaign_transition_audit,
    fetch_canonical_campaign_binding_audit as _fetch_canonical_campaign_binding_audit,
    inspect_canonical_campaign_binding as _inspect_canonical_campaign_binding,
    inspect_legacy_campaign_transition as _inspect_legacy_campaign_transition,
    rollback_legacy_campaign_transition as _rollback_legacy_campaign_transition,
    transition_legacy_campaign_to_canonical_successor as _transition_legacy_campaign_to_canonical_successor,
)
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageActivationRequest,
    CanonicalPreviewPackageAuthorizeRequest,
    CanonicalPreviewPackageCreateRequest,
    CanonicalPreviewPackageDryRunRequest,
    CanonicalPreviewPackagePauseRequest,
    CanonicalPreviewPackageRevokeRequest,
    activate_canonical_proving_campaign,
    authorize_canonical_preview_package,
    create_canonical_preview_package,
    get_canonical_preview_package,
    get_canonical_proving_activation_status,
    list_canonical_preview_package_history,
    pause_canonical_proving_activation,
    revoke_canonical_proving_activation,
    run_dry_run_for_canonical_preview_package,
)
from app.services.canonical_paper_cash_causality_audit import (
    CanonicalPaperCashCausalityAuditRequest,
    run_canonical_paper_cash_causality_audit,
)
from app.services.canonical_campaign_authority_audit import (
    CanonicalCampaignAuthorityAuditRequest,
    run_canonical_campaign_authority_audit,
)
from app.services.capital_campaign_orchestration import (
    fetch_campaign_orchestration_history as _fetch_campaign_orchestration_history,
    fetch_campaign_orchestration_readiness as _fetch_campaign_orchestration_readiness,
    fetch_campaign_orchestration_status as _fetch_campaign_orchestration_status,
    run_campaign_orchestration_preview_for_candle,
)
from app.services.exchange_connections import refresh_exchange_balances as _refresh_exchange_balances
from app.services.paper.accounting import build_account_snapshot
from app.services.risk import risk_monitor
from app.services.risk.equity_evidence import resolve_equity_risk_evidence
from app.services.risk.risk_context import resolve_effective_risk_policy
from app.services.strategy_outcomes import fetch_strategy_scorecards


_EXECUTION_FORENSICS_MAX_SINCE_CYCLES = 200
_PROVING_CAP_TARGET_USD = Decimal("5")
_TERMINAL_PACKAGE_STATES = {"COMPLETED", "FAILED_CLOSED", "EXPIRED", "INVALIDATED", "SUPERSEDED"}
_TERMINAL_ACTIVATION_STATES = {"REVOKED", "EXPIRED", "INVALIDATED", "COMPLETED"}
_TERMINAL_LIVE_ORDER_STATES = {"DRY_RUN_READY", "DRY_RUN_BLOCKED", "FILLED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED", "COMPLETED"}
_UNRESOLVED_RECONCILIATION_STATES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}
_KRKN_BTC_INTERVAL = "15m"
_INTERVAL_INGESTION_GRACE_MINUTES = {"15m": 5}
_FIRST_PROFIT_STAGE_ANCHORS: dict[int, float] = {
    1: 75.0,
    2: 99.6,
    3: 99.7,
    4: 99.75,
    5: 99.8,
    6: 99.85,
    7: 99.9,
    8: 99.93,
    9: 99.97,
    10: 100.0,
}


def _product_symbol(value: str) -> str:
    normalized = normalize_product_id(value)
    return normalized.split("-", 1)[0] if "-" in normalized else normalized


def _interval_minutes(interval: str | None) -> int | None:
    raw = str(interval or "").strip().lower()
    if not raw:
        return None
    if raw.endswith("m"):
        value = raw[:-1]
        return int(value) if value.isdigit() and int(value) > 0 else None
    if raw.endswith("h"):
        value = raw[:-1]
        return int(value) * 60 if value.isdigit() and int(value) > 0 else None
    if raw.endswith("d"):
        value = raw[:-1]
        return int(value) * 1440 if value.isdigit() and int(value) > 0 else None
    return None


def _strategy_identity_is_coherent(*, strategy_identity: str | None, strategy_version: str | None) -> bool:
    identity = str(strategy_identity or "").strip()
    version = str(strategy_version or "").strip()
    if not identity:
        return False
    if "@" not in identity:
        return identity == version or not version
    slug, identity_version = identity.split("@", 1)
    slug = slug.strip()
    identity_version = identity_version.strip()
    if not slug or not identity_version:
        return False
    if not version:
        return False
    if "@" in version:
        v_slug, v_version = version.split("@", 1)
        return v_slug.strip() == slug and v_version.strip() == identity_version
    return version == identity_version


def _latest_cycle_outcome(cycle: AutonomousCycleRun | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if cycle is None:
        return None, None, None, None, None
    context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    composition = context.get("authoritative_composition") if isinstance(context.get("authoritative_composition"), dict) else {}
    selected = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}
    strategy_identity = str(selected.get("strategy_identity") or "").strip() or None
    strategy_version = str(selected.get("strategy_version") or "").strip() or None
    return (
        str(cycle.proposed_action or "").strip() or None,
        str(cycle.failure_reason or selected.get("reason") or "").strip() or None,
        str(selected.get("decision_record_id") or "").strip() or None,
        strategy_identity,
        strategy_version,
    )


def _derive_first_autonomous_profit_status(evidence: dict[str, Any]) -> dict[str, Any]:
    now = evidence["now"]
    runtime = evidence.get("runtime")
    definition = evidence.get("definition")
    paper_account = evidence.get("paper_account")
    profile = evidence.get("profile")
    connection = evidence.get("connection")
    latest_candle = evidence.get("latest_candle")
    latest_cycle = evidence.get("latest_cycle")
    ready_package = evidence.get("ready_package")
    approval_event = evidence.get("approval_event")
    activation = evidence.get("activation")
    buy_submitted = bool(evidence.get("buy_submitted"))
    buy_fill_reconciled = bool(evidence.get("buy_fill_reconciled"))
    sell_submitted = bool(evidence.get("sell_submitted"))
    sell_fill_reconciled = bool(evidence.get("sell_fill_reconciled"))
    position_open = bool(evidence.get("position_open"))
    unresolved_reconciliation_count = int(evidence.get("unresolved_reconciliation_count") or 0)
    open_live_order_count = int(evidence.get("open_live_order_count") or 0)
    latest_reconciliation_unknown_count = int(evidence.get("unknown_reconciliation_count") or 0)
    provider_equity = evidence.get("provider_equity")
    paper_liquid_cash = evidence.get("paper_liquid_cash")
    provider_readiness_verdict = str(evidence.get("provider_readiness_verdict") or "").strip()
    provider_balance_synced_at = evidence.get("provider_balance_synced_at")
    latest_ingestion_candle_at = evidence.get("latest_ingestion_candle_at")
    realized_gross_profit = evidence.get("realized_gross_profit")
    fees = evidence.get("fees")
    realized_net_profit = evidence.get("realized_net_profit")
    autonomous_buy_provenance = bool(evidence.get("autonomous_buy_provenance"))
    autonomous_sell_provenance = bool(evidence.get("autonomous_sell_provenance"))
    starting_reconciled_usd = evidence.get("starting_reconciled_usd")
    ending_reconciled_usd = evidence.get("ending_reconciled_usd")

    latest_cycle_outcome, latest_cycle_reason, latest_cycle_decision_record_id, latest_strategy_identity, latest_strategy_version = _latest_cycle_outcome(latest_cycle)
    latest_strategy_coherent = _strategy_identity_is_coherent(
        strategy_identity=latest_strategy_identity,
        strategy_version=latest_strategy_version,
    ) if latest_strategy_identity is not None and latest_strategy_version is not None else False

    freshness_seconds = None
    freshness_minutes = None
    candle_interval_minutes = None
    ingestion_grace_minutes = None
    maximum_age_minutes = None
    freshness_verdict = "unavailable"
    if latest_candle is not None and getattr(latest_candle, "close_time", None) is not None:
        close_time = latest_candle.close_time.astimezone(timezone.utc)
        freshness_seconds = int((now - close_time).total_seconds())
        freshness_minutes = freshness_seconds // 60
        candle_interval_minutes = _interval_minutes(getattr(latest_candle, "interval", None))
        ingestion_grace_minutes = _INTERVAL_INGESTION_GRACE_MINUTES.get(str(getattr(latest_candle, "interval", "")).strip().lower(), 0)
        if candle_interval_minutes is None:
            freshness_verdict = "fail_closed_interval_unparseable"
        elif freshness_seconds < 0:
            freshness_verdict = "fail_closed_future_timestamp"
        else:
            maximum_age_minutes = candle_interval_minutes + ingestion_grace_minutes
            freshness_verdict = "fresh" if freshness_seconds <= (maximum_age_minutes * 60) else "stale"

    provider_connected = connection is not None and str(getattr(connection, "status", "")) == "connected"
    provider_ready = provider_readiness_verdict in {"READY_FOR_OPERATOR_REVIEW", "READY", "connected"}
    provider_balance_fresh = provider_balance_synced_at is not None and isinstance(provider_balance_synced_at, datetime) and int((now - provider_balance_synced_at.astimezone(timezone.utc)).total_seconds()) <= 1800
    provider_reconciliation_clean = unresolved_reconciliation_count == 0 and latest_reconciliation_unknown_count == 0
    runtime_campaign_matches = runtime is not None and str(getattr(runtime, "uuid", "")) == str(evidence["campaign_id"]) and int(getattr(runtime, "definition_version", -1)) == int(evidence["campaign_version"])
    runtime_uses_dedicated_account = runtime is not None and str(getattr(runtime, "paper_account_id", "")) == str(evidence["paper_account_id"])
    profile_uses_dedicated_account = profile is not None and str(getattr(profile, "paper_account_id", "")) == str(evidence["paper_account_id"])
    dedicated_account_active = paper_account is not None and bool(getattr(paper_account, "is_active", False))
    paper_cash_reconciled = unresolved_reconciliation_count == 0
    paper_liquid_cash_supports_exact_5 = paper_liquid_cash is not None and Decimal(str(paper_liquid_cash)) >= Decimal("5")
    max_open_positions_one = definition is not None and int(getattr(definition, "maximum_open_positions", -1)) == 1
    minimum_position_size_five = definition is not None and Decimal(str(getattr(definition, "minimum_position_size", "-1"))) == Decimal("5")
    maximum_position_size_five = definition is not None and Decimal(str(getattr(definition, "maximum_position_size", "-1"))) == Decimal("5")
    maximum_total_exposure_five = definition is not None and Decimal(str(getattr(definition, "maximum_total_exposure", "-1"))) == Decimal("5")
    latest_btc_candle_fresh = freshness_verdict == "fresh"
    latest_cycle_truthful_terminal = latest_cycle is not None and str(getattr(latest_cycle, "state", "")) in {"COMPLETE", "FAILED_CLOSED"} and str(getattr(latest_cycle, "termination_stage", "")) in {"preview_generated", "hold_no_package_created", "failed_closed"}
    worker_recently_ingested = latest_ingestion_candle_at is not None and int((now - latest_ingestion_candle_at.astimezone(timezone.utc)).total_seconds()) <= 1800
    decision_record_linkage_present = latest_cycle_decision_record_id is not None if latest_cycle is not None else False

    ready_package_current = ready_package is not None
    package_historically_advanced = approval_event is not None or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    hold_no_package_expected = latest_cycle_outcome == "HOLD" and not ready_package_current and not package_historically_advanced
    package_progress_distinguishable = hold_no_package_expected or ready_package_current or package_historically_advanced

    exact_package_authorization = approval_event is not None or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    dry_run_passed = bool(evidence.get("dry_run_passed")) or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    bounded_activation_exists = activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled

    autonomous_buy_submitted = buy_submitted and autonomous_buy_provenance
    autonomous_buy_fill_reconciled = buy_fill_reconciled and autonomous_buy_provenance
    autonomous_sell_submitted = sell_submitted and autonomous_sell_provenance
    autonomous_sell_fill_reconciled = sell_fill_reconciled and autonomous_sell_provenance
    position_managed_historically = autonomous_buy_fill_reconciled and (autonomous_sell_submitted or autonomous_sell_fill_reconciled)

    positive_realized_net_profit = realized_net_profit is not None and Decimal(str(realized_net_profit)) > Decimal("0")
    ending_usd_exceeds_starting_usd = (
        starting_reconciled_usd is not None
        and ending_reconciled_usd is not None
        and Decimal(str(ending_reconciled_usd)) > Decimal(str(starting_reconciled_usd))
    )
    fees_known = fees is not None

    stage_1_complete = all([
        provider_connected,
        provider_ready,
        runtime_uses_dedicated_account,
        profile_uses_dedicated_account,
        paper_cash_reconciled,
        paper_liquid_cash_supports_exact_5,
        max_open_positions_one,
        minimum_position_size_five,
        maximum_position_size_five,
        maximum_total_exposure_five,
        provider_reconciliation_clean,
    ])
    stage_2_complete = all([
        stage_1_complete,
        latest_btc_candle_fresh,
        worker_recently_ingested,
        latest_strategy_coherent,
        latest_cycle_truthful_terminal,
    ])
    stage_3_complete = stage_2_complete and (ready_package_current or package_historically_advanced)
    stage_4_complete = stage_3_complete and exact_package_authorization
    stage_5_complete = stage_4_complete and dry_run_passed
    stage_6_complete = stage_5_complete and bounded_activation_exists
    stage_7_complete = stage_6_complete and autonomous_buy_submitted and autonomous_buy_fill_reconciled
    stage_8_complete = stage_7_complete and position_managed_historically
    stage_9_complete = stage_8_complete and autonomous_sell_submitted and autonomous_sell_fill_reconciled
    stage_10_complete = stage_9_complete and all([
        autonomous_buy_provenance,
        autonomous_buy_fill_reconciled,
        autonomous_sell_provenance,
        autonomous_sell_fill_reconciled,
        fees_known,
        ending_usd_exceeds_starting_usd,
        positive_realized_net_profit,
    ])

    stage_rows = [
        (1, "FOUNDATION_READY", stage_1_complete),
        (2, "AUTONOMOUS_EVALUATION_READY", stage_2_complete),
        (3, "READY_PACKAGE_CREATED", stage_3_complete),
        (4, "PACKAGE_AUTHORIZED", stage_4_complete),
        (5, "DRY_RUN_PASSED", stage_5_complete),
        (6, "BOUNDED_ACTIVATION", stage_6_complete),
        (7, "LIVE_BUY_RECONCILED", stage_7_complete),
        (8, "POSITION_MANAGED", stage_8_complete),
        (9, "LIVE_SELL_RECONCILED", stage_9_complete),
        (10, "POSITIVE_NET_PROFIT_CONFIRMED", stage_10_complete),
    ]
    stage_blocking_gate_map = {
        3: "READY_PACKAGE_NOT_YET_CREATED",
        4: "PACKAGE_NOT_AUTHORIZED",
        5: "DRY_RUN_NOT_PASSED",
        6: "BOUNDED_ACTIVATION_MISSING",
        7: "LIVE_BUY_NOT_RECONCILED",
        8: "POSITION_MANAGEMENT_EVIDENCE_MISSING",
        9: "LIVE_SELL_NOT_RECONCILED",
        10: "POSITIVE_NET_PROFIT_NOT_CONFIRMED",
    }

    highest_contiguous_stage = 0
    for stage_number, _, completed in stage_rows:
        if completed:
            highest_contiguous_stage = stage_number
            continue
        break

    def _checkpoint_state(*, passed: bool, waiting: bool = False, not_applicable: bool = False, completed_historically: bool = False) -> str:
        if passed:
            return "PASSED"
        if completed_historically:
            return "COMPLETED_HISTORICALLY"
        if not_applicable:
            return "NOT_APPLICABLE"
        if waiting:
            return "WAITING"
        return "FAILED"

    checkpoint_rows = [
        ("provider_connection_connected", provider_connected, _checkpoint_state(passed=provider_connected)),
        ("provider_readiness_acceptable", provider_ready, _checkpoint_state(passed=provider_ready)),
        ("provider_balance_fresh", provider_balance_fresh, _checkpoint_state(passed=provider_balance_fresh, completed_historically=stage_9_complete and not provider_balance_fresh)),
        ("provider_reconciliation_clean", provider_reconciliation_clean, _checkpoint_state(passed=provider_reconciliation_clean)),
        ("runtime_campaign_matches", runtime_campaign_matches, _checkpoint_state(passed=runtime_campaign_matches)),
        ("runtime_uses_dedicated_account", runtime_uses_dedicated_account, _checkpoint_state(passed=runtime_uses_dedicated_account)),
        ("profile_uses_dedicated_account", profile_uses_dedicated_account, _checkpoint_state(passed=profile_uses_dedicated_account)),
        ("dedicated_account_active", dedicated_account_active, _checkpoint_state(passed=dedicated_account_active)),
        ("paper_cash_reconciled", paper_cash_reconciled, _checkpoint_state(passed=paper_cash_reconciled)),
        ("paper_liquid_cash_supports_exact_5", paper_liquid_cash_supports_exact_5, _checkpoint_state(passed=paper_liquid_cash_supports_exact_5)),
        ("max_open_positions_one", max_open_positions_one, _checkpoint_state(passed=max_open_positions_one)),
        ("minimum_position_size_five", minimum_position_size_five, _checkpoint_state(passed=minimum_position_size_five)),
        ("maximum_position_size_five", maximum_position_size_five, _checkpoint_state(passed=maximum_position_size_five)),
        ("maximum_total_exposure_five", maximum_total_exposure_five, _checkpoint_state(passed=maximum_total_exposure_five)),
        ("latest_btc_candle_fresh_interval_aware", latest_btc_candle_fresh, _checkpoint_state(passed=latest_btc_candle_fresh, waiting=stage_1_complete and not latest_btc_candle_fresh)),
        ("latest_cycle_truthful_terminal", latest_cycle_truthful_terminal, _checkpoint_state(passed=latest_cycle_truthful_terminal)),
        ("worker_recently_ingested_kraken_btc", worker_recently_ingested, _checkpoint_state(passed=worker_recently_ingested, waiting=stage_1_complete and not worker_recently_ingested)),
        ("latest_strategy_identity_coherent", latest_strategy_coherent, _checkpoint_state(passed=latest_strategy_coherent)),
        ("decision_record_linkage_present_when_applicable", decision_record_linkage_present, _checkpoint_state(passed=decision_record_linkage_present, not_applicable=latest_cycle is None)),
        ("ready_package_progress_distinguishable", package_progress_distinguishable, _checkpoint_state(passed=ready_package_current, not_applicable=hold_no_package_expected, completed_historically=package_historically_advanced and not ready_package_current, waiting=not hold_no_package_expected and not ready_package_current and not package_historically_advanced)),
        ("exact_package_authorization_exists", exact_package_authorization, _checkpoint_state(passed=approval_event is not None, not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=exact_package_authorization and approval_event is None, waiting=stage_3_complete and not exact_package_authorization)),
        ("production_dry_run_passed", dry_run_passed, _checkpoint_state(passed=bool(evidence.get("dry_run_passed")), not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=dry_run_passed and not bool(evidence.get("dry_run_passed")), waiting=stage_4_complete and not dry_run_passed)),
        ("bounded_proving_activation_exists", bounded_activation_exists, _checkpoint_state(passed=activation is not None, not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=bounded_activation_exists and activation is None, waiting=stage_5_complete and not bounded_activation_exists)),
        ("live_buy_order_submitted", autonomous_buy_submitted, _checkpoint_state(passed=autonomous_buy_submitted, waiting=stage_6_complete and not autonomous_buy_submitted, not_applicable=not stage_6_complete)),
        ("live_buy_fill_reconciled", autonomous_buy_fill_reconciled, _checkpoint_state(passed=autonomous_buy_fill_reconciled, waiting=autonomous_buy_submitted and not autonomous_buy_fill_reconciled, not_applicable=not stage_6_complete)),
        ("open_live_btc_position_exists", position_open, _checkpoint_state(passed=position_open, completed_historically=position_managed_historically and not position_open, waiting=stage_7_complete and not position_managed_historically, not_applicable=not stage_7_complete)),
        ("live_sell_order_submitted", autonomous_sell_submitted, _checkpoint_state(passed=autonomous_sell_submitted, waiting=stage_8_complete and not autonomous_sell_submitted, not_applicable=not stage_8_complete)),
        ("live_sell_fill_reconciled", autonomous_sell_fill_reconciled, _checkpoint_state(passed=autonomous_sell_fill_reconciled, waiting=autonomous_sell_submitted and not autonomous_sell_fill_reconciled, not_applicable=not stage_8_complete)),
        ("realized_fees_known", fees_known, _checkpoint_state(passed=fees_known, waiting=stage_9_complete and not fees_known, not_applicable=not stage_9_complete)),
        ("ending_usd_exceeds_starting_usd", ending_usd_exceeds_starting_usd, _checkpoint_state(passed=ending_usd_exceeds_starting_usd, waiting=stage_9_complete and fees_known and not ending_usd_exceeds_starting_usd, not_applicable=not stage_9_complete)),
    ]

    completed_checkpoint_count = sum(1 for _, _, state in checkpoint_rows if state in {"PASSED", "COMPLETED_HISTORICALLY"})
    total_checkpoint_count = len(checkpoint_rows)

    first_profit_complete = stage_10_complete

    critical_blocking_gate = next(
        (
            name
            for name, passed, _ in checkpoint_rows
            if not passed
            and name
            in {
                "provider_connection_connected",
                "provider_readiness_acceptable",
                "provider_reconciliation_clean",
                "runtime_campaign_matches",
                "runtime_uses_dedicated_account",
                "profile_uses_dedicated_account",
                "dedicated_account_active",
                "paper_cash_reconciled",
                "latest_strategy_identity_coherent",
                "decision_record_linkage_present_when_applicable",
            }
        ),
        None,
    )

    if first_profit_complete:
        status = "FIRST_AUTONOMOUS_NET_PROFIT_COMPLETE"
        blocking_gate = None
    elif critical_blocking_gate is not None:
        status = "BLOCKED"
        blocking_gate = critical_blocking_gate
    elif freshness_verdict != "fresh":
        status = "WAITING_FOR_FRESH_MARKET_DATA"
        blocking_gate = "latest_btc_candle_fresh_interval_aware"
    elif ready_package is not None and approval_event is None:
        status = "READY_PACKAGE_AVAILABLE"
        blocking_gate = "exact_package_authorization_exists"
    elif approval_event is not None and not bool(evidence.get("dry_run_passed")):
        status = "WAITING_FOR_DRY_RUN"
        blocking_gate = "production_dry_run_passed"
    elif bool(evidence.get("dry_run_passed")) and activation is None:
        status = "WAITING_FOR_ACTIVATION"
        blocking_gate = "bounded_proving_activation_exists"
    elif buy_submitted and not buy_fill_reconciled:
        status = "WAITING_FOR_BUY_FILL"
        blocking_gate = "live_buy_fill_reconciled"
    elif position_open and not sell_submitted:
        status = "POSITION_OPEN"
        blocking_gate = "live_sell_order_submitted"
    elif sell_submitted and not sell_fill_reconciled:
        status = "WAITING_FOR_SELL_FILL"
        blocking_gate = "live_sell_fill_reconciled"
    elif buy_fill_reconciled and sell_fill_reconciled:
        status = "VERIFYING_NET_PROFIT"
        blocking_gate = "ending_usd_exceeds_starting_usd"
    elif latest_cycle_outcome == "HOLD":
        status = "WAITING_FOR_EXECUTABLE_SIGNAL"
        blocking_gate = stage_blocking_gate_map.get(highest_contiguous_stage + 1, "READY_PACKAGE_NOT_YET_CREATED")
    else:
        status = "BLOCKED"
        blocking_gate = next((name for name, passed, _ in checkpoint_rows if not passed and name != "ready_package_progress_distinguishable"), "missing_safety_evidence")

    completion_percent = _FIRST_PROFIT_STAGE_ANCHORS.get(highest_contiguous_stage, 0.0)

    next_action_map = {
        "BLOCKED": "run evidence audit and fix the first failed safety gate",
        "WAITING_FOR_FRESH_MARKET_DATA": "wait for next closed Kraken BTC 15m candle ingestion",
        "WAITING_FOR_EXECUTABLE_SIGNAL": "wait for actionable BUY or SELL decision evidence",
        "READY_PACKAGE_AVAILABLE": "record canonical package authorization",
        "WAITING_FOR_AUTHORIZATION": "record canonical package authorization",
        "WAITING_FOR_DRY_RUN": "run canonical package dry run",
        "WAITING_FOR_ACTIVATION": "activate bounded proving",
        "WAITING_FOR_BUY_FILL": "wait for BUY fill reconciliation",
        "POSITION_OPEN": "wait for SELL signal and submit bounded SELL",
        "WAITING_FOR_SELL_FILL": "wait for SELL fill reconciliation",
        "VERIFYING_NET_PROFIT": "verify reconciled net profit including fees",
        "FIRST_AUTONOMOUS_NET_PROFIT_COMPLETE": "record milestone completion evidence",
    }

    safe_to_submit_order_now = status in {"READY_PACKAGE_AVAILABLE", "WAITING_FOR_AUTHORIZATION", "WAITING_FOR_DRY_RUN", "WAITING_FOR_ACTIVATION"} and open_live_order_count == 0 and unresolved_reconciliation_count == 0

    return {
        "completion_percent": completion_percent,
        "completed_checkpoint_count": completed_checkpoint_count,
        "total_checkpoint_count": total_checkpoint_count,
        "status": status,
        "blocking_gate": blocking_gate,
        "latest_cycle_id": None if latest_cycle is None else str(latest_cycle.cycle_id),
        "latest_cycle_outcome": latest_cycle_outcome,
        "latest_cycle_reason": latest_cycle_reason,
        "ready_package_id": None if ready_package is None else str(ready_package.package_id),
        "activation_id": None if activation is None else str(activation.activation_id),
        "provider_equity": None if provider_equity is None else format(Decimal(str(provider_equity)), "f"),
        "paper_liquid_cash": None if paper_liquid_cash is None else format(Decimal(str(paper_liquid_cash)), "f"),
        "open_live_order_count": open_live_order_count,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
        "live_position_state": "OPEN" if position_open else "FLAT",
        "realized_gross_profit": None if realized_gross_profit is None else format(Decimal(str(realized_gross_profit)), "f"),
        "fees": None if fees is None else format(Decimal(str(fees)), "f"),
        "realized_net_profit": None if realized_net_profit is None else format(Decimal(str(realized_net_profit)), "f"),
        "safe_to_submit_order_now": safe_to_submit_order_now,
        "exact_next_operator_action": next_action_map.get(status, "review checkpoint evidence"),
        "stage": {
            "highest_contiguous_completed": highest_contiguous_stage,
            "name": dict((n, s) for n, s, _ in stage_rows).get(highest_contiguous_stage, "NONE"),
            "rows": [
                {
                    "number": stage_number,
                    "name": stage_name,
                    "completed": completed,
                    "anchor_percent": _FIRST_PROFIT_STAGE_ANCHORS[stage_number],
                }
                for stage_number, stage_name, completed in stage_rows
            ],
        },
        "evidence": {
            "provider_balance_synced_at": None if provider_balance_synced_at is None else provider_balance_synced_at.isoformat(),
            "latest_candle_close_time": None if latest_candle is None else latest_candle.close_time.isoformat(),
            "evaluation_time": now.isoformat(),
            "freshness_seconds": freshness_seconds,
            "freshness_minutes": freshness_minutes,
            "candle_interval_minutes": candle_interval_minutes,
            "ingestion_grace_minutes": ingestion_grace_minutes,
            "maximum_age_minutes": maximum_age_minutes,
            "freshness_verdict": freshness_verdict,
            "latest_decision_record_id": latest_cycle_decision_record_id,
            "latest_strategy_identity": latest_strategy_identity,
            "latest_strategy_version": latest_strategy_version,
            "latest_ingestion_candle_at": None if latest_ingestion_candle_at is None else latest_ingestion_candle_at.isoformat(),
            "approval_event_id": None if approval_event is None else str(approval_event.id),
            "dry_run_live_crypto_order_id": None if activation is None else str(activation.dry_run_live_crypto_order_id),
            "autonomous_buy_provenance": autonomous_buy_provenance,
            "autonomous_sell_provenance": autonomous_sell_provenance,
            "starting_reconciled_usd": None if starting_reconciled_usd is None else format(Decimal(str(starting_reconciled_usd)), "f"),
            "ending_reconciled_usd": None if ending_reconciled_usd is None else format(Decimal(str(ending_reconciled_usd)), "f"),
            "package_progress_mode": (
                "hold_no_package_expected"
                if hold_no_package_expected
                else "ready_package_current"
                if ready_package_current
                else "ready_package_historically_advanced"
                if package_historically_advanced
                else "undetermined"
            ),
        },
        "checkpoints": [
            {"name": name, "passed": passed, "state": state}
            for name, passed, state in checkpoint_rows
        ],
        "formula": {
            "completion_percent": "stage anchor for highest contiguous completed stage",
            "stage_anchors": _FIRST_PROFIT_STAGE_ANCHORS,
            "milestone_complete": "autonomous_buy_provenance AND autonomous_buy_fill_reconciled AND autonomous_sell_provenance AND autonomous_sell_fill_reconciled AND fees_known AND ending_usd_exceeds_starting_usd AND realized_net_profit > 0",
        },
    }


async def _gather_first_autonomous_profit_evidence(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        connection = await db.scalar(
            select(ExchangeConnection)
            .where(ExchangeConnection.provider == provider)
            .where(ExchangeConnection.environment == environment)
            .order_by(desc(ExchangeConnection.updated_at), desc(ExchangeConnection.exchange_connection_id))
            .limit(1)
        )
        runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == runtime_campaign_id).limit(1))
        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )
        paper_account = await db.get(PaperAccount, paper_account_id)
        profile = await db.get(LiveTradingProfile, live_trading_profile_id)

        asset_symbol = _product_symbol(product_id)
        asset = await db.scalar(
            select(Asset)
            .where(Asset.symbol == asset_symbol)
            .where(Asset.exchange == provider)
            .where(Asset.asset_class == "crypto")
            .where(Asset.is_active.is_(True))
            .order_by(desc(Asset.created_at), desc(Asset.id))
            .limit(1)
        )

        latest_candle = None
        latest_ingestion_candle_at = None
        if asset is not None:
            latest_candle = await db.scalar(
                select(Candle)
                .where(Candle.asset_id == asset.id)
                .where(Candle.interval == _KRKN_BTC_INTERVAL)
                .where(Candle.close_time <= now)
                .order_by(desc(Candle.close_time), desc(Candle.id))
                .limit(1)
            )
            latest_ingestion_candle_at = await db.scalar(
                select(Candle.created_at)
                .where(Candle.asset_id == asset.id)
                .where(Candle.interval == _KRKN_BTC_INTERVAL)
                .order_by(desc(Candle.close_time), desc(Candle.id))
                .limit(1)
            )

        latest_cycle = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.capital_campaign_id == campaign_id)
            .where(AutonomousCycleRun.capital_campaign_version == campaign_version)
            .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
            .limit(1)
        )

        ready_package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(CanonicalPreviewPackage.campaign_id == campaign_id)
            .where(CanonicalPreviewPackage.campaign_version == campaign_version)
            .where(CanonicalPreviewPackage.package_state == "READY")
            .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
            .limit(1)
        )

        approval_event = None
        if ready_package is not None:
            approval_rows = list(
                (
                    await db.execute(
                        select(LiveApprovalEvent)
                        .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
                        .where(LiveApprovalEvent.approval_state == "approved")
                        .where(LiveApprovalEvent.checkpoint_type == "bounded_proving_entry")
                        .order_by(desc(LiveApprovalEvent.recorded_at), desc(LiveApprovalEvent.id))
                        .limit(100)
                    )
                ).scalars().all()
            )
            for item in approval_rows:
                scope = item.approval_scope if isinstance(item.approval_scope, dict) else {}
                if str(scope.get("canonical_preview_package_id") or "") == str(ready_package.package_id):
                    approval_event = item
                    break

        activation = await db.scalar(
            select(CanonicalProvingActivation)
            .where(CanonicalProvingActivation.campaign_id == campaign_id)
            .where(CanonicalProvingActivation.campaign_version == campaign_version)
            .where(CanonicalProvingActivation.provider == provider)
            .where(CanonicalProvingActivation.environment == environment)
            .where(CanonicalProvingActivation.product == product_id)
            .order_by(desc(CanonicalProvingActivation.activated_at), desc(CanonicalProvingActivation.activation_id))
            .limit(1)
        )

        unresolved_reconciliation_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
                    .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                )
            )
            or 0
        )
        unknown_reconciliation_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
                    .where(LiveReconciliationEvent.reconciliation_status == "unknown")
                )
            )
            or 0
        )
        open_live_order_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == provider)
                    .where(LiveCryptoOrder.environment == environment)
                    .where(LiveCryptoOrder.product_id == product_id)
                    .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                )
            )
            or 0
        )

        orders = list(
            (
                await db.execute(
                    select(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == provider)
                    .where(LiveCryptoOrder.environment == environment)
                    .where(LiveCryptoOrder.product_id == product_id)
                    .order_by(desc(LiveCryptoOrder.created_at), desc(LiveCryptoOrder.live_crypto_order_id))
                    .limit(200)
                )
            ).scalars().all()
        )
        buy_submitted = any(str(item.side).upper() == "BUY" and item.submitted_at is not None for item in orders)
        buy_fill_reconciled = any(str(item.side).upper() == "BUY" and item.filled_at is not None for item in orders)
        sell_submitted = any(str(item.side).upper() == "SELL" and item.submitted_at is not None for item in orders)
        sell_fill_reconciled = any(str(item.side).upper() == "SELL" and item.filled_at is not None for item in orders)
        autonomous_buy_provenance = any(
            str(item.side).upper() == "BUY"
            and item.decision_record_id is not None
            and item.submitted_at is not None
            for item in orders
        )
        autonomous_sell_provenance = any(
            str(item.side).upper() == "SELL"
            and item.decision_record_id is not None
            and item.submitted_at is not None
            for item in orders
        )

        position_open = False
        if asset is not None and paper_account is not None:
            trades = list(
                (
                    await db.execute(
                        select(Trade)
                        .where(Trade.paper_account_id == paper_account.id)
                        .where(Trade.asset_id == asset.id)
                        .order_by(Trade.executed_at.asc(), Trade.id.asc())
                    )
                ).scalars().all()
            )
            net_qty = Decimal("0")
            for item in trades:
                qty = Decimal(str(item.quantity))
                if str(item.side).lower() == "buy":
                    net_qty += qty
                elif str(item.side).lower() == "sell":
                    net_qty -= qty
            position_open = net_qty > Decimal("0")

        dry_run_passed = False
        if activation is not None:
            dry_run_order = await db.get(LiveCryptoOrder, activation.dry_run_live_crypto_order_id)
            dry_run_passed = dry_run_order is not None and str(dry_run_order.status) == "DRY_RUN_READY"

    realized_net_profit = None if runtime is None else Decimal(str(runtime.realized_profit))
    fees = None if runtime is None else Decimal(str(runtime.fees))
    realized_gross_profit = None if realized_net_profit is None or fees is None else (realized_net_profit + fees)
    starting_reconciled_usd = None if runtime is None else Decimal(str(runtime.starting_capital))
    ending_reconciled_usd = None if runtime is None else Decimal(str(runtime.current_equity))
    paper_liquid_cash = None if paper_account is None else Decimal(str(paper_account.current_cash_balance))
    provider_equity = None if connection is None else connection.total_equity_usd

    return {
        "now": now,
        "campaign_id": campaign_id,
        "campaign_version": campaign_version,
        "paper_account_id": paper_account_id,
        "connection": connection,
        "runtime": runtime,
        "definition": definition,
        "paper_account": paper_account,
        "profile": profile,
        "latest_candle": latest_candle,
        "latest_ingestion_candle_at": latest_ingestion_candle_at,
        "latest_cycle": latest_cycle,
        "ready_package": ready_package,
        "approval_event": approval_event,
        "activation": activation,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
        "unknown_reconciliation_count": unknown_reconciliation_count,
        "open_live_order_count": open_live_order_count,
        "buy_submitted": buy_submitted,
        "buy_fill_reconciled": buy_fill_reconciled,
        "sell_submitted": sell_submitted,
        "sell_fill_reconciled": sell_fill_reconciled,
        "autonomous_buy_provenance": autonomous_buy_provenance,
        "autonomous_sell_provenance": autonomous_sell_provenance,
        "position_open": position_open,
        "dry_run_passed": dry_run_passed,
        "provider_equity": provider_equity,
        "paper_liquid_cash": paper_liquid_cash,
        "provider_readiness_verdict": None if connection is None else connection.last_readiness_verdict,
        "provider_balance_synced_at": None if connection is None else connection.last_successful_sync_at,
        "starting_reconciled_usd": starting_reconciled_usd,
        "ending_reconciled_usd": ending_reconciled_usd,
        "realized_gross_profit": realized_gross_profit,
        "fees": fees,
        "realized_net_profit": realized_net_profit,
    }


async def first_autonomous_profit_status(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    evidence = await _gather_first_autonomous_profit_evidence(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        runtime_campaign_id=runtime_campaign_id,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
    )
    payload = _derive_first_autonomous_profit_status(evidence)
    payload["invariants"] = {
        "read_only": True,
        "no_provider_order_submission": True,
        "checkpoint_count": 30,
    }
    return payload


def _runtime_exchange_scope(runtime_exchange: str | None) -> tuple[str | None, str | None]:
    raw = (runtime_exchange or "").strip().lower()
    if not raw:
        return None, None
    if raw.endswith("_sandbox"):
        return raw.removesuffix("_sandbox"), "sandbox"
    return raw, "production"


def _coerce_decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _resolve_since_datetime(value: str) -> datetime:
    raw = value.strip()
    lowered = raw.lower()
    if lowered in {"now", "0", "0m", "0h", "0d"}:
        return datetime.now(timezone.utc)

    relative = re.fullmatch(r"(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if "minute" in unit:
            return datetime.now(timezone.utc) - timedelta(minutes=amount)
        if "hour" in unit:
            return datetime.now(timezone.utc) - timedelta(hours=amount)
        return datetime.now(timezone.utc) - timedelta(days=amount)

    candidate = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_uuid_list(values: Any) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []
    out: list[uuid.UUID] = []
    for value in values:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _sum_trade_quantity(trades: list[Trade], *, side: str) -> Decimal:
    total = Decimal("0")
    for trade in trades:
        if trade.side == side:
            total += Decimal(str(trade.quantity))
    return total


def _infer_non_candidate_reason(signals: list[Signal]) -> str:
    if not signals:
        return "UNPROVEN"
    actionable = [item for item in signals if item.action in {"buy", "sell"}]
    if not actionable:
        return "HOLD"
    return "UNPROVEN"


def _event_payload_campaign_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        campaign_id = metadata.get("campaign_id")
        if campaign_id is not None:
            return str(campaign_id)
    return None


async def _compute_position_quantity(
    *,
    db: Any,
    paper_account_id: uuid.UUID,
    asset_id: uuid.UUID,
    executed_at: datetime,
    include_trade_at_timestamp: bool,
) -> Decimal:
    trades = list(
        (
            await db.execute(
                select(Trade)
                .where(Trade.paper_account_id == paper_account_id)
                .where(Trade.asset_id == asset_id)
                .where(Trade.executed_at <= executed_at)
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )
        ).scalars().all()
    )
    total = Decimal("0")
    for trade in trades:
        if not include_trade_at_timestamp and trade.executed_at == executed_at:
            continue
        qty = Decimal(str(trade.quantity))
        if trade.side == "buy":
            total += qty
        elif trade.side == "sell":
            total -= qty
    return max(Decimal("0"), total)


def _execution_summary_from_audits(audit_rows: list[dict[str, Any]], trades: list[Trade]) -> dict[str, Any]:
    actions = {str(item.get("action") or "") for item in audit_rows}
    service_called = any(action.startswith("signal_execution") for action in actions) or bool(trades)
    rejected = any("rejected" in action for action in actions)
    skipped = any("duplicate" in action for action in actions)
    errored = any("failed" in action for action in actions)
    return {
        "execution_service_called": service_called,
        "order_creation_reason": "paper_internal_sim_creates_trade_directly" if trades else "paper_order_model_absent",
        "trade_created": bool(trades),
        "rejected": rejected,
        "skipped": skipped,
        "error": errored,
    }


async def _build_cycle_forensics(*, db: Any, cycle: AutonomousCycleRun) -> dict[str, Any]:
    decision: DecisionRecord | None = None
    if cycle.decision_record_id is not None:
        decision = await db.get(DecisionRecord, cycle.decision_record_id)

    signal_ids: list[uuid.UUID] = []
    if decision is not None:
        source_lineage = decision.source_lineage or {}
        signal_ids = _safe_uuid_list(source_lineage.get("signals"))

    signals: list[Signal] = []
    if signal_ids:
        signals = list(
            (
                await db.execute(
                    select(Signal)
                    .where(Signal.id.in_(signal_ids))
                    .order_by(Signal.created_at.asc(), Signal.id.asc())
                )
            ).scalars().all()
        )

    strategy_map: dict[uuid.UUID, Strategy] = {}
    if signals:
        strategy_ids = sorted({item.strategy_id for item in signals}, key=str)
        if strategy_ids:
            strategies = list((await db.execute(select(Strategy).where(Strategy.id.in_(strategy_ids)))).scalars().all())
            strategy_map = {item.id: item for item in strategies}

    asset_map: dict[uuid.UUID, Asset] = {}
    if signals:
        asset_ids = sorted({item.asset_id for item in signals}, key=str)
        if asset_ids:
            assets = list((await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all())
            asset_map = {item.id: item for item in assets}

    risk_events: list[RiskEvent] = []
    if signal_ids:
        risk_events = list(
            (
                await db.execute(
                    select(RiskEvent)
                    .where(RiskEvent.related_signal_id.in_(signal_ids))
                    .order_by(RiskEvent.created_at.asc(), RiskEvent.id.asc())
                )
            ).scalars().all()
        )
    if cycle.risk_event_id is not None and all(item.id != cycle.risk_event_id for item in risk_events):
        extra_event = await db.get(RiskEvent, cycle.risk_event_id)
        if extra_event is not None:
            risk_events.append(extra_event)

    trades: list[Trade] = []
    if signal_ids:
        trades = list(
            (
                await db.execute(
                    select(Trade)
                    .where(Trade.signal_id.in_(signal_ids))
                    .order_by(Trade.executed_at.asc(), Trade.id.asc())
                )
            ).scalars().all()
        )

    audit_rows: list[dict[str, Any]] = []
    if signal_ids:
        audit_rows = [
            {
                "id": item.id,
                "created_at": item.created_at,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "before_state": item.before_state,
                "after_state": item.after_state,
            }
            for item in (
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "signal")
                        .where(AuditLog.entity_id.in_(signal_ids))
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    )
                ).scalars().all()
            )
        ]

    execution_summary = _execution_summary_from_audits(audit_rows, trades)

    interval = decision.timeframe if decision is not None else None
    provider = None
    latest_candle_time = None
    primary_asset_id = signals[0].asset_id if signals else None
    primary_asset = asset_map.get(primary_asset_id) if primary_asset_id is not None else None
    if primary_asset is not None:
        provider = primary_asset.exchange
        if interval is None:
            interval = (cycle.cycle_context or {}).get("strategy_interval") if isinstance(cycle.cycle_context, dict) else None
        if interval is not None:
            latest_candle_time = await db.scalar(
                select(Candle.close_time)
                .where(Candle.asset_id == primary_asset.id)
                .where(Candle.interval == interval)
                .order_by(Candle.open_time.desc())
                .limit(1)
            )

    candidate = any(item.action in {"buy", "sell"} for item in signals)
    candidate_reason = None
    if not candidate:
        candidate_reason = _infer_non_candidate_reason(signals)

    accounting_entries: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    trade_fill_evidence = 0
    balance_change_observed = 0
    position_change_observed = 0
    position_change_unproven = 0
    balance_change_unproven = 0
    for trade in trades:
        total_fees += Decimal(str(trade.fee))
        before_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=False,
        )
        after_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=True,
        )

        trade_audit = await db.scalar(
            select(AuditLog)
            .where(AuditLog.entity_type == "trade")
            .where(AuditLog.entity_id == trade.id)
            .where(AuditLog.action == "paper_trade_simulated")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        before_balance = None
        after_balance = None
        if trade_audit is not None:
            before_state = trade_audit.before_state if isinstance(trade_audit.before_state, dict) else {}
            after_state = trade_audit.after_state if isinstance(trade_audit.after_state, dict) else {}
            before_balance = before_state.get("cash_balance")
            after_balance = after_state.get("cash_balance")
            trade_fill_evidence += 1
        if before_balance is not None and after_balance is not None and str(before_balance) != str(after_balance):
            balance_change_observed += 1
        elif before_balance is None or after_balance is None:
            balance_change_unproven += 1

        if before_position != after_position:
            position_change_observed += 1
        elif before_position is None or after_position is None:
            position_change_unproven += 1

        accounting_entries.append(
            {
                "trade_id": trade.id,
                "paper_account_id": trade.paper_account_id,
                "asset_id": trade.asset_id,
                "balance_before": before_balance,
                "balance_after": after_balance,
                "position_before": _decimal_str(before_position),
                "position_after": _decimal_str(after_position),
                "fee": _decimal_str(trade.fee),
                "executed_at": trade.executed_at,
            }
        )

    roster_runs = list(
        (
            await db.execute(
                select(StrategyRosterRun)
                .where(StrategyRosterRun.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterRun.started_at.asc(), StrategyRosterRun.roster_run_id.asc())
            )
        ).scalars().all()
    )

    roster_proposals = list(
        (
            await db.execute(
                select(StrategyRosterProposal)
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposal.evaluated_at.asc(), StrategyRosterProposal.proposal_id.asc())
            )
        ).scalars().all()
    )

    outcome_score_rows = list(
        (
            await db.execute(
                select(StrategyRosterProposalOutcome)
                .join(
                    StrategyRosterProposal,
                    StrategyRosterProposalOutcome.proposal_id == StrategyRosterProposal.proposal_id,
                )
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposalOutcome.evaluated_at.asc(), StrategyRosterProposalOutcome.outcome_id.asc())
            )
        ).scalars().all()
    )

    event_start = cycle.started_at - timedelta(minutes=5)
    event_end = (cycle.completed_at or cycle.started_at) + timedelta(minutes=30)
    research_events = list(
        (
            await db.execute(
                select(ValidationRunEvent)
                .where(ValidationRunEvent.created_at >= event_start)
                .where(ValidationRunEvent.created_at <= event_end)
                .where(ValidationRunEvent.event_type.like("RESEARCH_CYCLE_%"))
                .order_by(ValidationRunEvent.created_at.asc(), ValidationRunEvent.id.asc())
            )
        ).scalars().all()
    )

    signal_rows = []
    for signal in signals:
        strategy = strategy_map.get(signal.strategy_id)
        signal_rows.append(
            {
                "signal_id": signal.id,
                "strategy_id": signal.strategy_id,
                "strategy": None if strategy is None else strategy.slug,
                "action": signal.action.upper(),
                "confidence": _decimal_str(signal.ai_confidence),
                "reason": None,
                "status": signal.status,
                "asset_id": signal.asset_id,
            }
        )

    cycle_context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    cycle_handoff = cycle_context.get("execution_handoff") if isinstance(cycle_context.get("execution_handoff"), dict) else {}
    strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
    strategy_signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context.get("signal_payload"), dict) else {}
    autonomous_proposed_action = (getattr(cycle, "proposed_action", None) or strategy_signal_payload.get("action") or "HOLD").upper()
    if autonomous_proposed_action not in {"BUY", "SELL", "HOLD"}:
        autonomous_proposed_action = "HOLD"

    roster_buy = sum(1 for item in roster_proposals if str(item.action).upper() == "BUY")
    roster_sell = sum(1 for item in roster_proposals if str(item.action).upper() == "SELL")
    roster_hold = sum(1 for item in roster_proposals if str(item.action).upper() == "HOLD")
    roster_mode = "SHADOW"
    roster_executable = "NO"
    roster_reason = "Strategy Roster proposals are shadow research observations and never executable orders"

    canonical_signal = cycle_handoff.get("canonical_signal") if isinstance(cycle_handoff.get("canonical_signal"), dict) else None

    if cycle_handoff:
        execution_handoff_status = str(cycle_handoff.get("execution_handoff") or "UNPROVEN")
        cycle_handoff_status = str(cycle_handoff.get("status") or "UNPROVEN")
        if cycle_handoff_status in {"PAPER_EXECUTION_FAILED", "PAPER_EXECUTION_REJECTED", "PAPER_EXECUTION_SKIPPED"}:
            execution_handoff_blocker = str(cycle_handoff.get("exact_result") or cycle_handoff_status)
        else:
            execution_handoff_blocker = "NOT APPLICABLE"
    elif signal_rows:
        execution_handoff_status = "LEGACY_SIGNAL_PIPELINE"
        execution_handoff_blocker = "NOT APPLICABLE"
    elif autonomous_proposed_action in {"BUY", "SELL"}:
        execution_handoff_status = "NOT IMPLEMENTED"
        execution_handoff_blocker = "AUTONOMOUS_CANONICAL_SIGNAL_HANDOFF_NOT_IMPLEMENTED"
    else:
        execution_handoff_status = "NOT APPLICABLE"
        execution_handoff_blocker = "HOLD_ACTION"

    summary = "No legacy executable signals linked to this autonomous cycle"
    if cycle_handoff:
        summary = str(cycle_handoff.get("status") or "UNPROVEN")
    elif candidate and execution_summary.get("trade_created"):
        summary = "Actionable signal became paper trade"
    elif candidate and execution_summary.get("rejected"):
        summary = "Actionable signal rejected before trade"
    elif candidate and execution_summary.get("skipped"):
        summary = "Actionable signal skipped"
    elif candidate and not execution_summary.get("execution_service_called"):
        summary = "Actionable signal not executed"

    candidate_status = "UNPROVEN" if not signal_rows and canonical_signal is None else ("YES" if candidate else "NO")
    if canonical_signal is not None and str(canonical_signal.get("executable") or "NO").upper() == "YES":
        candidate_status = "YES"
    risk_evaluated_status = "YES" if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_decision = risk_events[-1].action_taken if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_reason = risk_events[-1].detail if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")

    execution_attempted_status = "YES" if bool(cycle_handoff.get("attempted")) else ("YES" if candidate else "NO")
    execution_service_called_status = (
        "YES"
        if (bool(cycle_handoff.get("attempted")) or execution_summary.get("execution_service_called"))
        else "UNPROVEN"
        if candidate
        else "NOT APPLICABLE"
    )
    order_created_status = "NOT APPLICABLE"
    trade_created_status = "YES" if execution_summary.get("trade_created") else "NO"
    if trades:
        filled_status = "YES" if trade_fill_evidence == len(trades) else "UNPROVEN"
    elif candidate:
        filled_status = "NO"
    else:
        filled_status = "NOT APPLICABLE"

    rejected_status = "YES" if execution_summary.get("rejected") else ("NO" if candidate else "NOT APPLICABLE")
    skipped_status = "YES" if execution_summary.get("skipped") else ("NO" if candidate else "NOT APPLICABLE")
    error_status = "YES" if execution_summary.get("error") else ("NO" if candidate else "NOT APPLICABLE")

    decision_record_linkage_status = "YES" if cycle.decision_record_id is not None else ("UNPROVEN" if signal_rows else "NOT APPLICABLE")
    outcome_linkage_status = (
        "YES"
        if outcome_score_rows
        else "NO"
        if cycle.decision_record_id is not None
        else "UNPROVEN"
    )
    research_linkage_status = "YES" if research_events else "NO"

    account_balance_changed_status = (
        "YES"
        if balance_change_observed > 0
        else "UNPROVEN"
        if trades and balance_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    position_changed_status = (
        "YES"
        if position_change_observed > 0
        else "UNPROVEN"
        if trades and position_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    accounting_entry_status = "YES" if trade_fill_evidence > 0 else ("UNPROVEN" if trades else "NOT APPLICABLE")

    return {
        "cycle_id": cycle.cycle_id,
        "timestamp": cycle.started_at,
        "asset": None if primary_asset is None else primary_asset.symbol,
        "asset_id": primary_asset_id,
        "provider": provider,
        "interval": interval,
        "latest_candle_time": latest_candle_time,
        "signal_section": {
            "signals_generated": len(signal_rows),
            "signals": signal_rows,
            "source": "signals_table_via_decision_lineage",
        },
        "strategy_roster": {
            "proposal_count": len(roster_proposals),
            "buy_count": roster_buy,
            "sell_count": roster_sell,
            "hold_count": roster_hold,
            "mode": roster_mode,
            "executable": roster_executable,
            "reason": roster_reason,
        },
        "canonical_signal": {
            "signal_id": (canonical_signal or {}).get("signal_id"),
            "action": (canonical_signal or {}).get("action"),
            "executable": (canonical_signal or {}).get("executable", "NO"),
            "mode": (canonical_signal or {}).get("mode", "PAPER"),
        },
        "autonomous_decision": {
            "proposed_action": autonomous_proposed_action,
            "mandate_verdict": getattr(cycle, "mandate_verdict", None) or "UNPROVEN",
            "risk_verdict": getattr(cycle, "risk_verdict", None) or "UNPROVEN",
            "execution_handoff": execution_handoff_status,
            "exact_blocker": execution_handoff_blocker,
        },
        "execution_candidate": {
            "is_candidate": candidate,
            "status": candidate_status,
            "reason_if_no": candidate_reason if candidate_status == "NO" else "NOT APPLICABLE",
        },
        "risk": {
            "evaluated_status": risk_evaluated_status,
            "decision": risk_decision,
            "reason": risk_reason,
            "risk_event_ids": [item.id for item in risk_events],
        },
        "execution": {
            "execution_attempted_status": execution_attempted_status,
            "execution_service_called_status": execution_service_called_status,
            "exact_result": cycle_handoff.get("exact_result") if cycle_handoff else None,
            "order_created_status": order_created_status,
            "order_creation_reason": execution_summary.get("order_creation_reason"),
            "trade_created_status": trade_created_status,
            "filled_status": filled_status,
            "rejected_status": rejected_status,
            "skipped_status": skipped_status,
            "error_status": error_status,
            "trade_ids": [item.id for item in trades],
            "signal_ids": signal_ids,
        },
        "accounting": {
            "paper_account_ids": sorted({item.paper_account_id for item in trades}, key=str),
            "entries": accounting_entries,
            "fees_total": _decimal_str(total_fees),
            "pnl": decision.pnl if decision is not None else None,
            "buy_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="buy")),
            "sell_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="sell")),
            "account_balance_changed_status": account_balance_changed_status,
            "position_changed_status": position_changed_status,
            "accounting_entry_persisted_status": accounting_entry_status,
        },
        "decision_records": {
            "decision_record_id": cycle.decision_record_id,
            "outcome_score_linkage_count": len(outcome_score_rows),
            "outcome_score_ids": [item.outcome_id for item in outcome_score_rows],
            "decision_record_linkage_status": decision_record_linkage_status,
            "outcome_linkage_status": outcome_linkage_status,
            "research_linkage_status": research_linkage_status,
            "research_linkage": [
                {
                    "event_id": item.id,
                    "event_type": item.event_type,
                    "campaign_id": _event_payload_campaign_id(item.payload),
                    "created_at": item.created_at,
                }
                for item in research_events
            ],
            "autonomous_cycle_linkage": {
                "cycle_id": cycle.cycle_id,
                "scheduled_roster_run_ids": [item.roster_run_id for item in roster_runs],
            },
        },
        "summary": summary,
    }


async def fetch_execution_forensics(
    *,
    since: str | None,
    cycle_id: UUID | None,
    latest: bool,
) -> dict[str, Any]:
    selectors = int(bool(since)) + int(cycle_id is not None) + int(latest)
    if selectors != 1:
        raise ValueError("Choose exactly one selector: --since, --cycle, or --latest")

    async with AsyncSessionLocal() as db:
        cycles: list[AutonomousCycleRun]
        criteria: dict[str, Any] = {
            "selector": "latest" if latest else "cycle" if cycle_id is not None else "since",
            "since": since,
            "cycle_id": cycle_id,
        }

        if latest:
            item = await db.scalar(select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1))
            cycles = [] if item is None else [item]
        elif cycle_id is not None:
            item = await db.get(AutonomousCycleRun, cycle_id)
            if item is None:
                raise ValueError(f"Cycle {cycle_id} not found")
            cycles = [item]
        else:
            assert since is not None
            threshold = _resolve_since_datetime(since)
            criteria["resolved_since"] = threshold
            cycles = list(
                (
                    await db.execute(
                        select(AutonomousCycleRun)
                        .where(AutonomousCycleRun.started_at >= threshold)
                        .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                        .limit(_EXECUTION_FORENSICS_MAX_SINCE_CYCLES)
                    )
                ).scalars().all()
            )
            criteria["max_cycles"] = _EXECUTION_FORENSICS_MAX_SINCE_CYCLES

        deduped_cycles: list[AutonomousCycleRun] = []
        seen_cycle_ids: set[uuid.UUID] = set()
        for item in cycles:
            cycle_key = item.cycle_id
            if cycle_key in seen_cycle_ids:
                continue
            seen_cycle_ids.add(cycle_key)
            deduped_cycles.append(item)
        cycles = deduped_cycles

        reports = [await _build_cycle_forensics(db=db, cycle=item) for item in cycles]

    return {
        "mode": "read_only_forensics",
        "criteria": criteria,
        "cycle_count": len(reports),
        "truncated": bool(since) and len(reports) >= _EXECUTION_FORENSICS_MAX_SINCE_CYCLES,
        "cycles": reports,
    }


def _seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds()))


def _preview_command_mode(*, replayed: bool, command_name: str) -> str:
    if command_name == "preview-show":
        return "VIEW_EXISTING"
    return "IDEMPOTENT_REPLAY" if replayed else "NEW_PREVIEW"


def _decision_classification(*, proposed_action: str | None, risk_verdict: str | None, deterministic_explanation: list[str], failure_reason: str | None) -> str:
    action = (proposed_action or "").upper()
    risk = (risk_verdict or "").upper()
    explanation_blob = " ".join(deterministic_explanation).lower()
    reason = (failure_reason or "").lower()

    if reason.startswith("mandate_status_") or "mandate_not_active" in explanation_blob or "mandate_version_invalid" in reason:
        return "MANDATE_REJECTED"
    if "reconciliation_not_ready" in reason or "provider_not_ready" in reason or "insufficient_candle_context" in explanation_blob or "exchange_connection_not_found" in reason:
        return "INFRASTRUCTURE_BLOCKED"
    if risk == "REJECTED":
        return "RISK_REJECTED"
    if action == "HOLD":
        if "strategy_evaluated" in explanation_blob or "signal_action=hold" in explanation_blob:
            return "STRATEGY_DERIVED"
        return "SAFETY_HOLD" if explanation_blob else "INFRASTRUCTURE_BLOCKED"
    if action in {"BUY", "SELL"}:
        return "STRATEGY_DERIVED"
    return "INFRASTRUCTURE_BLOCKED"


def _capital_state(*, preview: CryptoOrderPreview | None, proposed_action: str | None) -> str:
    if preview is not None:
        return "PREVIEW_ONLY"
    if (proposed_action or "").upper() == "HOLD":
        return "NONE"
    return "UNKNOWN"


def _build_timeline_payload(
    *,
    command_mode: str,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cycle_created_at = _parse_datetime(getattr(cycle, "created_at", None)) or _parse_datetime(getattr(cycle, "started_at", None))
    decision_created_at = _parse_datetime(getattr(decision, "timestamp", None))
    snapshot_created_at = _parse_datetime(getattr(snapshot, "timestamp", None))
    preview_created_at = _parse_datetime(getattr(preview, "created_at", None))

    cycle_context = getattr(cycle, "cycle_context", None) or {}
    timeline_context = {}
    if isinstance(cycle_context, dict):
        strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
        signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context, dict) else {}
        if isinstance(signal_payload, dict):
            timeline_context = signal_payload.get("timeline") if isinstance(signal_payload.get("timeline"), dict) else {}
        if not timeline_context and isinstance(cycle_context.get("timeline"), dict):
            timeline_context = cycle_context.get("timeline")

    latest_completed_candle_open = _parse_datetime(timeline_context.get("latest_completed_candle_open")) if isinstance(timeline_context, dict) else None
    latest_completed_candle_close = _parse_datetime(timeline_context.get("latest_completed_candle_close")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_open = _parse_datetime(timeline_context.get("oldest_candle_used_open")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_close = _parse_datetime(timeline_context.get("oldest_candle_used_close")) if isinstance(timeline_context, dict) else None
    evaluated_at = _parse_datetime(timeline_context.get("evaluated_at")) or decision_created_at or cycle_created_at or now

    cycle_age_seconds = _seconds_between(now, cycle_created_at)
    decision_age_seconds = _seconds_between(now, decision_created_at)
    snapshot_age_seconds = _seconds_between(now, snapshot_created_at)
    market_data_age_seconds = _seconds_between(now, latest_completed_candle_close)

    history_candle_count = timeline_context.get("history_candle_count") if isinstance(timeline_context, dict) else None
    current_candle_excluded = bool(timeline_context.get("current_incomplete_candle_excluded")) if isinstance(timeline_context, dict) else None
    decision_applies_to = timeline_context.get("decision_applies_to") if isinstance(timeline_context, dict) else None

    mismatch_warning = False
    if cycle_age_seconds is not None and decision_age_seconds is not None:
        if abs(cycle_age_seconds - decision_age_seconds) > 120:
            mismatch_warning = True

    return {
        "evaluated_at": evaluated_at,
        "cycle_created_at": cycle_created_at,
        "decision_created_at": decision_created_at,
        "snapshot_created_at": snapshot_created_at,
        "preview_created_at": preview_created_at,
        "latest_completed_candle_open": latest_completed_candle_open,
        "latest_completed_candle_close": latest_completed_candle_close,
        "oldest_candle_used_open": oldest_candle_used_open,
        "oldest_candle_used_close": oldest_candle_used_close,
        "history_candle_count": history_candle_count,
        "cycle_age_seconds": cycle_age_seconds,
        "decision_age_seconds": decision_age_seconds,
        "snapshot_age_seconds": snapshot_age_seconds,
        "market_data_age_seconds": market_data_age_seconds,
        "current_incomplete_candle_excluded": current_candle_excluded,
        "decision_applies_to": decision_applies_to,
        "age_sources": {
            "cycle_age_seconds": "autonomous_cycle_runs.created_at",
            "decision_age_seconds": "decision_records.timestamp",
            "snapshot_age_seconds": "decision_snapshots.timestamp",
            "market_data_age_seconds": "candles.close_time",
        },
        "timestamp_mismatch_warning": mismatch_warning,
    }


def _build_preview_evidence_payload(
    *,
    command_name: str,
    result: Any,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    evaluation_mode = _preview_command_mode(replayed=bool(getattr(result, "replayed", False)), command_name=command_name)
    command_mode = evaluation_mode
    if command_name == "preview-show":
        command_mode = "VIEW_EXISTING"

    proposed_action = getattr(result, "proposed_action", None) or getattr(cycle, "proposed_action", None) or "HOLD"
    risk_verdict = getattr(result, "risk_verdict", None) or getattr(cycle, "risk_verdict", None)
    deterministic_explanation = list(getattr(result.diagnostics, "deterministic_explanation", []) if getattr(result, "diagnostics", None) else [])
    if not deterministic_explanation and cycle is not None:
        deterministic_explanation = list(getattr(cycle, "deterministic_explanation", []) or [])

    timeline = _build_timeline_payload(
        command_mode=command_mode,
        cycle=cycle,
        decision=decision,
        snapshot=snapshot,
        preview=preview,
    )

    decision_classification = _decision_classification(
        proposed_action=proposed_action,
        risk_verdict=risk_verdict,
        deterministic_explanation=deterministic_explanation,
        failure_reason=getattr(result.diagnostics, "failure_reason", None) if getattr(result, "diagnostics", None) else getattr(cycle, "failure_reason", None),
    )

    capital_state = _capital_state(preview=preview, proposed_action=proposed_action)
    new_evaluation = command_mode == "NEW_PREVIEW"
    outcome = (proposed_action or "FAILED").upper() if command_mode != "VIEW_EXISTING" else (getattr(decision, "outcome", None) or (proposed_action or "FAILED")).upper()

    if command_mode == "VIEW_EXISTING":
        record_created = timeline.get("decision_created_at") or timeline.get("cycle_created_at")
    elif command_mode == "IDEMPOTENT_REPLAY":
        record_created = timeline.get("cycle_created_at")
    else:
        record_created = timeline.get("cycle_created_at") or timeline.get("decision_created_at")

    timeline_warning = bool(timeline.get("timestamp_mismatch_warning"))

    return {
        "command_mode": command_mode,
        "evaluation_mode": evaluation_mode,
        "outcome": outcome,
        "decision_classification": decision_classification,
        "capital_state": capital_state,
        "new_evaluation": new_evaluation,
        "record_created_at": record_created,
        "timeline": timeline,
        "timeline_warning": timeline_warning,
    }


async def execute_preview_cycle(
    *,
    mandate_id: UUID | None,
    actor: str,
    product_id: str,
    strategy_interval: str,
    trigger: str,
    idempotency_seed: str | None,
    software_build_version: str | None,
    forced_action: str | None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        resolved_mandate_id = mandate_id
        if resolved_mandate_id is None:
            resolved_mandate_id = await db.scalar(
                select(AutonomousCapitalMandate.mandate_id)
                .where(AutonomousCapitalMandate.status == "ACTIVE")
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            if resolved_mandate_id is None:
                resolved_mandate_id = await db.scalar(
                    select(AutonomousCapitalMandate.mandate_id)
                    .order_by(desc(AutonomousCapitalMandate.updated_at))
                    .limit(1)
                )
        if resolved_mandate_id is None:
            raise ValueError("No mandate found. Seed or create a mandate before running preview.")

        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=resolved_mandate_id,
                actor=actor,
                product_id=product_id,
                strategy_interval=strategy_interval,
                trigger=trigger,
                idempotency_seed=idempotency_seed,
                software_build_version=software_build_version,
                forced_action=forced_action,
            ),
        )

        cycle = await db.get(AutonomousCycleRun, result.cycle_id)
        decision = await db.get(DecisionRecord, result.decision_record_id) if result.decision_record_id else None
        snapshot = await db.get(DecisionSnapshot, result.decision_record_id) if result.decision_record_id else None
        preview = await db.get(CryptoOrderPreview, result.preview_id) if result.preview_id else None

    payload = {
        "cycle_id": result.cycle_id,
        "state": result.state,
        "idempotency_key": result.idempotency_key,
        "mandate_id": result.mandate_id,
        "mandate_version_id": result.mandate_version_id,
        "proposed_action": result.proposed_action,
        "mandate_verdict": result.mandate_verdict,
        "risk_verdict": result.risk_verdict,
        "decision_record_id": result.decision_record_id,
        "preview_id": result.preview_id,
        "mandate_evaluation_id": result.mandate_evaluation_id,
        "risk_event_id": result.risk_event_id,
        "audit_correlation_id": result.audit_correlation_id,
        "replayed": result.replayed,
        "cycle_context": result.cycle_context,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "diagnostics": {
            "duration_ms": result.diagnostics.duration_ms,
            "evaluation_stage": result.diagnostics.evaluation_stage,
            "termination_stage": result.diagnostics.termination_stage,
            "failure_reason": result.diagnostics.failure_reason,
            "deterministic_explanation": list(result.diagnostics.deterministic_explanation),
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview",
            result=result,
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


def _resolve_git_sha() -> str | None:
    configured_sha = (
        Path(__file__).resolve().parents[4] / ".git" / "HEAD"
    )
    if configured_sha.exists():
        try:
            head_value = configured_sha.read_text(encoding="utf-8").strip()
            if head_value.startswith("ref:"):
                ref_path = head_value.split(":", 1)[1].strip()
                ref_file = configured_sha.parent / ref_path
                if ref_file.exists():
                    return ref_file.read_text(encoding="utf-8").strip()[:12]
            if head_value:
                return head_value[:12]
        except OSError:
            return None
    return None


async def fetch_preview_evidence(*, preview_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        preview = await db.get(CryptoOrderPreview, preview_id)
        if preview is None:
            raise ValueError(f"Preview {preview_id} not found")

        decision: DecisionRecord | None = None
        snapshot: DecisionSnapshot | None = None
        if preview.decision_record_id is not None:
            decision = await db.get(DecisionRecord, preview.decision_record_id)
            snapshot = await db.get(DecisionSnapshot, preview.decision_record_id)

        cycle: AutonomousCycleRun | None = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.preview_id == preview.crypto_order_preview_id)
            .order_by(desc(AutonomousCycleRun.started_at))
            .limit(1)
        )

    payload = {
        "preview": {
            "crypto_order_preview_id": preview.crypto_order_preview_id,
            "status": preview.status,
            "provider": preview.provider,
            "environment": preview.environment,
            "product_id": preview.product_id,
            "side": preview.side,
            "order_type": preview.order_type,
            "requested_amount": _coerce_decimal(preview.requested_amount),
            "requested_amount_currency": preview.requested_amount_currency,
            "quote_size": _coerce_decimal(preview.quote_size),
            "base_size": _coerce_decimal(preview.base_size),
            "estimated_average_price": _coerce_decimal(preview.estimated_average_price),
            "estimated_total_value": _coerce_decimal(preview.estimated_total_value),
            "estimated_base_size": _coerce_decimal(preview.estimated_base_size),
            "estimated_quote_size": _coerce_decimal(preview.estimated_quote_size),
            "estimated_fee": _coerce_decimal(preview.estimated_fee),
            "estimated_fee_currency": preview.estimated_fee_currency,
            "estimated_slippage": _coerce_decimal(preview.estimated_slippage),
            "estimated_commission_total": _coerce_decimal(preview.estimated_commission_total),
            "best_bid": _coerce_decimal(preview.best_bid),
            "best_ask": _coerce_decimal(preview.best_ask),
            "status_reason": preview.failure_reason,
            "warning_messages": list(preview.warning_messages or []),
            "readiness_verdict": preview.readiness_verdict,
            "risk_verdict": preview.risk_verdict,
            "risk_explanation": preview.risk_explanation,
            "decision_record_id": preview.decision_record_id,
            "risk_event_id": preview.risk_event_id,
            "audit_correlation_id": preview.audit_correlation_id,
            "created_at": preview.created_at,
            "updated_at": preview.updated_at,
            "expires_at": preview.expires_at,
        },
        "decision_record": {
            "decision_id": decision.decision_id if decision else None,
            "timeframe": decision.timeframe if decision else None,
            "trade_accepted": decision.trade_accepted if decision else None,
            "trade_rejected_reason": decision.trade_rejected_reason if decision else None,
            "outcome": decision.outcome if decision else None,
            "generated_signals": decision.generated_signals if decision else None,
            "indicators": decision.indicators if decision else None,
            "risk_adjustments": decision.risk_adjustments if decision else None,
            "supporting_strategies": decision.supporting_strategies if decision else None,
            "opposing_strategies": decision.opposing_strategies if decision else None,
            "execution_details": decision.execution_details if decision else None,
        },
        "decision_snapshot": {
            "decision_id": snapshot.decision_id if snapshot else None,
            "strategy_version": snapshot.strategy_version if snapshot else None,
            "configuration_version": snapshot.configuration_version if snapshot else None,
            "decision_engine_version": snapshot.decision_engine_version if snapshot else None,
            "generated_features": snapshot.generated_features if snapshot else None,
            "strategy_inputs": snapshot.strategy_inputs if snapshot else None,
            "risk_inputs": snapshot.risk_inputs if snapshot else None,
        },
        "cycle": {
            "cycle_id": cycle.cycle_id if cycle else None,
            "state": cycle.state if cycle else None,
            "evaluation_stage": cycle.evaluation_stage if cycle else None,
            "termination_stage": cycle.termination_stage if cycle else None,
            "failure_reason": cycle.failure_reason if cycle else None,
            "mandate_id": cycle.mandate_id if cycle else None,
            "mandate_version_id": cycle.mandate_version_id if cycle else None,
            "proposed_action": cycle.proposed_action if cycle else None,
            "risk_verdict": cycle.risk_verdict if cycle else None,
            "started_at": cycle.started_at if cycle else None,
            "completed_at": cycle.completed_at if cycle else None,
            "created_at": cycle.created_at if cycle else None,
            "deterministic_explanation": cycle.deterministic_explanation if cycle else None,
            "cycle_context": cycle.cycle_context if cycle else None,
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview-show",
            result=type("_PreviewResult", (), {"replayed": False, "proposed_action": preview.side, "risk_verdict": preview.risk_verdict, "diagnostics": type("_Diag", (), {"deterministic_explanation": cycle.deterministic_explanation if cycle else [], "failure_reason": cycle.failure_reason if cycle else None})()})(),
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


async def fetch_campaign_orchestration_readiness(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_readiness(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_preview(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=campaign_id, version=version, allow_draft_preview=True)


async def fetch_campaign_orchestration_status(*, campaign_id: UUID, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_status(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_history(*, campaign_id: UUID, version: int | None, limit: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_history(db=db, campaign_id=campaign_id, version=version, limit=limit)


async def fetch_candle_readiness(
    *,
    symbol: str,
    interval: str,
    exchange: str | None,
    max_age_minutes: int,
    lookback_limit: int,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().lower() if exchange else None

    async with AsyncSessionLocal() as db:
        asset_query = select(Asset).where(func.upper(Asset.symbol) == normalized_symbol)
        if normalized_exchange:
            asset_query = asset_query.where(func.lower(Asset.exchange) == normalized_exchange)
        assets = (await db.execute(asset_query.order_by(desc(Asset.created_at)).limit(2))).scalars().all()

        if not assets:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "asset_not_found",
            }

        if len(assets) > 1:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "ambiguous_asset_resolution",
            }

        asset = assets[0]
        latest_candle = await db.scalar(
            select(Candle)
            .where(Candle.asset_id == asset.id, Candle.interval == interval)
            .order_by(desc(Candle.open_time))
            .limit(1)
        )
        row_count = (
            await db.scalar(
                select(func.count())
                .select_from(Candle)
                .where(Candle.asset_id == asset.id, Candle.interval == interval)
            )
            or 0
        )

    if latest_candle is None:
        return {
            "symbol": asset.symbol,
            "exchange": asset.exchange,
            "interval": interval,
            "asset_id": asset.id,
            "row_count": int(row_count),
            "latest_open_time": None,
            "latest_close_time": None,
            "age_minutes": None,
            "ready": False,
            "reason": "no_candles",
        }

    now = datetime.now(timezone.utc)
    close_time = latest_candle.close_time
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - close_time).total_seconds() // 60))
    ready = age_minutes <= max_age_minutes

    return {
        "symbol": asset.symbol,
        "exchange": asset.exchange,
        "interval": interval,
        "asset_id": asset.id,
        "row_count": int(row_count),
        "latest_open_time": latest_candle.open_time,
        "latest_close_time": latest_candle.close_time,
        "age_minutes": age_minutes,
        "ready": ready,
        "reason": "ok" if ready else "stale_candles",
        "max_age_minutes": max_age_minutes,
        "lookback_limit": lookback_limit,
    }


async def fetch_operator_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        await db.execute(select(1))

        if mandate_id is not None:
            mandate: AutonomousCapitalMandate | None = await db.get(AutonomousCapitalMandate, mandate_id)
            if mandate is None:
                raise ValueError(f"Mandate {mandate_id} not found")
            cycle_stmt = (
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.mandate_id == mandate_id)
                .order_by(desc(AutonomousCycleRun.started_at))
                .limit(1)
            )
        else:
            mandate = await db.scalar(
                select(AutonomousCapitalMandate)
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            cycle_stmt = select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1)

        latest_cycle = await db.scalar(cycle_stmt)
        latest_preview = await db.scalar(select(CryptoOrderPreview).order_by(desc(CryptoOrderPreview.created_at)).limit(1))
        connections = (
            await db.execute(select(ExchangeConnection).order_by(ExchangeConnection.provider.asc(), ExchangeConnection.environment.asc()))
        ).scalars().all()
        campaign_count = int((await db.scalar(select(func.count()).select_from(CapitalCampaign))) or 0)
        decision_count = int((await db.scalar(select(func.count()).select_from(DecisionRecord))) or 0)
        open_preview_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(CryptoOrderPreview)
                    .where(CryptoOrderPreview.expires_at > now)
                )
            )
            or 0
        )

        open_live_orders = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(
                        func.lower(LiveCryptoOrder.status).notin_(
                            [
                                "filled",
                                "cancelled",
                                "failed",
                                "rejected",
                                "expired",
                                "settled",
                                "completed",
                            ]
                        )
                    )
                )
            )
            or 0
        )

    candle_summary: dict[str, Any] | None = None
    if candle_symbol:
        candle_summary = await fetch_candle_readiness(
            symbol=candle_symbol,
            interval=candle_interval,
            exchange=candle_exchange,
            max_age_minutes=candle_max_age_minutes,
            lookback_limit=200,
        )

    kraken_production = None
    for item in connections:
        if item.provider == "kraken_spot" and item.environment == "production":
            kraken_production = item
            break

    latest_strategy: dict[str, Any] = {"name": None, "version": None}
    open_positions: int | None = None
    if latest_cycle is not None:
        context = latest_cycle.cycle_context or {}
        strategy = context.get("strategy") if isinstance(context, dict) else None
        reconciliation = context.get("reconciliation_status") if isinstance(context, dict) else None
        if isinstance(strategy, dict):
            latest_strategy = {
                "name": strategy.get("name"),
                "version": strategy.get("version"),
            }
        if isinstance(reconciliation, dict) and isinstance(reconciliation.get("open_position_count"), int):
            open_positions = reconciliation.get("open_position_count")

    latest_signal = latest_cycle.proposed_action if latest_cycle else None
    worker_heartbeat = latest_cycle.completed_at if latest_cycle and latest_cycle.completed_at else None
    if worker_heartbeat is None and latest_cycle is not None:
        worker_heartbeat = latest_cycle.started_at

    system_health = "healthy"
    if kraken_production is not None and kraken_production.status not in {"connected"}:
        system_health = "degraded"
    if candle_summary and not candle_summary.get("ready"):
        system_health = "degraded"

    preview_operator_recommendation = "No action required."
    if latest_cycle is not None:
        action = str(latest_cycle.proposed_action or "").upper()
        state = str(latest_cycle.state or "").upper()
        risk_verdict = str(latest_cycle.risk_verdict or "").upper()
        if state == "FAILED":
            preview_operator_recommendation = "Inspect latest cycle failure before proceeding."
        elif action == "HOLD":
            preview_operator_recommendation = "Waiting for next qualifying BUY."
        elif action in {"BUY", "SELL"} and risk_verdict == "REJECTED":
            preview_operator_recommendation = "Inspect Risk rejection."
        elif action in {"BUY", "SELL"}:
            preview_operator_recommendation = "Review latest preview evidence and approval readiness."

    api_status = "responsive"
    database_status = "connected"
    kraken_status = "Unavailable"
    if kraken_production is not None:
        readiness = kraken_production.last_readiness_verdict or "Unknown"
        kraken_status = f"{kraken_production.status} ({readiness})"

    worker_status = "Unavailable"
    if worker_heartbeat is not None:
        heartbeat_value = worker_heartbeat if worker_heartbeat.tzinfo is not None else worker_heartbeat.replace(tzinfo=timezone.utc)
        age_minutes = int(max(0, (now - heartbeat_value).total_seconds() // 60))
        worker_status = f"heartbeat {age_minutes}m ago"

    git_sha = _resolve_git_sha()

    return {
        "environment": settings.environment,
        "git_sha": git_sha,
        "api_status": api_status,
        "database_status": database_status,
        "worker_status": worker_status,
        "worker_heartbeat": worker_heartbeat,
        "kraken_status": kraken_status,
        "system_health": system_health,
        "database_url_configured": bool(settings.database_url),
        "mandate_id": mandate.mandate_id if mandate else None,
        "mandate_status": mandate.status if mandate else None,
        "latest_strategy": latest_strategy,
        "latest_signal": latest_signal,
        "campaign_count": campaign_count,
        "decision_count": decision_count,
        "open_positions": open_positions,
        "open_previews": open_preview_count,
        "open_live_orders": open_live_orders,
        "research_status": "available" if settings.research_evolution_enabled else "disabled",
        "operator_recommendation": preview_operator_recommendation,
        "safety_flags": {
            "live_crypto_order_submission_enabled": settings.live_crypto_order_submission_enabled,
            "live_crypto_dry_run_enabled": settings.live_crypto_dry_run_enabled,
            "live_crypto_max_order_usd": _coerce_decimal(settings.live_crypto_max_order_usd),
            "live_crypto_preparation_enabled": settings.live_crypto_preparation_enabled,
        },
        "latest_cycle": {
            "cycle_id": latest_cycle.cycle_id if latest_cycle else None,
            "state": latest_cycle.state if latest_cycle else None,
            "proposed_action": latest_cycle.proposed_action if latest_cycle else None,
            "risk_verdict": latest_cycle.risk_verdict if latest_cycle else None,
            "failure_reason": latest_cycle.failure_reason if latest_cycle else None,
            "started_at": latest_cycle.started_at if latest_cycle else None,
            "completed_at": latest_cycle.completed_at if latest_cycle else None,
        },
        "latest_preview": {
            "crypto_order_preview_id": latest_preview.crypto_order_preview_id if latest_preview else None,
            "status": latest_preview.status if latest_preview else None,
            "provider": latest_preview.provider if latest_preview else None,
            "product_id": latest_preview.product_id if latest_preview else None,
            "side": latest_preview.side if latest_preview else None,
            "created_at": latest_preview.created_at if latest_preview else None,
            "expires_at": latest_preview.expires_at if latest_preview else None,
        },
        "connection_summary": [
            {
                "exchange_connection_id": item.exchange_connection_id,
                "provider": item.provider,
                "environment": item.environment,
                "status": item.status,
                "credentials_valid": item.credentials_valid,
                "last_readiness_verdict": item.last_readiness_verdict,
                "last_verified_at": item.last_verified_at,
                "last_heartbeat_at": item.last_heartbeat_at,
            }
            for item in connections
        ],
        "candle_summary": candle_summary,
    }


async def fetch_risk_ledger_diagnosis(*, account_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        account = await db.get(PaperAccount, account_id)
        if account is None:
            raise ValueError(f"Paper account {account_id} not found")

        effective_policy = await resolve_effective_risk_policy(db=db, paper_account_id=account.id)
        latest_trade = await db.scalar(
            select(Trade)
            .where(Trade.paper_account_id == account.id)
            .order_by(desc(Trade.executed_at), desc(Trade.id))
            .limit(1)
        )
        trade_count = int(
            (await db.scalar(select(func.count()).select_from(Trade).where(Trade.paper_account_id == account.id))) or 0
        )

        equity_evidence = await resolve_equity_risk_evidence(
            db=db,
            paper_account=account,
            actor="operator_cli:risk_diagnosis",
            max_price_age_seconds=settings.live_crypto_price_max_age_seconds,
        )

        status_payload: dict[str, Any] | None = None
        status_error: dict[str, Any] | None = None
        try:
            risk_status = await risk_monitor.get_risk_status(db=db, account_id=account.id)
            status_payload = {
                "daily_loss": {
                    "used": format(risk_status.daily_loss.used, "f"),
                    "limit": format(risk_status.daily_loss.limit, "f"),
                    "pct_used": format(risk_status.daily_loss.pct_used, "f"),
                },
                "drawdown": {
                    "used": format(risk_status.drawdown.used, "f"),
                    "limit": format(risk_status.drawdown.limit, "f"),
                    "pct_used": format(risk_status.drawdown.pct_used, "f"),
                },
                "daily_loss_input_source": risk_status.daily_loss_input_source,
                "drawdown_input_source": risk_status.drawdown_input_source,
                "current_equity": format(risk_status.current_equity, "f"),
                "current_cash_balance": format(risk_status.current_cash_balance, "f"),
                "current_position_value": format(risk_status.current_position_value, "f"),
                "start_of_day_equity": format(risk_status.start_of_day_equity, "f"),
                "high_water_mark_equity": format(risk_status.high_water_mark_equity, "f"),
                "valuation_source": risk_status.valuation_source,
                "valuation_state": risk_status.valuation_state,
                "daily_loss_baseline_source": risk_status.daily_loss_baseline_source,
                "drawdown_baseline_source": risk_status.drawdown_baseline_source,
                "baseline_state": risk_status.baseline_state,
                "generated_at": risk_status.generated_at,
            }
        except Exception as exc:  # pragma: no cover - defensive payload branch
            status_error = {
                "error": str(exc),
                "equity_evidence_ready": equity_evidence.ready,
                "equity_evidence_fail_closed_reason": equity_evidence.fail_closed_reason,
            }

        snapshot = await build_account_snapshot(
            db=db,
            paper_account_id=account.id,
            starting_balance=account.starting_balance,
        )

    starting_balance = Decimal(account.starting_balance)
    current_cash_balance = Decimal(account.current_cash_balance)
    old_daily_loss_limit = starting_balance * Decimal(effective_policy.max_daily_loss_pct)
    old_drawdown_limit = starting_balance * Decimal(effective_policy.max_drawdown_pct)
    old_daily_loss_used = max(Decimal("0"), starting_balance - current_cash_balance)
    old_drawdown_used = old_daily_loss_used

    authoritative_start_of_day_equity = equity_evidence.baseline.start_of_day_equity
    authoritative_high_water_mark_equity = equity_evidence.baseline.high_water_mark_equity
    authoritative_current_equity = equity_evidence.valuation.current_equity
    authoritative_daily_loss_used = max(Decimal("0"), authoritative_start_of_day_equity - authoritative_current_equity)
    authoritative_drawdown_used = max(Decimal("0"), authoritative_high_water_mark_equity - authoritative_current_equity)
    authoritative_daily_loss_limit = authoritative_start_of_day_equity * Decimal(effective_policy.max_daily_loss_pct)
    authoritative_drawdown_limit = authoritative_high_water_mark_equity * Decimal(effective_policy.max_drawdown_pct)

    old_daily_loss_pct = old_daily_loss_used / old_daily_loss_limit if old_daily_loss_limit > 0 else Decimal("0")
    old_drawdown_pct = old_drawdown_used / old_drawdown_limit if old_drawdown_limit > 0 else Decimal("0")
    authoritative_daily_loss_pct = authoritative_daily_loss_used / authoritative_daily_loss_limit if authoritative_daily_loss_limit > 0 else Decimal("0")
    authoritative_drawdown_pct = authoritative_drawdown_used / authoritative_drawdown_limit if authoritative_drawdown_limit > 0 else Decimal("0")

    latest_trade_executed_at = None if latest_trade is None else latest_trade.executed_at
    balance_source_timestamp = latest_trade_executed_at or account.created_at
    snapshot_gap_cash = snapshot.cash_balance - current_cash_balance
    snapshot_gap_equity = snapshot.equity - current_cash_balance

    return {
        "account": {
            "account_id": str(account.id),
            "created_at": account.created_at,
            "asset_class": account.asset_class,
            "is_active": bool(account.is_active),
        },
        "evaluation": {
            "generated_at": datetime.now(timezone.utc),
            "policy_source": effective_policy.source,
            "status_input_source": None if status_payload is None else status_payload.get("daily_loss_input_source"),
            "latest_trade_executed_at": latest_trade_executed_at,
            "balance_source_timestamp": balance_source_timestamp,
            "trade_count": trade_count,
        },
        "inputs": {
            "starting_balance": {
                "value": format(starting_balance, "f"),
                "source": "paper_accounts.starting_balance",
                "record_created_at": account.created_at,
            },
            "current_cash_balance": {
                "value": format(current_cash_balance, "f"),
                "source": "paper_accounts.current_cash_balance",
                "record_created_at": account.created_at,
                "latest_trade_executed_at": latest_trade_executed_at,
            },
            "max_daily_loss_pct": {
                "value": format(effective_policy.max_daily_loss_pct, "f"),
                "source": effective_policy.source,
            },
            "max_drawdown_pct": {
                "value": format(effective_policy.max_drawdown_pct, "f"),
                "source": effective_policy.source,
            },
        },
        "formulas": {
            "legacy_cash_only.daily_loss.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.daily_loss.limit": "starting_balance * max_daily_loss_pct",
            "legacy_cash_only.drawdown.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.drawdown.limit": "starting_balance * max_drawdown_pct",
            "authoritative_equity.daily_loss.used": "max(0, start_of_day_equity - current_equity)",
            "authoritative_equity.daily_loss.limit": "start_of_day_equity * max_daily_loss_pct",
            "authoritative_equity.drawdown.used": "max(0, high_water_mark_equity - current_equity)",
            "authoritative_equity.drawdown.limit": "high_water_mark_equity * max_drawdown_pct",
            "pct_used": "used / limit if limit > 0 else 0",
        },
        "status": status_payload,
        "status_error": status_error,
        "equity_evidence": {
            "ready": equity_evidence.ready,
            "fail_closed_reason": equity_evidence.fail_closed_reason,
            "valuation_state": equity_evidence.valuation.valuation_state,
            "valuation_source": equity_evidence.valuation.valuation_source,
            "latest_price_timestamp": equity_evidence.valuation.latest_price_timestamp,
            "stale_cutoff": equity_evidence.valuation.stale_cutoff,
            "missing_price_assets": equity_evidence.valuation.missing_price_assets,
            "stale_price_assets": equity_evidence.valuation.stale_price_assets,
            "price_evidence": equity_evidence.valuation.price_evidence,
            "unresolved_reconciliation_count": equity_evidence.unresolved_reconciliation_count,
            "unknown_provider_order_count": equity_evidence.unknown_provider_order_count,
            "start_of_day_source": equity_evidence.baseline.start_of_day_source,
            "high_water_mark_source": equity_evidence.baseline.high_water_mark_source,
            "baseline_state": equity_evidence.baseline.baseline_state,
        },
        "snapshot": {
            "cash_balance": format(snapshot.cash_balance, "f"),
            "position_value": format(snapshot.position_value, "f"),
            "equity": format(snapshot.equity, "f"),
            "equity_return_usd": format(snapshot.equity_return_usd, "f"),
            "equity_return_pct": format(snapshot.equity_return_pct, "f"),
            "positions": [
                {
                    "asset_id": str(item.asset_id),
                    "symbol": item.symbol,
                    "quantity": format(item.quantity, "f"),
                    "avg_entry_price": format(item.avg_entry_price, "f"),
                    "position_value": format(item.position_value, "f"),
                    "unrealized_pnl_usd": format(item.unrealized_pnl_usd, "f"),
                    "unrealized_pnl_pct": format(item.unrealized_pnl_pct, "f"),
                }
                for item in snapshot.positions
            ],
        },
        "diagnosis": {
            "persisted_cash_balance_minus_computed_cash_balance": format(snapshot_gap_cash, "f"),
            "persisted_cash_balance_minus_snapshot_equity": format(snapshot_gap_equity, "f"),
            "ledger_alignment": "aligned" if snapshot_gap_cash == Decimal("0") else "divergent",
            "legacy_cash_only": {
                "daily_loss": {
                    "used": format(old_daily_loss_used, "f"),
                    "limit": format(old_daily_loss_limit, "f"),
                    "pct_used": format(old_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(old_drawdown_used, "f"),
                    "limit": format(old_drawdown_limit, "f"),
                    "pct_used": format(old_drawdown_pct, "f"),
                },
            },
            "authoritative_equity_based": {
                "daily_loss": {
                    "used": format(authoritative_daily_loss_used, "f"),
                    "limit": format(authoritative_daily_loss_limit, "f"),
                    "pct_used": format(authoritative_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(authoritative_drawdown_used, "f"),
                    "limit": format(authoritative_drawdown_limit, "f"),
                    "pct_used": format(authoritative_drawdown_pct, "f"),
                },
                "current_equity": format(authoritative_current_equity, "f"),
                "current_cash_balance": format(equity_evidence.valuation.cash_balance, "f"),
                "current_position_value": format(equity_evidence.valuation.position_value, "f"),
                "start_of_day_equity": format(authoritative_start_of_day_equity, "f"),
                "high_water_mark_equity": format(authoritative_high_water_mark_equity, "f"),
                "valuation_source": equity_evidence.valuation.valuation_source,
                "valuation_state": equity_evidence.valuation.valuation_state,
                "daily_loss_baseline_source": equity_evidence.baseline.start_of_day_source,
                "drawdown_baseline_source": equity_evidence.baseline.high_water_mark_source,
            },
        },
    }


async def fetch_watch_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    return await fetch_operator_status(
        mandate_id=mandate_id,
        candle_symbol=candle_symbol,
        candle_interval=candle_interval,
        candle_exchange=candle_exchange,
        candle_max_age_minutes=candle_max_age_minutes,
    )


async def fetch_strategy_roster_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_run = await db.scalar(
            select(StrategyRosterRun)
            .where(StrategyRosterRun.provider == provider)
            .where(StrategyRosterRun.product_id == product_id)
            .where(StrategyRosterRun.interval == interval)
            .order_by(desc(StrategyRosterRun.candle_close_time), desc(StrategyRosterRun.created_at))
            .limit(1)
        )

        if latest_run is None:
            return {
                "provider": provider,
                "product_id": product_id,
                "interval": interval,
                "roster_run": None,
                "proposals": [],
            }

        proposals = list(
            (
                await db.execute(
                    select(StrategyRosterProposal)
                    .where(StrategyRosterProposal.roster_run_id == latest_run.roster_run_id)
                    .order_by(StrategyRosterProposal.strategy_slug.asc())
                )
            ).scalars().all()
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "roster_run": {
            "roster_run_id": latest_run.roster_run_id,
            "asset_id": latest_run.asset_id,
            "candle_open_time": latest_run.candle_open_time,
            "candle_close_time": latest_run.candle_close_time,
            "trigger": latest_run.trigger,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "strategies_requested": list(latest_run.strategies_requested or []),
            "strategies_completed": list(latest_run.strategies_completed or []),
            "strategies_failed": list(latest_run.strategies_failed or []),
            "buy_count": latest_run.buy_count,
            "sell_count": latest_run.sell_count,
            "hold_count": latest_run.hold_count,
            "execution_mode": latest_run.execution_mode,
            "live_submission_allowed": latest_run.live_submission_allowed,
            "scheduled_cycle_id": latest_run.scheduled_cycle_id,
        },
        "proposals": [
            {
                "proposal_id": item.proposal_id,
                "strategy_slug": item.strategy_slug,
                "strategy_version": item.strategy_version,
                "strategy_identity": item.strategy_identity,
                "parameter_set_identity": item.parameter_set_identity,
                "action": item.action,
                "evaluation_status": item.evaluation_status,
                "strength": item.strength,
                "confidence": item.confidence,
                "reason": item.reason,
                "deterministic_explanation": list(item.deterministic_explanation or []),
                "indicator_values": item.indicator_values,
                "market_window_evidence": item.market_window_evidence,
                "evaluated_at": item.evaluated_at,
            }
            for item in proposals
        ],
    }
async def fetch_strategy_scorecards_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_outcome_at = await db.scalar(
            select(StrategyRosterProposalOutcome.evaluated_at)
            .where(StrategyRosterProposalOutcome.provider == provider)
            .where(StrategyRosterProposalOutcome.product_id == product_id)
            .where(StrategyRosterProposalOutcome.interval == interval)
            .order_by(desc(StrategyRosterProposalOutcome.evaluated_at))
            .limit(1)
        )
        scorecards = await fetch_strategy_scorecards(
            db=db,
            provider=provider,
            product_id=product_id,
            interval=interval,
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "latest_outcome_evaluated_at": latest_outcome_at,
        "scorecards": [
            {
                "strategy_slug": item.strategy_slug,
                "per_horizon": [
                    {
                        "horizon": bucket.horizon_label,
                        "total_evaluated": bucket.total_evaluated,
                        "buy_evaluations": bucket.buy_evaluations,
                        "buy_correct": bucket.buy_correct,
                        "sell_evaluations": bucket.sell_evaluations,
                        "sell_correct": bucket.sell_correct,
                        "hold_evaluations": bucket.hold_evaluations,
                        "hold_correct": bucket.hold_correct,
                        "overall_correct_pct": bucket.overall_correct_pct,
                        "average_raw_return_pct": bucket.average_raw_return_pct,
                        "average_fee_adjusted_return_pct": bucket.average_fee_adjusted_return_pct,
                        "average_mfe_pct": bucket.average_mfe_pct,
                        "average_mae_pct": bucket.average_mae_pct,
                    }
                    for bucket in item.per_horizon
                ],
                "aggregate": {
                    "horizon": item.aggregate.horizon_label,
                    "total_evaluated": item.aggregate.total_evaluated,
                    "buy_evaluations": item.aggregate.buy_evaluations,
                    "buy_correct": item.aggregate.buy_correct,
                    "sell_evaluations": item.aggregate.sell_evaluations,
                    "sell_correct": item.aggregate.sell_correct,
                    "hold_evaluations": item.aggregate.hold_evaluations,
                    "hold_correct": item.aggregate.hold_correct,
                    "overall_correct_pct": item.aggregate.overall_correct_pct,
                    "average_raw_return_pct": item.aggregate.average_raw_return_pct,
                    "average_fee_adjusted_return_pct": item.aggregate.average_fee_adjusted_return_pct,
                    "average_mfe_pct": item.aggregate.average_mfe_pct,
                    "average_mae_pct": item.aggregate.average_mae_pct,
                },
                "best_regime": item.best_regime,
                "worst_regime": item.worst_regime,
                "regime_evidence_count": item.regime_evidence_count,
                "regime_min_evidence_required": item.regime_min_evidence_required,
            }
            for item in scorecards
        ],
    }


def _serialize_commissioning_run(run: VenueCommissioningRun) -> dict[str, Any]:
    return {
        "commissioning_run_id": run.commissioning_run_id,
        "status": run.status,
        "execution_purpose": run.execution_purpose,
        "commissioning_type": run.commissioning_type,
        "provider": run.provider,
        "environment": run.environment,
        "product_id": run.product_id,
        "max_quote_notional": run.max_quote_notional,
        "max_buys": run.max_buys,
        "max_sells": run.max_sells,
        "hold_minutes": run.hold_minutes,
        "buy_requested_quote_usd": run.buy_requested_quote_usd,
        "buy_client_order_id": run.buy_client_order_id,
        "buy_provider_order_id": run.buy_provider_order_id,
        "buy_submitted_at": run.buy_submitted_at,
        "buy_filled_at": run.buy_filled_at,
        "buy_filled_quote_usd": run.buy_filled_quote_usd,
        "buy_filled_base_btc": run.buy_filled_base_btc,
        "buy_avg_price_usd": run.buy_avg_price_usd,
        "buy_fee_usd": run.buy_fee_usd,
        "hold_started_at": run.hold_started_at,
        "hold_due_at": run.hold_due_at,
        "sell_client_order_id": run.sell_client_order_id,
        "sell_provider_order_id": run.sell_provider_order_id,
        "sell_submitted_at": run.sell_submitted_at,
        "sell_filled_at": run.sell_filled_at,
        "sell_requested_base_btc": run.sell_requested_base_btc,
        "sell_filled_base_btc": run.sell_filled_base_btc,
        "sell_filled_quote_usd": run.sell_filled_quote_usd,
        "sell_avg_price_usd": run.sell_avg_price_usd,
        "sell_fee_usd": run.sell_fee_usd,
        "gross_pnl_usd": run.gross_pnl_usd,
        "total_fees_usd": run.total_fees_usd,
        "net_realized_pnl_usd": run.net_realized_pnl_usd,
        "dust_base_btc": run.dust_base_btc,
        "duplicate_orders_detected": run.duplicate_orders_detected,
        "manual_intervention_required": run.manual_intervention_required,
        "ledger_matches_kraken": run.ledger_matches_kraken,
        "activated_by": run.activated_by,
        "activated_at": run.activated_at,
        "started_by": run.started_by,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "revoked_by": run.revoked_by,
        "revoked_reason": run.revoked_reason,
        "updated_at": run.updated_at,
    }


async def fetch_venue_commission_readiness(
    *,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        readiness = await venue_commissioning_service["evaluate_readiness"](db=db, config=config)

    return {
        "provider": provider,
        "product_id": product_id,
        "environment": environment,
        "amount_usd": amount_usd,
        "hold_minutes": hold_minutes,
        "would_activate_safely": readiness.would_activate_safely,
        "exact_blocker": readiness.exact_blocker,
        "existing_active_run": readiness.existing_active_run,
        "checks": [
            {"label": item.label, "status": item.status, "reason": item.reason}
            for item in readiness.checks
        ],
    }


async def activate_venue_commission_run(
    *,
    actor: str,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
    confirm: bool,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["activate_run"](
            db=db,
            actor=actor,
            config=config,
            confirm=confirm,
        )

    return {
        "activation": "accepted",
        "run": _serialize_commissioning_run(run),
    }


async def start_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["start_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "start": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def fetch_venue_commission_status(*, commissioning_run_id: UUID) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["get_run"](db=db, run_id=commissioning_run_id)

    return {
        "run": _serialize_commissioning_run(run),
    }


async def revoke_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["revoke_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "revoke": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def inspect_canonical_campaign_binding(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_campaign_binding(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def bind_canonical_campaign_runtime(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _bind_canonical_campaign_runtime(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_canonical_campaign_binding_status(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    return await inspect_canonical_campaign_binding(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        actor=actor,
        confirm=confirm,
    )


async def fetch_canonical_campaign_binding_audit(*, campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_canonical_campaign_binding_audit(db=db, campaign_id=campaign_id, limit=limit)


async def canonical_paper_cash_causality_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_canonical_paper_cash_causality_audit(
            db=db,
            request=CanonicalPaperCashCausalityAuditRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
            ),
        )


async def canonical_campaign_authority_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    cycle_id: UUID,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_canonical_campaign_authority_audit(
            db=db,
            request=CanonicalCampaignAuthorityAuditRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                cycle_id=cycle_id,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
            ),
        )


async def create_canonical_preview_package_bundle(
    *,
    campaign_id: UUID,
    campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    max_proposed_order_amount: Decimal,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await create_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageCreateRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
                max_proposed_order_amount=max_proposed_order_amount,
                actor=actor,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
    return payload


async def show_canonical_preview_package_bundle(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_preview_package(db=db, package_id=package_id)


async def canonical_preview_package_readiness(*, package_id: UUID) -> dict[str, Any]:
    payload = await show_canonical_preview_package_bundle(package_id=package_id)
    return {
        "package_id": str(package_id),
        "readiness": payload.get("readiness"),
    }


async def canonical_preview_package_history(
    *,
    campaign_id: UUID,
    campaign_version: int | None,
    limit: int,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await list_canonical_preview_package_history(
            db=db,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            limit=limit,
        )


async def authorize_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    actor: str,
    approver_role: str,
    rationale: str,
    expires_at: datetime,
    max_order_usd: Decimal,
    max_total_deployed_campaign_capital_usd: Decimal,
    no_leverage: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await authorize_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageAuthorizeRequest(
                package_id=package_id,
                actor=actor,
                approver_role=approver_role,
                rationale=rationale,
                expires_at=expires_at,
                max_order_usd=max_order_usd,
                max_total_deployed_campaign_capital_usd=max_total_deployed_campaign_capital_usd,
                no_leverage=no_leverage,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def dry_run_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    operator_identity: str,
    idempotency_token: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await run_dry_run_for_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageDryRunRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                operator_identity=operator_identity,
                idempotency_token=idempotency_token,
            ),
        )
        await db.commit()
        return payload


async def activate_canonical_proving_campaign_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    dry_run_live_crypto_order_id: UUID,
    actor: str,
    expires_at: datetime,
    idempotency_key: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("confirmation required for canonical proving activation")

    async with AsyncSessionLocal() as db:
        payload = await activate_canonical_proving_campaign(
            db=db,
            request=CanonicalPreviewPackageActivationRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                dry_run_live_crypto_order_id=dry_run_live_crypto_order_id,
                actor=actor,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def canonical_proving_activation_status(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_proving_activation_status(db=db, package_id=package_id)


async def pause_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await pause_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackagePauseRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def revoke_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await revoke_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackageRevokeRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def inspect_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def transition_legacy_campaign_to_canonical_successor(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def rollback_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _rollback_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_legacy_campaign_transition_audit(*, legacy_campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_legacy_campaign_transition_audit(db=db, legacy_campaign_id=legacy_campaign_id, limit=limit)


async def canonical_proving_account_transition_preview(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    old_paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_proving_account_transition(
            db=db,
            request=CanonicalProvingAccountTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                old_paper_account_id=old_paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
                idempotency_key=None,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def canonical_proving_account_transition_execute(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    old_paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
    idempotency_key: str,
    expected_evidence_source_id: str | None = None,
    expected_evidence_observed_at: str | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_canonical_proving_account(
            db=db,
            request=CanonicalProvingAccountTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                old_paper_account_id=old_paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
                idempotency_key=idempotency_key,
                expected_evidence_source_id=expected_evidence_source_id,
                expected_evidence_observed_at=expected_evidence_observed_at,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def refresh_provider_balance_evidence(
    *,
    provider: str,
    environment: str,
    actor: str,
) -> dict[str, Any]:
    normalized_provider = provider.strip().lower()
    normalized_environment = environment.strip().lower()

    async with AsyncSessionLocal() as db:
        connection = await db.scalar(
            select(ExchangeConnection)
            .where(ExchangeConnection.provider == normalized_provider)
            .where(ExchangeConnection.environment == normalized_environment)
            .order_by(desc(ExchangeConnection.updated_at), desc(ExchangeConnection.exchange_connection_id))
            .limit(1)
        )
        if connection is None:
            raise LookupError(
                f"exchange connection not found for provider={normalized_provider} environment={normalized_environment}"
            )

        refreshed = await _refresh_exchange_balances(
            db=db,
            exchange_connection_id=connection.exchange_connection_id,
            actor=actor,
        )

        return {
            "provider": refreshed.provider,
            "environment": refreshed.environment,
            "exchange_connection_id": str(refreshed.exchange_connection_id),
            "status": refreshed.status,
            "readiness_verdict": refreshed.readiness.verdict,
            "total_equity_usd": None if refreshed.total_equity_usd is None else format(refreshed.total_equity_usd, "f"),
            "last_successful_sync_at": None
            if refreshed.last_successful_sync_at is None
            else refreshed.last_successful_sync_at.isoformat(),
            "last_verified_at": refreshed.readiness.checked_at.isoformat(),
            "invariants": {
                "no_order_submission": True,
                "sanctioned_refresh_path": "exchange_connections.refresh_exchange_balances",
            },
        }


async def canonical_proving_cap_transition_preview(
    *,
    campaign_id: UUID,
    campaign_version: int,
) -> dict[str, Any]:
    blockers: list[str] = []
    async with AsyncSessionLocal() as db:
        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )
        runtime = await db.scalar(
            select(CapitalCampaign)
            .where(CapitalCampaign.uuid == campaign_id)
            .limit(1)
        )
        if definition is None:
            blockers.append("definition_not_found")
        if runtime is None:
            blockers.append("runtime_campaign_not_found")
        if definition is not None and runtime is not None and runtime.definition_version != definition.version:
            blockers.append("runtime_definition_version_mismatch")
        if definition is not None and Decimal(str(definition.minimum_position_size)) != _PROVING_CAP_TARGET_USD:
            blockers.append("minimum_position_size_must_equal_5")

        active_package_count = 0
        active_activation_count = 0
        open_live_order_count = 0
        unresolved_reconciliation_count = 0
        non_compliant_activation_count = 0
        if definition is not None:
            active_package_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalPreviewPackage)
                    .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                    .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                    .where(CanonicalPreviewPackage.package_state.notin_(sorted(_TERMINAL_PACKAGE_STATES)))
                )
                or 0
            )
            non_compliant_activation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalProvingActivation)
                    .where(CanonicalProvingActivation.campaign_id == campaign_id)
                    .where(CanonicalProvingActivation.campaign_version == campaign_version)
                    .where(CanonicalProvingActivation.no_leverage.is_(False))
                )
                or 0
            )
        if definition is not None and runtime is not None:
            runtime_provider, runtime_environment = _runtime_exchange_scope(getattr(runtime, "exchange", None))
            active_activation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalProvingActivation)
                    .where(CanonicalProvingActivation.campaign_id == campaign_id)
                    .where(CanonicalProvingActivation.campaign_version == campaign_version)
                    .where(CanonicalProvingActivation.activation_state.notin_(sorted(_TERMINAL_ACTIVATION_STATES)))
                )
                or 0
            )
            open_live_order_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == runtime_provider if runtime_provider is not None else True)
                    .where(LiveCryptoOrder.environment == runtime_environment if runtime_environment is not None else True)
                    .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                )
                or 0
            )
            unresolved_reconciliation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.capital_campaign_id == runtime.id)
                    .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                )
                or 0
            )

        if active_package_count > 0:
            blockers.append("no_active_canonical_package")
        if active_activation_count > 0:
            blockers.append("no_active_proving_activation")
        if non_compliant_activation_count > 0:
            blockers.append("no_leverage_boundary_violated")
        if definition is not None and Decimal(str(definition.deployed_capital)) > Decimal("0"):
            blockers.append("no_deployed_capital")
        if open_live_order_count > 0:
            blockers.append("no_open_live_orders")
        if unresolved_reconciliation_count > 0:
            blockers.append("no_unresolved_reconciliation_state")

        before = {
            "maximum_position_size": None if definition is None else format(Decimal(str(definition.maximum_position_size)), "f"),
            "maximum_total_exposure": None if definition is None else format(Decimal(str(definition.maximum_total_exposure)), "f"),
            "minimum_position_size": None if definition is None else format(Decimal(str(definition.minimum_position_size)), "f"),
            "maximum_open_positions": None if definition is None else int(definition.maximum_open_positions),
            "deployed_capital": None if definition is None else format(Decimal(str(definition.deployed_capital)), "f"),
        }
        after = {
            "maximum_open_positions": 1,
            "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
            "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
        }
        already_exact = (
            definition is not None
            and int(definition.maximum_open_positions) == 1
            and Decimal(str(definition.maximum_position_size)) == _PROVING_CAP_TARGET_USD
            and Decimal(str(definition.maximum_total_exposure)) == _PROVING_CAP_TARGET_USD
        )

        return {
            "ready": len(blockers) == 0,
            "blockers": blockers,
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "before": before,
            "proposed": after,
            "already_exact": already_exact,
            "invariants": {
                "exact_proving_cap_usd": format(_PROVING_CAP_TARGET_USD, "f"),
                "no_order_submission": True,
                "active_package_count": active_package_count,
                "active_activation_count": active_activation_count,
                "open_live_order_count": open_live_order_count,
                "unresolved_reconciliation_count": unresolved_reconciliation_count,
                "non_compliant_activation_count": non_compliant_activation_count,
            },
        }


async def canonical_proving_cap_transition_execute(
    *,
    campaign_id: UUID,
    campaign_version: int,
    actor: str,
    confirm: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("confirm=true is required")
    if not idempotency_key.strip():
        raise PermissionError("idempotency_key is required")

    async with AsyncSessionLocal() as db:
        async with db.begin():
            definition = await db.scalar(
                select(CapitalCampaignDefinition)
                .where(CapitalCampaignDefinition.campaign_id == campaign_id)
                .where(CapitalCampaignDefinition.version == campaign_version)
                .with_for_update()
                .limit(1)
            )
            runtime = await db.scalar(
                select(CapitalCampaign)
                .where(CapitalCampaign.uuid == campaign_id)
                .with_for_update()
                .limit(1)
            )
            blockers: list[str] = []
            if definition is None:
                blockers.append("definition_not_found")
            if runtime is None:
                blockers.append("runtime_campaign_not_found")
            if definition is not None and runtime is not None and runtime.definition_version != definition.version:
                blockers.append("runtime_definition_version_mismatch")
            if definition is not None and Decimal(str(definition.minimum_position_size)) != _PROVING_CAP_TARGET_USD:
                blockers.append("minimum_position_size_must_equal_5")

            active_package_count = 0
            active_activation_count = 0
            open_live_order_count = 0
            unresolved_reconciliation_count = 0
            non_compliant_activation_count = 0
            if definition is not None:
                active_package_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalPreviewPackage)
                        .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                        .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                        .where(CanonicalPreviewPackage.package_state.notin_(sorted(_TERMINAL_PACKAGE_STATES)))
                    )
                    or 0
                )
                non_compliant_activation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalProvingActivation)
                        .where(CanonicalProvingActivation.campaign_id == campaign_id)
                        .where(CanonicalProvingActivation.campaign_version == campaign_version)
                        .where(CanonicalProvingActivation.no_leverage.is_(False))
                    )
                    or 0
                )
            if definition is not None and runtime is not None:
                runtime_provider, runtime_environment = _runtime_exchange_scope(getattr(runtime, "exchange", None))
                active_activation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalProvingActivation)
                        .where(CanonicalProvingActivation.campaign_id == campaign_id)
                        .where(CanonicalProvingActivation.campaign_version == campaign_version)
                        .where(CanonicalProvingActivation.activation_state.notin_(sorted(_TERMINAL_ACTIVATION_STATES)))
                    )
                    or 0
                )
                open_live_order_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(LiveCryptoOrder)
                        .where(LiveCryptoOrder.provider == runtime_provider if runtime_provider is not None else True)
                        .where(LiveCryptoOrder.environment == runtime_environment if runtime_environment is not None else True)
                        .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                    )
                    or 0
                )
                unresolved_reconciliation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(LiveReconciliationEvent)
                        .where(LiveReconciliationEvent.capital_campaign_id == runtime.id)
                        .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                    )
                    or 0
                )

            if active_package_count > 0:
                blockers.append("no_active_canonical_package")
            if active_activation_count > 0:
                blockers.append("no_active_proving_activation")
            if non_compliant_activation_count > 0:
                blockers.append("no_leverage_boundary_violated")
            if definition is not None and Decimal(str(definition.deployed_capital)) > Decimal("0"):
                blockers.append("no_deployed_capital")
            if open_live_order_count > 0:
                blockers.append("no_open_live_orders")
            if unresolved_reconciliation_count > 0:
                blockers.append("no_unresolved_reconciliation_state")

            before_preview = {
                "maximum_position_size": None if definition is None else format(Decimal(str(definition.maximum_position_size)), "f"),
                "maximum_total_exposure": None if definition is None else format(Decimal(str(definition.maximum_total_exposure)), "f"),
                "minimum_position_size": None if definition is None else format(Decimal(str(definition.minimum_position_size)), "f"),
                "maximum_open_positions": None if definition is None else int(definition.maximum_open_positions),
                "deployed_capital": None if definition is None else format(Decimal(str(definition.deployed_capital)), "f"),
            }
            proposed_preview = {
                "maximum_open_positions": 1,
                "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
                "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
            }
            preview = {
                "ready": len(blockers) == 0,
                "blockers": blockers,
                "before": before_preview,
                "proposed": proposed_preview,
            }
            if not preview["ready"]:
                raise PermissionError("proving cap transition prerequisites failed: " + ", ".join(preview["blockers"]))
            if definition is None:
                raise LookupError("campaign definition not found")

            latest_audit = await db.scalar(
                select(AuditLog)
                .where(AuditLog.entity_type == "capital_campaign")
                .where(AuditLog.entity_id == campaign_id)
                .where(AuditLog.action == "capital_campaign.proving_cap_transition")
                .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
                .with_for_update()
                .limit(1)
            )

            if latest_audit is not None and isinstance(latest_audit.after_state, dict):
                prior_key = str(latest_audit.after_state.get("idempotency_key") or "")
                prior_cap = str(latest_audit.after_state.get("maximum_position_size") or "")
                if prior_key == idempotency_key and prior_cap == format(_PROVING_CAP_TARGET_USD, "f"):
                    return {
                        "changed": False,
                        "idempotent": True,
                        "audit_created": False,
                        "campaign_id": str(campaign_id),
                        "campaign_version": campaign_version,
                        "before": preview["before"],
                        "after": preview["proposed"],
                    }
                if prior_key and prior_key != idempotency_key:
                    raise PermissionError("conflicting retry blocked: proving cap transition already executed")

            before = dict(preview["before"])
            definition.maximum_open_positions = 1
            definition.maximum_position_size = _PROVING_CAP_TARGET_USD
            definition.maximum_total_exposure = _PROVING_CAP_TARGET_USD
            definition.updated_at = datetime.now(timezone.utc)

            after = {
                "maximum_open_positions": 1,
                "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
                "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
                "idempotency_key": idempotency_key,
                "runtime_campaign_id": None if runtime is None else runtime.id,
            }
            db.add(
                AuditLog(
                    actor=actor,
                    action="capital_campaign.proving_cap_transition",
                    entity_type="capital_campaign",
                    entity_id=campaign_id,
                    before_state=before,
                    after_state=after,
                )
            )

        return {
            "changed": True,
            "idempotent": False,
            "audit_created": True,
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "before": before,
            "after": after,
        }
