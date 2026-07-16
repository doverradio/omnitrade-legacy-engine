from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID


class RenderOptions:
    def __init__(self, *, color_enabled: bool, unicode_enabled: bool, verbose: bool) -> None:
        self.color_enabled = color_enabled
        self.unicode_enabled = unicode_enabled
        self.verbose = verbose


class _Palette:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def resolve_render_options(*, no_color: bool, verbose: bool) -> RenderOptions:
    no_color_env = os.environ.get("NO_COLOR", "").strip() == "1"
    term = os.environ.get("TERM", "")
    color_enabled = not (no_color or no_color_env or term == "dumb")

    encoding = (getattr(getattr(os, "sys", None), "stdout", None) and os.sys.stdout.encoding) or ""
    unicode_enabled = any(token in encoding.lower() for token in ("utf", "ucs"))
    return RenderOptions(color_enabled=color_enabled, unicode_enabled=unicode_enabled, verbose=verbose)


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


def _style(text: str, tone: str, options: RenderOptions) -> str:
    if not options.color_enabled:
        return text
    color_map = {
        "good": _Palette.GREEN,
        "bad": _Palette.RED,
        "warn": _Palette.YELLOW,
        "info": _Palette.CYAN,
        "muted": _Palette.GRAY,
        "title": _Palette.BOLD + _Palette.BLUE,
    }
    prefix = color_map.get(tone, "")
    if not prefix:
        return text
    return f"{prefix}{text}{_Palette.RESET}"


def _state_tone(value: str | None) -> str:
    normalized = (value or "").upper()
    if normalized in {"COMPLETE", "PREVIEW_READY", "READY", "AUTHORIZED", "ACCEPTED"}:
        return "good"
    if normalized in {"FAILED", "REJECTED", "ERROR", "DISCONNECTED"}:
        return "bad"
    if normalized in {"HOLD", "WARNING", "RESIZED", "YELLOW"}:
        return "warn"
    return "info"


def _badge(action: str | None, *, replayed: bool, options: RenderOptions) -> str:
    if replayed:
        return "[REPLAY]" if not options.unicode_enabled else "⚪ REPLAY"

    normalized = (action or "").upper()
    if normalized == "BUY":
        label = "[BUY]" if not options.unicode_enabled else "🟢 BUY"
        return _style(label, "good", options)
    if normalized == "SELL":
        label = "[SELL]" if not options.unicode_enabled else "🔴 SELL"
        return _style(label, "bad", options)
    if normalized == "HOLD":
        label = "[HOLD]" if not options.unicode_enabled else "🟡 HOLD"
        return _style(label, "warn", options)
    if normalized == "FAILED":
        label = "[FAILED]" if not options.unicode_enabled else "⚠ FAILED"
        return _style(label, "bad", options)
    return normalized or "UNKNOWN"


def _frame_header(title: str, options: RenderOptions) -> list[str]:
    if options.unicode_enabled:
        bar = "═" * 39
    else:
        bar = "=" * 39
    return [
        _style(bar, "title", options),
        _style(f" {title}", "title", options),
        _style(bar, "title", options),
        "",
    ]


def _section(title: str, rows: list[tuple[str, str]], options: RenderOptions) -> list[str]:
    line = "─" * 28 if options.unicode_enabled else "-" * 28
    out = [title, line]
    for label, value in rows:
        out.append(f"{label:<18} {value}")
    out.append("")
    return out


def _humanize_reason(code: str) -> str:
    normalized = code.strip()
    mapping = {
        "CHECK_PASSED:strategy_evaluated": "The approved strategy executed successfully.",
        "CHECK_INFO:signal_action=hold": "The strategy recommends HOLD.",
        "CHECK_INFO:signal_action=buy": "The strategy recommends BUY.",
        "CHECK_INFO:signal_action=sell": "The strategy recommends SELL.",
        "CHECK_FAILED:insufficient_candle_context": "Not enough historical market data.",
        "CHECK_FAILED:provider_not_ready": "Exchange readiness is not currently healthy.",
        "CHECK_FAILED:reconciliation_not_ready": "Reconciliation evidence is incomplete or stale.",
        "CHECK_FAILED:mandate_not_active": "The mandate is not active.",
        "CHECK_FAILED:exchange_connection_not_found": "Exchange connection evidence is missing.",
        "CHECK_FAILED:no_approved_strategy_active": "No approved strategy is currently active.",
        "CHECK_FAILED:asset_not_found_for_strategy": "No matching asset was found for strategy evaluation.",
        "CHECK_FAILED:ambiguous_asset_resolution_for_strategy": "Asset resolution is ambiguous.",
    }
    return mapping.get(normalized, normalized)


def _duration_seconds(payload: dict[str, Any]) -> str:
    diagnostics = payload.get("diagnostics") or {}
    value = diagnostics.get("duration_ms")
    if value is None:
        return "Unavailable"
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return "Unavailable"
    return f"{seconds:.2f} seconds"


def _minutes_age(ts: Any) -> int | None:
    if not isinstance(ts, datetime):
        return None
    value = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - value.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds() // 60))


def _classify_hold(payload: dict[str, Any]) -> str:
    action = str(payload.get("proposed_action") or "").upper()
    if action != "HOLD":
        return "Not a HOLD outcome"
    diagnostics = payload.get("diagnostics") or {}
    reasons = diagnostics.get("deterministic_explanation") or []
    joined = " ".join(str(item) for item in reasons)
    if "strategy_evaluated" in joined or "signal_action=hold" in joined:
        return "Strategy-derived"
    return "Infrastructure-related"


def _operator_recommendation(payload: dict[str, Any]) -> str:
    state = str(payload.get("state") or "").upper()
    action = str(payload.get("proposed_action") or "").upper()
    risk_verdict = str(payload.get("risk_verdict") or "").upper()
    preview_id = payload.get("preview_id")

    if state == "FAILED":
        return "Inspect failure details and infrastructure readiness."
    if action == "HOLD":
        hold_type = _classify_hold(payload)
        if hold_type == "Strategy-derived":
            return "Waiting for next qualifying BUY or SELL signal."
        return "Inspect readiness and reconciliation before the next cycle."
    if action in {"BUY", "SELL"} and risk_verdict in {"ACCEPTED", "RESIZED"} and preview_id:
        return "Review preview details and continue operator approval workflow."
    if action in {"BUY", "SELL"} and risk_verdict == "REJECTED":
        return "Inspect Risk rejection before retrying."
    return "No action required."


def _next_evaluation(payload: dict[str, Any]) -> str:
    cycle_context = payload.get("cycle_context") or {}
    market = cycle_context.get("market_evidence") or {}
    observed_age = market.get("age_minutes")
    if observed_age is None:
        return "Unavailable"
    try:
        remaining = max(0, 15 - int(observed_age))
    except (TypeError, ValueError):
        return "Unavailable"
    return f"{remaining}m 00s"


def render_preview_text(payload: dict[str, Any], options: RenderOptions) -> str:
    diagnostics = payload.get("diagnostics") or {}
    timeline = payload.get("timeline") or {}
    command_mode = _fmt(payload.get("command_mode") or payload.get("evaluation_mode") or ("IDEMPOTENT_REPLAY" if payload.get("replayed") else "NEW_PREVIEW"))
    outcome = _fmt(payload.get("outcome") or payload.get("proposed_action") or "HOLD").upper()
    classification = _fmt(payload.get("decision_classification") or _classify_hold(payload)).replace("_", " ").title().replace(" ", "-")
    capital_state = _fmt(payload.get("capital_state") or ("PREVIEW_ONLY" if payload.get("preview_id") else "NONE"))
    raw_explanations = [str(item) for item in (diagnostics.get("deterministic_explanation") or [])]
    humanized = [_humanize_reason(item) for item in raw_explanations]
    signal_reason = "\n".join(entry for entry in humanized if entry)

    lines = _frame_header("AUTONOMOUS PREVIEW", options)
    lines.extend(
        _section(
            "Evaluation",
            [
                ("Mode", _style(command_mode.replace("_", " "), _state_tone(command_mode), options)),
                ("Generated at", _fmt(timeline.get("evaluated_at") or payload.get("started_at") or payload.get("completed_at"), default="Unavailable")),
                ("Cycle age", f"{timeline.get('cycle_age_seconds')} seconds" if timeline.get("cycle_age_seconds") is not None else "Unavailable"),
                ("Decision record", "CREATED NOW" if command_mode == "NEW_PREVIEW" else ("No" if command_mode != "VIEW_EXISTING" else "No new evaluation")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Identifiers",
            [
                ("Cycle ID", _fmt(payload.get("cycle_id"), default="Unavailable")),
                ("Decision Record ID", _fmt(payload.get("decision_record_id"), default="Unavailable")),
                ("Decision Snapshot ID", _fmt((payload.get("decision_snapshot") or {}).get("decision_id") or payload.get("decision_record_id"), default="Unavailable")),
                ("Preview ID", _fmt(payload.get("preview_id"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Market Window",
            [
                ("Asset", _fmt((timeline.get("asset") or (payload.get("cycle_context") or {}).get("product_id") or "BTC-USD"))),
                ("Interval", _fmt((payload.get("cycle_context") or {}).get("strategy_interval") or (payload.get("decision_record") or {}).get("timeframe") or "15m")),
                ("Latest candle open", _fmt(timeline.get("latest_completed_candle_open"), default="Unavailable")),
                ("Latest candle close", _fmt(timeline.get("latest_completed_candle_close"), default="Unavailable")),
                ("History loaded", _fmt(timeline.get("history_candle_count"), default="Unavailable") + " candles" if timeline.get("history_candle_count") is not None else "Unavailable"),
                ("Oldest candle used", _fmt(timeline.get("oldest_candle_used_open"), default="Unavailable")),
                ("Newest candle used", _fmt(timeline.get("latest_completed_candle_close"), default="Unavailable")),
                ("Decision applies to", _fmt(timeline.get("decision_applies_to"), default="Unavailable")),
                ("Current candle excluded", _fmt(timeline.get("current_incomplete_candle_excluded"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Outcome",
            [
                ("Action", _badge(outcome, replayed=command_mode == "IDEMPOTENT_REPLAY", options=options)),
                ("Classification", classification),
                ("Capital state", capital_state),
                ("Risk expected", "Yes" if outcome in {"BUY", "SELL"} else "No"),
                ("Preview expected", "Yes" if outcome in {"BUY", "SELL"} and _fmt(payload.get("risk_verdict")).upper() in {"ACCEPTED", "RESIZED"} else "No"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Timing",
            [
                ("Evaluation timestamp", _fmt(timeline.get("evaluated_at") or payload.get("started_at") or payload.get("completed_at"), default="Unavailable")),
                ("Decision age", f"{timeline.get('decision_age_seconds')} seconds" if timeline.get("decision_age_seconds") is not None else "Unavailable"),
                ("Candle age", f"{timeline.get('market_data_age_seconds')} seconds" if timeline.get("market_data_age_seconds") is not None else "Unavailable"),
                ("Duration", _duration_seconds(payload)),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Safety",
            [
                ("Live Order", _style("NOT SUBMITTED", "good", options)),
                ("Boundary", "Preview-only path"),
            ],
            options,
        )
    )

    lines.extend(_section("Recommendation", [("Operator", _operator_recommendation(payload))], options))

    if payload.get("timeline_warning"):
        lines.extend(_section("Warning", [("EVIDENCE TIMESTAMP MISMATCH", "Fresh cycle and decision timestamps disagree unexpectedly")], options))

    if signal_reason and options.verbose:
        lines.extend(_section("Verbose", [("Deterministic codes", "\n".join(f"- {item}" for item in raw_explanations))], options))
        lines.append("")
        lines.append(_style("Source fields", "muted", options))
        lines.append("- command_mode -> explicit command/evaluation mode")
        lines.append("- timeline.cycle_age_seconds -> autonomous_cycle_runs.created_at")
        lines.append("- timeline.decision_age_seconds -> decision_records.timestamp")
        lines.append("- timeline.market_data_age_seconds -> candles.close_time")
        lines.append("- timeline.latest_completed_candle_open/close -> strategy market window")
        lines.append(f"- outcome -> {_fmt(outcome)}")
        lines.append(f"- classification -> {_fmt(payload.get('decision_classification') or _classify_hold(payload))}")
    return "\n".join(lines)


def render_preview_show_text(payload: dict[str, Any], options: RenderOptions) -> str:
    preview = payload.get("preview") or {}
    decision = payload.get("decision_record") or {}
    snapshot = payload.get("decision_snapshot") or {}
    cycle = payload.get("cycle") or {}
    timeline = payload.get("timeline") or {}
    command_mode = _fmt(payload.get("command_mode") or payload.get("evaluation_mode") or "VIEW_EXISTING")
    outcome = _fmt(payload.get("outcome") or decision.get("outcome") or preview.get("side") or "HOLD").upper()
    classification = _fmt(payload.get("decision_classification") or ("Strategy-derived" if outcome in {"BUY", "SELL"} else _classify_hold({"proposed_action": outcome, "diagnostics": {"deterministic_explanation": list(cycle.get("deterministic_explanation") or [])}})))
    lines = _frame_header("PREVIEW EVIDENCE", options)
    lines.extend(
        _section(
            "Evaluation",
            [
                ("Mode", _style(command_mode.replace("_", " "), _state_tone(command_mode), options)),
                ("Record created", _fmt(timeline.get("record_created_at") or decision.get("timeframe") or preview.get("created_at"), default="Unavailable")),
                ("Age", f"{timeline.get('decision_age_seconds')} seconds" if timeline.get("decision_age_seconds") is not None else "Unavailable"),
                ("New evaluation", "No"),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Identifiers",
            [
                ("Preview ID", _fmt(preview.get("crypto_order_preview_id"), default="Unavailable")),
                ("Decision Record ID", _fmt(decision.get("decision_id"), default="Unavailable")),
                ("Decision Snapshot ID", _fmt(snapshot.get("decision_id") or decision.get("decision_id"), default="Unavailable")),
                ("Cycle ID", _fmt(cycle.get("cycle_id"), default="Unavailable")),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Market Window",
            [
                ("Asset", _fmt(preview.get("product_id"), default="Unavailable")),
                ("Interval", _fmt(decision.get("timeframe"), default="Unavailable")),
                ("Latest candle", _fmt(timeline.get("latest_completed_candle_close"), default="Unavailable")),
                ("History loaded", _fmt(timeline.get("history_candle_count"), default="Unavailable") + " candles" if timeline.get("history_candle_count") is not None else "Unavailable"),
                ("Decision applies to", _fmt(timeline.get("decision_applies_to"), default="Unavailable")),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Outcome",
            [
                ("Action", _badge(outcome, replayed=False, options=options)),
                ("Classification", classification.replace("_", " ").title()),
                ("Capital state", _fmt(payload.get("capital_state") or ("PREVIEW_ONLY" if preview else "NONE"))),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Safety",
            [
                ("Mode", "Read-only evidence"),
                ("Submission", _style("NOT SUBMITTED", "good", options)),
            ],
            options,
        )
    )
    warning_messages = preview.get("warning_messages") or []
    if warning_messages:
        lines.extend(_section("Warnings", [("Count", str(len(warning_messages)))], options))
        for message in warning_messages:
            lines.append(f"- {message}")
        lines.append("")
    strategy_inputs = snapshot.get("strategy_inputs") or {}
    signal_reason = strategy_inputs.get("signal_reason")
    if signal_reason:
        lines.extend(_section("Signal", [("Reason", _fmt(signal_reason))], options))
    if options.verbose:
        lines.append(_style("Verbose deterministic codes", "muted", options))
        for item in cycle.get("deterministic_explanation") or []:
            lines.append(f"- {item}")
        lines.append("")
        lines.append(_style("Source fields", "muted", options))
        lines.append("- command_mode -> explicit preview-show mode")
        lines.append("- decision_age_seconds -> decision_records.timestamp")
        lines.append("- record_created_at -> decision_records.timestamp")
        lines.append("- history_candle_count -> cycle timeline slice")
    return "\n".join(lines)


def render_candles_text(payload: dict[str, Any], options: RenderOptions) -> str:
    ready = bool(payload.get("ready"))
    lines = _frame_header("CANDLE READINESS", options)
    lines.extend(
        _section(
            "Market",
            [
                ("Symbol", _fmt(payload.get("symbol"))),
                ("Exchange", _fmt(payload.get("exchange"), default="Unavailable")),
                ("Interval", _fmt(payload.get("interval"))),
                ("Rows", _fmt(payload.get("row_count"))),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Timing",
            [
                ("Latest open", _fmt(payload.get("latest_open_time"), default="Unavailable")),
                ("Latest close", _fmt(payload.get("latest_close_time"), default="Unavailable")),
                ("Candle age", f"{_fmt(payload.get('age_minutes'), default='Unavailable')} minutes" if payload.get("age_minutes") is not None else "Unavailable"),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Readiness",
            [
                ("Verdict", _style("READY" if ready else "NOT READY", "good" if ready else "warn", options)),
                ("Reason", _humanize_reason(_fmt(payload.get("reason"), default="Unavailable"))),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Safety",
            [
                ("Mode", "Read-only inspection"),
                ("Capital movement", "None"),
            ],
            options,
        )
    )
    return "\n".join(lines)


def render_status_text(payload: dict[str, Any], options: RenderOptions) -> str:
    latest_cycle = payload.get("latest_cycle") or {}
    latest_preview = payload.get("latest_preview") or {}
    safety_flags = payload.get("safety_flags") or {}
    lines = _frame_header("MISSION CONTROL STATUS", options)

    lines.extend(
        _section(
            "Overall",
            [
                ("Environment", _fmt(payload.get("environment"))),
                ("Git SHA", _fmt(payload.get("git_sha"), default="Unavailable")),
                ("API", _fmt(payload.get("api_status"), default="Unavailable")),
                ("Worker", _fmt(payload.get("worker_status"), default="Unavailable")),
                ("Database", _fmt(payload.get("database_status"), default="Unavailable")),
                ("System health", _fmt(payload.get("system_health"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Market",
            [
                ("Kraken", _fmt(payload.get("kraken_status"), default="Unavailable")),
                ("Candles", _fmt((payload.get("candle_summary") or {}).get("reason"), default="Unavailable")),
                ("Candle age", f"{_fmt((payload.get('candle_summary') or {}).get('age_minutes'), default='Unavailable')} minutes" if (payload.get("candle_summary") or {}).get("age_minutes") is not None else "Unavailable"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Mandate",
            [
                ("Mandate ID", _fmt(payload.get("mandate_id"), default="Unavailable")),
                ("Mandate status", _fmt(payload.get("mandate_status"), default="Unavailable")),
                ("Submission boundary", "LIVE BLOCKED" if not safety_flags.get("live_crypto_order_submission_enabled") else "LIVE ENABLED"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Latest Cycle",
            [
                ("Cycle ID", _fmt(latest_cycle.get("cycle_id"), default="Unavailable")),
                ("Decision", _badge(latest_cycle.get("proposed_action"), replayed=False, options=options)),
                ("State", _fmt(latest_cycle.get("state"), default="Unavailable")),
                ("Strategy", _fmt((payload.get("latest_strategy") or {}).get("name"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Portfolio",
            [
                ("Campaign count", _fmt(payload.get("campaign_count"), default="Unavailable")),
                ("Decision count", _fmt(payload.get("decision_count"), default="Unavailable")),
                ("Open positions", _fmt(payload.get("open_positions"), default="Unavailable")),
                ("Open previews", _fmt(payload.get("open_previews"), default="Unavailable")),
                ("Open live orders", _fmt(payload.get("open_live_orders"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Safety",
            [
                ("Dry run", _fmt(safety_flags.get("live_crypto_dry_run_enabled"), default="Unavailable")),
                ("Research", _fmt(payload.get("research_status"), default="Unavailable")),
                ("Max order USD", _fmt(safety_flags.get("live_crypto_max_order_usd"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Recommendation",
            [("Operator", _fmt(payload.get("operator_recommendation"), default="No action required."))],
            options,
        )
    )

    connection_summary = payload.get("connection_summary") or []
    if connection_summary and options.verbose:
        lines.append("Exchange connections")
        lines.append("--------------------" if not options.unicode_enabled else "────────────────────")
        for row in connection_summary:
            lines.append(
                f"- {row.get('provider')} {row.get('environment')} status={row.get('status')} readiness={row.get('last_readiness_verdict')}"
            )
        lines.append("")

    return "\n".join(lines)


def render_watch_text(payload: dict[str, Any], options: RenderOptions) -> str:
    latest_cycle = payload.get("latest_cycle") or {}
    candle_summary = payload.get("candle_summary") or {}
    lines = _frame_header("OPERATOR WATCH", options)
    lines.extend(
        _section(
            "Live Snapshot",
            [
                ("Latest decision", _badge(latest_cycle.get("proposed_action"), replayed=False, options=options)),
                ("Worker heartbeat", _fmt(payload.get("worker_heartbeat"), default="Unavailable")),
                ("Campaign count", _fmt(payload.get("campaign_count"), default="Unavailable")),
                ("Decision count", _fmt(payload.get("decision_count"), default="Unavailable")),
                ("Candles", _fmt(candle_summary.get("reason"), default="Unavailable")),
                ("Current signal", _fmt(latest_cycle.get("proposed_action"), default="Unavailable")),
                ("System health", _fmt(payload.get("system_health"), default="Unavailable")),
            ],
            options,
        )
    )
    lines.append(_style("Press Ctrl+C to exit watch mode.", "muted", options))
    return "\n".join(lines)


def _strategy_label(slug: str | None) -> str:
    value = (slug or "unknown").replace("_", " ").strip()
    if not value:
        return "Unknown"
    return " ".join(part.capitalize() for part in value.split())


def _roster_failure_reason(item: dict[str, Any]) -> str:
    codes = [str(code).strip().lower() for code in (item.get("deterministic_explanation") or [])]
    reason_text = str(item.get("reason") or "").strip().lower()

    if any("insufficient_candle_history" in code for code in codes) or "insufficient candle history" in reason_text:
        return "insufficient_history"
    if any("strategy_row_missing" in code for code in codes):
        return "strategy_row_missing"
    if any("strategy_not_registered" in code for code in codes):
        return "strategy_not_registered"
    if any("incomplete_candle" in code for code in codes):
        return "incomplete_candle"
    if any("stale_candle" in code for code in codes):
        return "stale_candle"
    if any("strategy_evaluation_exception" in code for code in codes):
        return "strategy_evaluation_exception"
    return reason_text.replace(" ", "_") if reason_text else "evaluation_failed"


def _roster_display_status(item: dict[str, Any]) -> tuple[str, str | None]:
    evaluation_status = str(item.get("evaluation_status") or "").upper()
    action = str(item.get("action") or "").upper()

    if evaluation_status == "FAILED":
        return "FAILED", _roster_failure_reason(item)
    if evaluation_status == "INSUFFICIENT_CONTEXT":
        return "FAILED", _roster_failure_reason(item)
    if action in {"BUY", "SELL", "HOLD"}:
        return action, None
    return "FAILED", "evaluation_failed"


def render_roster_text(payload: dict[str, Any], options: RenderOptions) -> str:
    run = payload.get("roster_run") or {}
    proposals = payload.get("proposals") or []
    lines = _frame_header("STRATEGY ROSTER", options)

    if not run:
        lines.append("No roster run found for this market.")
        return "\n".join(lines)

    lines.extend(
        _section(
            "Market",
            [
                ("Asset", _fmt(payload.get("product_id"), default="Unavailable")),
                ("Interval", _fmt(payload.get("interval"), default="Unavailable")),
                ("Candle close", _fmt(run.get("candle_close_time"), default="Unavailable")),
                ("Trigger", _fmt(run.get("trigger"), default="Unavailable")),
            ],
            options,
        )
    )

    buy_count = 0
    sell_count = 0
    hold_count = 0
    failed_count = 0

    for item in proposals:
        strategy_name = _strategy_label(item.get("strategy_slug"))
        status, reason = _roster_display_status(item)
        if status == "BUY":
            buy_count += 1
        elif status == "SELL":
            sell_count += 1
        elif status == "HOLD":
            hold_count += 1
        else:
            failed_count += 1
        lines.append(f"{strategy_name:<20} {_badge(status, replayed=False, options=options)}")
        if reason is not None:
            lines.append(f"  reason: {reason}")

    if proposals:
        lines.append("")

    lines.extend(
        _section(
            "Summary",
            [
                ("BUY", _fmt(buy_count, default="0")),
                ("SELL", _fmt(sell_count, default="0")),
                ("HOLD", _fmt(hold_count, default="0")),
                ("Failed", _fmt(failed_count, default="0")),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Execution",
            [
                ("Mode", _fmt(run.get("execution_mode"), default="SHADOW")),
                ("Capital moved", "No"),
                ("Live submission", "Disabled" if not run.get("live_submission_allowed") else "Enabled"),
            ],
            options,
        )
    )

    return "\n".join(lines)


def render_scorecards_text(payload: dict[str, Any], options: RenderOptions) -> str:
    lines = _frame_header("STRATEGY SCORECARDS", options)
    lines.extend(
        _section(
            "Market",
            [
                ("Provider", _fmt(payload.get("provider"), default="Unavailable")),
                ("Product", _fmt(payload.get("product_id"), default="Unavailable")),
                ("Interval", _fmt(payload.get("interval"), default="Unavailable")),
                ("Latest scored", _fmt(payload.get("latest_outcome_evaluated_at"), default="Unavailable")),
            ],
            options,
        )
    )

    scorecards = payload.get("scorecards") or []
    if not scorecards:
        lines.append("No scorecards found for this market.")
        return "\n".join(lines)

    lines.append("All metrics show explicit population sizes per horizon and aggregate.")
    lines.append("")

    def _pct(value: Any) -> str:
        if value is None:
            return "-"
        return f"{Decimal(str(value)):.4f}"

    def _bucket_lines(bucket: dict[str, Any], *, title: str) -> list[str]:
        total = int(bucket.get("total_evaluated") or 0)
        buy_eval = int(bucket.get("buy_evaluations") or 0)
        buy_correct = int(bucket.get("buy_correct") or 0)
        sell_eval = int(bucket.get("sell_evaluations") or 0)
        sell_correct = int(bucket.get("sell_correct") or 0)
        hold_eval = int(bucket.get("hold_evaluations") or 0)
        hold_correct = int(bucket.get("hold_correct") or 0)
        reconciliation_total = buy_eval + sell_eval + hold_eval

        return [
            f"  {title}",
            f"    population(total_evaluated): {total}",
            f"    BUY evaluations/correct: {buy_eval}/{buy_correct}",
            f"    SELL evaluations/correct: {sell_eval}/{sell_correct}",
            f"    HOLD evaluations/correct: {hold_eval}/{hold_correct}",
            f"    reconciliation(BUY+SELL+HOLD==total): {reconciliation_total}=={total}",
            f"    overall_correct_pct: {_pct(bucket.get('overall_correct_pct'))}",
            f"    average_raw_return_pct: {_pct(bucket.get('average_raw_return_pct'))}",
            f"    average_fee_adjusted_return_pct: {_pct(bucket.get('average_fee_adjusted_return_pct'))}",
            f"    average_mfe_pct: {_pct(bucket.get('average_mfe_pct'))}",
            f"    average_mae_pct: {_pct(bucket.get('average_mae_pct'))}",
        ]

    for card in scorecards:
        strategy = str(card.get("strategy_slug") or "unknown")
        lines.append(f"Strategy: {strategy}")

        for bucket in card.get("per_horizon") or []:
            horizon = str(bucket.get("horizon") or "unknown")
            lines.extend(_bucket_lines(bucket, title=f"Per Horizon [{horizon}]"))

        aggregate = card.get("aggregate") or {}
        lines.extend(_bucket_lines(aggregate, title="Aggregate [all horizons combined]"))

        evidence_count = int(card.get("regime_evidence_count") or 0)
        min_required = int(card.get("regime_min_evidence_required") or 0)
        best_regime = card.get("best_regime")
        worst_regime = card.get("worst_regime")

        if evidence_count < min_required:
            lines.append(f"  Best Regime: Insufficient evidence ({evidence_count}/{min_required})")
            lines.append(f"  Worst Regime: Insufficient evidence ({evidence_count}/{min_required})")
        else:
            lines.append(f"  Best Regime: {best_regime or '-'}")
            lines.append(f"  Worst Regime: {worst_regime or '-'}")

        lines.append("")

    return "\n".join(lines)


def _forensics_bool(value: Any) -> str:
    if isinstance(value, str) and value.upper() in {"YES", "NO", "UNPROVEN", "NOT APPLICABLE"}:
        return value.upper()
    return "YES" if bool(value) else "NO"


def render_execution_forensics_text(payload: dict[str, Any], options: RenderOptions) -> str:
    lines = _frame_header("PRODUCTION EXECUTION FORENSICS", options)
    criteria = payload.get("criteria") or {}
    lines.extend(
        _section(
            "Scope",
            [
                ("Mode", _fmt(payload.get("mode"), default="Unavailable")),
                ("Selector", _fmt(criteria.get("selector"), default="Unavailable")),
                ("Since", _fmt(criteria.get("resolved_since") or criteria.get("since"), default="N/A")),
                ("Cycle ID", _fmt(criteria.get("cycle_id"), default="N/A")),
                ("Cycles", _fmt(payload.get("cycle_count"), default="0")),
            ],
            options,
        )
    )

    cycles = payload.get("cycles") or []
    if not cycles:
        lines.append("No autonomous cycles matched this selector.")
        return "\n".join(lines)

    for idx, cycle in enumerate(cycles, start=1):
        lines.append(f"Cycle {idx}")
        lines.append("--------------------" if not options.unicode_enabled else "────────────────────")
        lines.extend(
            _section(
                "Cycle",
                [
                    ("Cycle ID", _fmt(cycle.get("cycle_id"), default="Unavailable")),
                    ("Timestamp", _fmt(cycle.get("timestamp"), default="Unavailable")),
                    ("Asset", _fmt(cycle.get("asset"), default="Unavailable")),
                    ("Provider", _fmt(cycle.get("provider"), default="Unavailable")),
                    ("Interval", _fmt(cycle.get("interval"), default="Unavailable")),
                    ("Latest candle", _fmt(cycle.get("latest_candle_time"), default="Unavailable")),
                ],
                options,
            )
        )

        signal_section = cycle.get("signal_section") or {}
        signal_rows = signal_section.get("signals") or []
        lines.extend(
            _section(
                "Legacy Signals",
                [
                    ("Executable signals", _fmt(signal_section.get("signals_generated"), default="0")),
                    ("Source", _fmt(signal_section.get("source"), default="signals_table_via_decision_lineage")),
                ],
                options,
            )
        )
        for signal in signal_rows:
            lines.append(
                f"- signal_id={_fmt(signal.get('signal_id'))} strategy={_fmt(signal.get('strategy'))} action={_fmt(signal.get('action'))} confidence={_fmt(signal.get('confidence'), default='N/A')} reason={_fmt(signal.get('reason'), default='N/A')}"
            )
        if signal_rows:
            lines.append("")

        roster = cycle.get("strategy_roster") or {}
        lines.extend(
            _section(
                "Strategy Roster",
                [
                    ("Proposals", _fmt(roster.get("proposal_count"), default="0")),
                    ("BUY", _fmt(roster.get("buy_count"), default="0")),
                    ("SELL", _fmt(roster.get("sell_count"), default="0")),
                    ("HOLD", _fmt(roster.get("hold_count"), default="0")),
                    ("Mode", _fmt(roster.get("mode"), default="SHADOW")),
                    ("Executable", _forensics_bool(roster.get("executable"))),
                    ("Reason", _fmt(roster.get("reason"), default="N/A")),
                ],
                options,
            )
        )

        autonomous = cycle.get("autonomous_decision") or {}
        lines.extend(
            _section(
                "Autonomous Decision",
                [
                    ("Proposed action", _fmt(autonomous.get("proposed_action"), default="UNPROVEN")),
                    ("Mandate verdict", _fmt(autonomous.get("mandate_verdict"), default="UNPROVEN")),
                    ("Risk verdict", _fmt(autonomous.get("risk_verdict"), default="UNPROVEN")),
                    ("Execution handoff", _fmt(autonomous.get("execution_handoff"), default="UNPROVEN")),
                    ("Exact blocker", _fmt(autonomous.get("exact_blocker"), default="UNPROVEN")),
                ],
                options,
            )
        )

        canonical_signal = cycle.get("canonical_signal") or {}
        lines.extend(
            _section(
                "Canonical Signal",
                [
                    ("Signal ID", _fmt(canonical_signal.get("signal_id"), default="N/A")),
                    ("Action", _fmt(canonical_signal.get("action"), default="N/A")),
                    ("Executable", _forensics_bool(canonical_signal.get("executable"))),
                    ("Mode", _fmt(canonical_signal.get("mode"), default="PAPER")),
                ],
                options,
            )
        )

        candidate = cycle.get("execution_candidate") or {}
        lines.extend(
            _section(
                "Candidate",
                [
                    ("Execution candidate", _forensics_bool(candidate.get("status", candidate.get("is_candidate")))),
                    ("If NO, why", _fmt(candidate.get("reason_if_no"), default="N/A")),
                ],
                options,
            )
        )

        risk = cycle.get("risk") or {}
        lines.extend(
            _section(
                "Risk",
                [
                    ("Evaluated", _forensics_bool(risk.get("evaluated_status", risk.get("evaluated")))),
                    ("Decision", _fmt(risk.get("decision"), default="N/A")),
                    ("Reason", _fmt(risk.get("reason"), default="N/A")),
                    ("Risk Event IDs", ", ".join(str(item) for item in (risk.get("risk_event_ids") or [])) or "N/A"),
                ],
                options,
            )
        )

        execution = cycle.get("execution") or {}
        lines.extend(
            _section(
                "Execution",
                [
                    ("Attempted", _forensics_bool(execution.get("execution_attempted_status", execution.get("execution_attempted")))),
                    ("Service called", _forensics_bool(execution.get("execution_service_called_status", execution.get("execution_service_called")))),
                    ("Exact result", _fmt(execution.get("exact_result"), default="N/A")),
                    ("Order created", _forensics_bool(execution.get("order_created_status", execution.get("order_created")))),
                    ("Trade created", _forensics_bool(execution.get("trade_created_status", execution.get("trade_created")))),
                    ("Filled", _forensics_bool(execution.get("filled_status", execution.get("filled")))),
                    ("Rejected", _forensics_bool(execution.get("rejected_status", execution.get("rejected")))),
                    ("Skipped", _forensics_bool(execution.get("skipped_status", execution.get("skipped")))),
                    ("Error", _forensics_bool(execution.get("error_status", execution.get("error")))),
                    ("Trade IDs", ", ".join(str(item) for item in (execution.get("trade_ids") or [])) or "N/A"),
                ],
                options,
            )
        )

        accounting = cycle.get("accounting") or {}
        lines.extend(
            _section(
                "Accounting",
                [
                    ("Paper accounts", ", ".join(str(item) for item in (accounting.get("paper_account_ids") or [])) or "N/A"),
                    ("Balance changed", _forensics_bool(accounting.get("account_balance_changed_status"))),
                    ("Position changed", _forensics_bool(accounting.get("position_changed_status"))),
                    ("Accounting entry", _forensics_bool(accounting.get("accounting_entry_persisted_status"))),
                    ("Fees", _fmt(accounting.get("fees_total"), default="N/A")),
                    ("PnL", _fmt(accounting.get("pnl"), default="N/A")),
                    ("BUY qty", _fmt(accounting.get("buy_quantity_total"), default="N/A")),
                    ("SELL qty", _fmt(accounting.get("sell_quantity_total"), default="N/A")),
                ],
                options,
            )
        )
        for entry in accounting.get("entries") or []:
            lines.append(
                "- trade_id={} account={} balance_before={} balance_after={} position_before={} position_after={} fee={}".format(
                    _fmt(entry.get("trade_id")),
                    _fmt(entry.get("paper_account_id")),
                    _fmt(entry.get("balance_before"), default="N/A"),
                    _fmt(entry.get("balance_after"), default="N/A"),
                    _fmt(entry.get("position_before"), default="N/A"),
                    _fmt(entry.get("position_after"), default="N/A"),
                    _fmt(entry.get("fee"), default="N/A"),
                )
            )
        if accounting.get("entries"):
            lines.append("")

        decision = cycle.get("decision_records") or {}
        auto_link = decision.get("autonomous_cycle_linkage") or {}
        lines.extend(
            _section(
                "Decision Linkage",
                [
                    ("Decision Record ID", _fmt(decision.get("decision_record_id"), default="N/A")),
                    ("Decision linked", _forensics_bool(decision.get("decision_record_linkage_status"))),
                    ("Outcome linkage", _fmt(decision.get("outcome_score_linkage_count"), default="0")),
                    ("Outcome linked", _forensics_bool(decision.get("outcome_linkage_status"))),
                    ("Research linked", _forensics_bool(decision.get("research_linkage_status"))),
                    ("Outcome IDs", ", ".join(str(item) for item in (decision.get("outcome_score_ids") or [])) or "N/A"),
                    ("Autonomous Cycle", _fmt(auto_link.get("cycle_id"), default="N/A")),
                    ("Roster Runs", ", ".join(str(item) for item in (auto_link.get("scheduled_roster_run_ids") or [])) or "N/A"),
                ],
                options,
            )
        )
        research = decision.get("research_linkage") or []
        if research:
            lines.append("Research linkage")
            lines.append("----------------" if not options.unicode_enabled else "────────────────")
            for item in research:
                lines.append(
                    f"- event_id={_fmt(item.get('event_id'))} type={_fmt(item.get('event_type'))} campaign_id={_fmt(item.get('campaign_id'), default='N/A')} created_at={_fmt(item.get('created_at'))}"
                )
            lines.append("")

        lines.extend(
            _section(
                "Summary",
                [("Conclusion", _fmt(cycle.get("summary"), default="Unavailable"))],
                options,
            )
        )

    return "\n".join(lines)


def render_venue_commission_text(payload: dict[str, Any], options: RenderOptions) -> str:
    lines = _frame_header("VENUE COMMISSIONING", options)

    if "checks" in payload:
        lines.extend(
            _section(
                "Readiness",
                [
                    ("Provider", _fmt(payload.get("provider"), default="N/A")),
                    ("Environment", _fmt(payload.get("environment"), default="N/A")),
                    ("Product", _fmt(payload.get("product_id"), default="N/A")),
                    ("Amount USD", _fmt(payload.get("amount_usd"), default="N/A")),
                    ("Hold minutes", _fmt(payload.get("hold_minutes"), default="N/A")),
                    ("Would activate", _forensics_bool(payload.get("would_activate_safely"))),
                    ("Exact blocker", _fmt(payload.get("exact_blocker"), default="NONE")),
                    ("Existing active run", _fmt(payload.get("existing_active_run"), default="NONE")),
                ],
                options,
            )
        )
        checks = payload.get("checks") or []
        if checks:
            lines.append("Checks")
            lines.append("------" if not options.unicode_enabled else "──────")
            for item in checks:
                lines.append(
                    "- {}: {}{}".format(
                        _fmt(item.get("label"), default="Unnamed"),
                        _fmt(item.get("status"), default="UNKNOWN"),
                        "" if not item.get("reason") else f" ({_fmt(item.get('reason'))})",
                    )
                )
            lines.append("")

    run = payload.get("run") if isinstance(payload.get("run"), dict) else None
    if run is not None:
        lines.extend(
            _section(
                "Run",
                [
                    ("Run ID", _fmt(run.get("commissioning_run_id"), default="N/A")),
                    ("Status", _fmt(run.get("status"), default="N/A")),
                    ("Purpose", _fmt(run.get("execution_purpose"), default="N/A")),
                    ("Type", _fmt(run.get("commissioning_type"), default="N/A")),
                    ("Provider", _fmt(run.get("provider"), default="N/A")),
                    ("Environment", _fmt(run.get("environment"), default="N/A")),
                    ("Product", _fmt(run.get("product_id"), default="N/A")),
                    ("Buy client order", _fmt(run.get("buy_client_order_id"), default="N/A")),
                    ("Sell client order", _fmt(run.get("sell_client_order_id"), default="N/A")),
                    ("Hold due", _fmt(run.get("hold_due_at"), default="N/A")),
                    ("Net realized PnL", _fmt(run.get("net_realized_pnl_usd"), default="N/A")),
                    ("Dust BTC", _fmt(run.get("dust_base_btc"), default="N/A")),
                    ("Manual review", _forensics_bool(run.get("manual_intervention_required"))),
                ],
                options,
            )
        )

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else []
    if diagnostics:
        lines.append("Diagnostics")
        lines.append("-----------" if not options.unicode_enabled else "───────────")
        for item in diagnostics:
            code = _fmt(item.get("code"), default="unknown")
            stage = _fmt(item.get("stage"), default="unknown")
            detail = _fmt(item.get("detail"), default="")
            lines.append(f"- {code} @ {stage}{'' if detail in {'', 'unknown'} else f' ({detail})'}")
        lines.append("")

    return "\n".join(lines)


def render_buy_opportunity_diagnostic_text(payload: dict[str, Any], options: RenderOptions) -> str:
    totals = payload.get("totals") or {}
    summary = payload.get("summary") or {}
    campaign = payload.get("canonical_proving_campaign") or {}
    window = payload.get("window") or {}
    buy_rows = payload.get("buy_blockers") or []

    lines = _frame_header("BUY OPPORTUNITY DIAGNOSTIC", options)
    lines.extend(
        _section(
            "Scope",
            [
                ("Window", f"Last {window.get('hours', 24)} hours"),
                ("Start", _fmt(window.get("start"), default="Unavailable")),
                ("End", _fmt(window.get("end"), default="Unavailable")),
                ("Campaign ID", _fmt(campaign.get("campaign_id"), default="Unavailable")),
                ("Campaign Version", _fmt(campaign.get("campaign_version"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Totals",
            [
                ("Strategy evaluations", _fmt(totals.get("strategy_evaluations"), default="0")),
                ("BUY opportunities", _fmt(totals.get("buy_opportunities"), default="0")),
                ("SELL opportunities", _fmt(totals.get("sell_opportunities"), default="0")),
                ("HOLD decisions", _fmt(totals.get("hold_decisions"), default="0")),
            ],
            options,
        )
    )

    if payload.get("no_buy_opportunities"):
        lines.append("No BUY opportunities existed in the last 24 hours.")
        lines.append("")
    else:
        lines.append("BUY blockers")
        lines.append("------------" if not options.unicode_enabled else "────────────")
        for item in buy_rows:
            cycle_id = _fmt(item.get("cycle_id"), default="unknown")
            blocker = _fmt(item.get("first_blocker"), default="other")
            reason = _fmt(item.get("blocker_reason"), default="unknown")
            ready = bool(item.get("ready_package"))
            ready_note = "ready package: yes" if ready else "ready package: no"
            lines.append(f"- cycle {cycle_id}: blocker={blocker} ({reason}) [{ready_note}]")
        lines.append("")

    lines.append("Summary")
    lines.append("-------" if not options.unicode_enabled else "───────")
    lines.append(f"BUY opportunities: {summary.get('buy_opportunities', 0)}")
    lines.append(f"READY packages: {summary.get('ready_packages', 0)}")
    lines.append(f"Primary blocker: {summary.get('primary_blocker', 'none')}")
    return "\n".join(lines)


def render_hold_decision_diagnostic_text(payload: dict[str, Any], options: RenderOptions) -> str:
    totals = payload.get("totals") or {}
    summary = payload.get("summary") or {}
    campaign = payload.get("canonical_proving_campaign") or {}
    window = payload.get("window") or {}
    holds = payload.get("hold_decisions") or []

    lines = _frame_header("HOLD DECISION DIAGNOSTIC", options)
    lines.extend(
        _section(
            "Scope",
            [
                ("Window", f"Last {window.get('hours', 24)} hours"),
                ("Start", _fmt(window.get("start"), default="Unavailable")),
                ("End", _fmt(window.get("end"), default="Unavailable")),
                ("Campaign ID", _fmt(campaign.get("campaign_id"), default="Unavailable")),
                ("Campaign Version", _fmt(campaign.get("campaign_version"), default="Unavailable")),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Totals",
            [
                ("Strategy evaluations", _fmt(totals.get("strategy_evaluations"), default="0")),
                ("BUY opportunities", _fmt(totals.get("buy_opportunities"), default="0")),
                ("SELL opportunities", _fmt(totals.get("sell_opportunities"), default="0")),
                ("HOLD decisions", _fmt(totals.get("hold_decisions"), default="0")),
            ],
            options,
        )
    )

    for item in holds:
        lines.append("HOLD decision")
        lines.append("-------------" if not options.unicode_enabled else "─────────────")
        lines.append(f"Decision timestamp: {_fmt(item.get('decision_timestamp'), default='Unavailable')}")
        lines.append(f"Product: {_fmt(item.get('product'), default='Unavailable')}")
        lines.append(f"Strategy identity: {_fmt(item.get('strategy_identity'), default='Unavailable')}")
        lines.append(f"Strategy version: {_fmt(item.get('strategy_version'), default='Unavailable')}")
        lines.append(f"Candle ID: {_fmt(item.get('candle_id'), default='Unavailable')}")
        lines.append(f"Candle close time: {_fmt(item.get('candle_close_time'), default='Unavailable')}")
        lines.append(f"HOLD reason: {_fmt(item.get('hold_reason'), default='Unavailable')}")
        lines.append("")

        lines.append("BUY conditions")
        lines.append("--------------" if not options.unicode_enabled else "──────────────")
        for condition in item.get("buy_conditions") or []:
            lines.append(f"- {condition.get('condition')}")
            lines.append(f"  actual value: {_fmt(condition.get('actual_value'), default='Unavailable')}")
            lines.append(f"  required threshold: {_fmt(condition.get('required_threshold'), default='Unavailable')}")
            lines.append(f"  pass/fail: {'pass' if condition.get('pass') else 'fail'}")
        lines.append("")

        lines.append("SELL conditions")
        lines.append("---------------" if not options.unicode_enabled else "───────────────")
        for condition in item.get("sell_conditions") or []:
            lines.append(f"- {condition.get('condition')}")
            lines.append(f"  actual value: {_fmt(condition.get('actual_value'), default='Unavailable')}")
            lines.append(f"  required threshold: {_fmt(condition.get('required_threshold'), default='Unavailable')}")
            lines.append(f"  pass/fail: {'pass' if condition.get('pass') else 'fail'}")
        lines.append("")

        lines.append(f"First unmet BUY condition: {_fmt(item.get('first_unmet_buy_condition'), default='none')}")
        distance = item.get("distance_to_buy") if isinstance(item.get("distance_to_buy"), dict) else None
        if distance is None:
            lines.append("Distance to BUY: unavailable")
        else:
            lines.append(f"Distance to BUY: {_fmt(distance.get('distance'), default='Unavailable')} {_fmt(distance.get('unit'), default='')}")

        missing = item.get("missing_evidence") or []
        if missing:
            lines.append("Missing evidence")
            lines.append("----------------" if not options.unicode_enabled else "────────────────")
            for evidence in missing:
                lines.append(f"- {evidence}")
        lines.append("")

    lines.append("Strategy evaluations:")
    lines.append(str(summary.get("strategy_evaluations", 0)))
    lines.append("")
    lines.append("BUY opportunities:")
    lines.append(str(summary.get("buy_opportunities", 0)))
    lines.append("")
    lines.append("SELL opportunities:")
    lines.append(str(summary.get("sell_opportunities", 0)))
    lines.append("")
    lines.append("HOLD decisions:")
    lines.append(str(summary.get("hold_decisions", 0)))
    lines.append("")
    lines.append("Most common HOLD reason:")
    lines.append(str(summary.get("most_common_hold_reason", "none")))
    lines.append("")
    lines.append("Most common unmet BUY condition:")
    lines.append(str(summary.get("most_common_unmet_buy_condition", "none")))
    return "\n".join(lines)
