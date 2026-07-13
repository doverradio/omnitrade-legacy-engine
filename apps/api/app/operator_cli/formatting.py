from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, default=json_default)


def _fmt(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    if isinstance(value, datetime):
        return json_default(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def render_preview_text(payload: dict[str, Any]) -> str:
    diagnostics = payload.get("diagnostics") or {}
    lines = [
        "Autonomous Preview",
        f"Cycle ID: {_fmt(payload.get('cycle_id'))}",
        f"State: {_fmt(payload.get('state'))}",
        f"Proposed action: {_fmt(payload.get('proposed_action'))}",
        f"Mandate verdict: {_fmt(payload.get('mandate_verdict'))}",
        f"Risk verdict: {_fmt(payload.get('risk_verdict'))}",
        f"Preview ID: {_fmt(payload.get('preview_id'), default='none')}",
        f"Decision record ID: {_fmt(payload.get('decision_record_id'), default='none')}",
        f"Replayed: {_fmt(payload.get('replayed'))}",
        f"Evaluation stage: {_fmt(diagnostics.get('evaluation_stage'))}",
        f"Termination stage: {_fmt(diagnostics.get('termination_stage'))}",
        f"Failure reason: {_fmt(diagnostics.get('failure_reason'), default='none')}",
        "Safety: preview-only path, no live order submission is performed by this command.",
    ]
    explanation = diagnostics.get("deterministic_explanation") or []
    if explanation:
        lines.append("Deterministic explanation:")
        for item in explanation:
            lines.append(f"- {item}")
    return "\n".join(lines)


def render_preview_show_text(payload: dict[str, Any]) -> str:
    preview = payload.get("preview") or {}
    decision = payload.get("decision_record") or {}
    snapshot = payload.get("decision_snapshot") or {}
    cycle = payload.get("cycle") or {}
    lines = [
        "Preview Evidence",
        f"Preview ID: {_fmt(preview.get('crypto_order_preview_id'))}",
        f"Status: {_fmt(preview.get('status'))}",
        f"Provider: {_fmt(preview.get('provider'))}",
        f"Environment: {_fmt(preview.get('environment'))}",
        f"Product: {_fmt(preview.get('product_id'))}",
        f"Side: {_fmt(preview.get('side'))}",
        f"Requested amount: {_fmt(preview.get('requested_amount'))} {_fmt(preview.get('requested_amount_currency'))}",
        f"Estimated average price: {_fmt(preview.get('estimated_average_price'), default='n/a')}",
        f"Estimated fee: {_fmt(preview.get('estimated_fee'), default='n/a')} {_fmt(preview.get('estimated_fee_currency'), default='')}",
        f"Readiness verdict: {_fmt(preview.get('readiness_verdict'), default='n/a')}",
        f"Risk verdict: {_fmt(preview.get('risk_verdict'), default='n/a')}",
        f"Decision ID: {_fmt(decision.get('decision_id'), default='none')}",
        f"Trade accepted: {_fmt(decision.get('trade_accepted'), default='n/a')}",
        f"Decision outcome: {_fmt(decision.get('outcome'), default='n/a')}",
        f"Decision timeframe: {_fmt(decision.get('timeframe'), default='n/a')}",
        f"Snapshot strategy version: {_fmt(snapshot.get('strategy_version'), default='n/a')}",
        f"Snapshot config version: {_fmt(snapshot.get('configuration_version'), default='n/a')}",
        f"Cycle ID: {_fmt(cycle.get('cycle_id'), default='none')}",
        f"Cycle state: {_fmt(cycle.get('state'), default='n/a')}",
        "Safety: read-only evidence view; no state mutation and no submission path.",
    ]

    warning_messages = preview.get("warning_messages") or []
    if warning_messages:
        lines.append("Warnings:")
        for message in warning_messages:
            lines.append(f"- {message}")

    strategy_inputs = snapshot.get("strategy_inputs") or {}
    signal_reason = strategy_inputs.get("signal_reason")
    if signal_reason:
        lines.append(f"Signal reason: {_fmt(signal_reason)}")

    return "\n".join(lines)


def render_candles_text(payload: dict[str, Any]) -> str:
    lines = [
        "Candle Readiness",
        f"Symbol: {_fmt(payload.get('symbol'))}",
        f"Exchange: {_fmt(payload.get('exchange'))}",
        f"Interval: {_fmt(payload.get('interval'))}",
        f"Asset ID: {_fmt(payload.get('asset_id'))}",
        f"Latest open: {_fmt(payload.get('latest_open_time'), default='none')}",
        f"Latest close: {_fmt(payload.get('latest_close_time'), default='none')}",
        f"Rows in lookback: {_fmt(payload.get('row_count'))}",
        f"Age minutes: {_fmt(payload.get('age_minutes'))}",
        f"Ready: {_fmt(payload.get('ready'))}",
        f"Reason: {_fmt(payload.get('reason'))}",
        "Safety: read-only candle inspection.",
    ]
    return "\n".join(lines)


def render_status_text(payload: dict[str, Any]) -> str:
    latest_cycle = payload.get("latest_cycle") or {}
    latest_preview = payload.get("latest_preview") or {}
    safety_flags = payload.get("safety_flags") or {}
    lines = [
        "Operator Status",
        f"Environment: {_fmt(payload.get('environment'))}",
        f"DB URL configured: {_fmt(payload.get('database_url_configured'))}",
        f"Live submission enabled: {_fmt(safety_flags.get('live_crypto_order_submission_enabled'))}",
        f"Dry run enabled: {_fmt(safety_flags.get('live_crypto_dry_run_enabled'))}",
        f"Max live order USD: {_fmt(safety_flags.get('live_crypto_max_order_usd'))}",
        f"Mandate ID: {_fmt(payload.get('mandate_id'), default='none')}",
        f"Latest cycle ID: {_fmt(latest_cycle.get('cycle_id'), default='none')}",
        f"Latest cycle state: {_fmt(latest_cycle.get('state'), default='none')}",
        f"Latest cycle action: {_fmt(latest_cycle.get('proposed_action'), default='none')}",
        f"Latest preview ID: {_fmt(latest_preview.get('crypto_order_preview_id'), default='none')}",
        f"Latest preview status: {_fmt(latest_preview.get('status'), default='none')}",
        "Safety: status is read-only and does not invoke order submission.",
    ]

    connection_summary = payload.get("connection_summary") or []
    if connection_summary:
        lines.append("Exchange connections:")
        for row in connection_summary:
            lines.append(
                f"- {row.get('provider')} {row.get('environment')} status={row.get('status')} readiness={row.get('last_readiness_verdict')}"
            )

    candle_summary = payload.get("candle_summary")
    if candle_summary:
        lines.append("Candle summary:")
        lines.append(
            f"- {candle_summary.get('symbol')} {candle_summary.get('interval')} ready={candle_summary.get('ready')} age_minutes={candle_summary.get('age_minutes')}"
        )

    return "\n".join(lines)
