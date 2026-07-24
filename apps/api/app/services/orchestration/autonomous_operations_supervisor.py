from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


STAGE_STALL_THRESHOLDS = {
    "PACKAGE_READY": timedelta(minutes=10),
    "PACKAGE_AUTHORIZED": timedelta(minutes=5),
    "PACKAGE_ACTIVATED": timedelta(minutes=10),
    "BUY_SUBMITTED": timedelta(minutes=15),
    "POSITION_OPEN": timedelta(hours=24),
    "SELL_SUBMITTED": timedelta(minutes=15),
    "RECONCILIATION_PENDING": timedelta(minutes=15),
}


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _id(value: Any, name: str) -> str | None:
    item = None if value is None else getattr(value, name, None)
    return None if item is None else str(item)


def resolve_autonomous_profit_snapshot(evidence: dict[str, Any]) -> dict[str, Any]:
    """Resolve operational state from read-only, canonical evidence.

    This function is deliberately pure. Callers own evidence collection; resolving
    or repeatedly reporting a snapshot cannot mutate trading state.
    """
    now = evidence.get("now") or datetime.now(timezone.utc)
    cycle = evidence.get("cycle")
    package = evidence.get("package")
    activation = evidence.get("activation")
    order = evidence.get("order")
    position = evidence.get("position")
    reconciliation = evidence.get("reconciliation")
    readiness = evidence.get("readiness") or {}
    action = str(getattr(cycle, "proposed_action", "") or "").upper()
    failure = str(getattr(cycle, "failure_reason", "") or "").strip()
    package_state = str(getattr(package, "package_state", "") or "").upper()
    order_side = str(getattr(order, "side", "") or "").upper()
    order_status = str(getattr(order, "status", "") or "").upper()
    reconciliation_status = str(getattr(reconciliation, "reconciliation_status", "") or "").lower()
    position_open = bool(evidence.get("position_open"))
    net_profit_raw = evidence.get("net_profit")
    net_profit = None if net_profit_raw is None else Decimal(str(net_profit_raw))
    buy_reconciled = bool(evidence.get("buy_reconciled"))
    sell_reconciled = bool(evidence.get("sell_reconciled"))
    autonomous_provenance = bool(evidence.get("autonomous_buy_provenance")) and bool(evidence.get("autonomous_sell_provenance"))

    if cycle is None:
        stage = "NO_CAMPAIGN"
        progress_at = evidence.get("campaign_updated_at")
    else:
        stage = "STRATEGIES_COMPLETED"
        progress_at = getattr(cycle, "completed_at", None) or getattr(cycle, "updated_at", None)
    if getattr(cycle, "decision_record_id", None):
        stage = "EVIDENCE_PERSISTED"
    if getattr(cycle, "mandate_evaluation_id", None):
        stage = "MANDATE_EVALUATED"
    if package is not None:
        stage = {
            "AUTHORIZED": "PACKAGE_AUTHORIZED",
            "DRY_RUN_PASSED": "PACKAGE_AUTHORIZED",
            "ACTIVATED": "PACKAGE_ACTIVATED",
        }.get(package_state, "PACKAGE_READY")
        progress_at = getattr(package, "updated_at", None) or getattr(package, "generated_at", None)
    if activation is not None:
        stage = "PACKAGE_ACTIVATED"
        progress_at = getattr(activation, "activated_at", None)
    if order is not None and getattr(order, "submitted_at", None) is not None:
        stage = "BUY_SUBMITTED" if order_side == "BUY" else "SELL_SUBMITTED"
        progress_at = getattr(order, "submitted_at", None)
    if position_open:
        stage = "POSITION_OPEN"
        progress_at = evidence.get("position_updated_at") or progress_at
    if reconciliation is not None and reconciliation_status not in {"filled", "canceled", "rejected"}:
        stage = "RECONCILIATION_PENDING"
        progress_at = getattr(reconciliation, "recorded_at", None)
    if sell_reconciled:
        stage = "RECONCILED"
        progress_at = getattr(reconciliation, "recorded_at", None) or progress_at
    profit_complete = autonomous_provenance and buy_reconciled and sell_reconciled and net_profit is not None and net_profit > 0
    if profit_complete:
        stage = "NET_PROFIT_CONFIRMED"

    reason_codes: list[str] = []
    if failure:
        lower = failure.lower()
        if "scorecard" in lower and "timeout" in lower:
            reason_codes.append("scorecard_fetch_timeout")
        elif "session" in lower or "database" in lower:
            reason_codes.append("database_session_unrecoverable")
        elif "risk" in lower:
            reason_codes.append("risk_rejected")
        else:
            reason_codes.append(failure)
    readiness_codes = readiness.get("reason_codes") or []
    for item in readiness_codes:
        code = item.get("code") if isinstance(item, dict) else item
        if code and code not in {"no_package_available"} and str(code) not in reason_codes:
            reason_codes.append(str(code))

    automatic_activation = bool(evidence.get("automatic_activation_enabled"))
    live_submission = bool(evidence.get("live_submission_enabled"))
    provider_reachable = live_submission and bool(evidence.get("provider_available", True))
    if package is not None and package_state in {"READY", "AUTHORIZED", "DRY_RUN_PASSED"} and not automatic_activation:
        reason_codes.append("automatic_activation_disabled")
    if stage in {"PACKAGE_ACTIVATED", "BUY_SUBMITTED", "POSITION_OPEN", "SELL_SUBMITTED", "RECONCILIATION_PENDING"} and not live_submission:
        reason_codes.append("live_submission_disabled")

    stall_duration = None if progress_at is None else max(timedelta(0), now - progress_at.astimezone(timezone.utc))
    threshold = STAGE_STALL_THRESHOLDS.get(stage)
    stalled = action != "HOLD" and threshold is not None and stall_duration is not None and stall_duration > threshold
    if stalled:
        reason_codes.append("pipeline_stalled")

    hard_blockers = [code for code in reason_codes if code not in {"automatic_activation_disabled", "live_submission_disabled"}]
    if profit_complete:
        overall = "FIRST_AUTONOMOUS_PROFIT_COMPLETE"
    elif hard_blockers:
        overall = "STALLED" if stalled and hard_blockers == ["pipeline_stalled"] else "BLOCKED"
    elif "automatic_activation_disabled" in reason_codes or "live_submission_disabled" in reason_codes:
        overall = "SAFETY_DISABLED"
    elif action == "HOLD" and package is None and not failure:
        overall = "HEALTHY_WAITING"
        reason_codes = ["healthy_hold", "no_actionable_signal"]
    else:
        overall = "PIPELINE_PROGRESSING"

    human_action = overall in {"BLOCKED", "STALLED", "SAFETY_DISABLED"}
    recommendation = {
        "HEALTHY_WAITING": "No action. Wait for the next autonomous cycle.",
        "PIPELINE_PROGRESSING": "No action. Continue observing canonical lifecycle evidence.",
        "SAFETY_DISABLED": "Review the named safety boundary; do not bypass it.",
        "STALLED": "Inspect the first stalled lifecycle boundary and its canonical audit evidence.",
        "BLOCKED": "Inspect the first reason code and its canonical audit evidence.",
        "FIRST_AUTONOMOUS_PROFIT_COMPLETE": "Preserve the completed reconciliation and profit evidence.",
    }[overall]

    return {
        "generated_at": _iso(now),
        "environment": evidence.get("environment"),
        "provider": evidence.get("provider"),
        "product": evidence.get("product"),
        "campaign_id": _id(package, "campaign_id") or _id(cycle, "capital_campaign_id"),
        "campaign_version": getattr(package, "campaign_version", None) or getattr(cycle, "capital_campaign_version", None),
        "mandate_id": _id(package, "mandate_id") or _id(cycle, "mandate_id"),
        "mandate_version_id": _id(package, "mandate_version_id") or _id(cycle, "mandate_version_id"),
        "latest_autonomous_cycle_id": _id(cycle, "cycle_id"),
        "latest_orchestration_cycle_id": _id(cycle, "cycle_id"),
        "latest_decision_record_id": _id(package, "decision_record_id") or _id(cycle, "decision_record_id"),
        "latest_package_id": _id(package, "package_id"),
        "latest_package_state": package_state or None,
        "latest_activation_id": _id(activation, "activation_id"),
        "latest_order_id": _id(order, "live_crypto_order_id"),
        "latest_position_id": _id(position, "id"),
        "latest_reconciliation_id": _id(reconciliation, "id"),
        "latest_net_profit": None if net_profit is None else format(net_profit, "f"),
        "latest_decision": action or None,
        "current_stage": stage,
        "overall_status": overall,
        "healthy": overall in {"HEALTHY_WAITING", "PIPELINE_PROGRESSING", "FIRST_AUTONOMOUS_PROFIT_COMPLETE"},
        "human_action_required": human_action,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "recommended_action": recommendation,
        "last_successful_full_pipeline_at": _iso(evidence.get("last_successful_full_pipeline_at")),
        "last_progress_at": _iso(progress_at),
        "stalled": stalled,
        "stall_duration": None if stall_duration is None else int(stall_duration.total_seconds()),
        "stall_threshold_seconds": None if threshold is None else int(threshold.total_seconds()),
        "live_submission_enabled": live_submission,
        "automatic_activation_enabled": automatic_activation,
        "provider_submission_reachable": provider_reachable,
        "read_only": True,
    }
