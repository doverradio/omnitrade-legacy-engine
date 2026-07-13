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
    cycle_context = payload.get("cycle_context") or {}
    strategy = cycle_context.get("strategy") or {}
    market = cycle_context.get("market_evidence") or {}
    mandate_status = payload.get("mandate_verdict")
    action = str(payload.get("proposed_action") or "HOLD")

    lines = _frame_header("AUTONOMOUS PREVIEW", options)
    lines.extend(
        _section(
            "Overall",
            [
                ("State", _style(_fmt(payload.get("state")).upper(), _state_tone(_fmt(payload.get("state"))), options)),
                ("Decision", _badge(action, replayed=bool(payload.get("replayed")), options=options)),
                ("Type", _classify_hold(payload) if action.upper() == "HOLD" else "Capital movement candidate"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Market",
            [
                ("Asset", _fmt((cycle_context.get("product_id") or payload.get("product_id") or "BTC-USD"))),
                ("Exchange", _fmt((cycle_context.get("provider") or cycle_context.get("exchange_environment") or market.get("provider") or "Unavailable"))),
                ("Interval", _fmt(cycle_context.get("strategy_interval") or cycle_context.get("interval") or "15m")),
                ("Candle age", f"{_fmt(market.get('age_minutes'), default='Unavailable')} minutes" if market.get("age_minutes") is not None else "Unavailable"),
                ("Readiness", "READY" if market.get("reference_price") else "Unavailable"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Mandate",
            [("Verdict", _style(_fmt(mandate_status).upper(), _state_tone(_fmt(mandate_status)), options))],
            options,
        )
    )

    lines.extend(
        _section(
            "Strategy",
            [
                ("Name", _fmt(strategy.get("name"), default="Unavailable")),
                ("Version", _fmt(strategy.get("version"), default="Unavailable")),
            ],
            options,
        )
    )

    raw_explanations = [str(item) for item in (diagnostics.get("deterministic_explanation") or [])]
    humanized = [_humanize_reason(item) for item in raw_explanations]
    signal_reason = " ".join(entry for entry in humanized if entry)
    lines.extend(
        _section(
            "Signal",
            [
                ("Action", _badge(action, replayed=False, options=options)),
                ("Reason", signal_reason or "Unavailable"),
            ],
            options,
        )
    )

    risk_expected = action.upper() in {"BUY", "SELL"}
    preview_expected = risk_expected and str(payload.get("risk_verdict") or "").upper() in {"ACCEPTED", "RESIZED"}
    lines.extend(
        _section(
            "Risk",
            [
                ("Verdict", _style(_fmt(payload.get("risk_verdict")), _state_tone(_fmt(payload.get("risk_verdict"))), options)),
                ("Expected", "Yes" if risk_expected else "No"),
                ("Preview expected", "Yes" if preview_expected else "No"),
            ],
            options,
        )
    )

    lines.extend(
        _section(
            "Decision Record",
            [
                ("Decision ID", _fmt(payload.get("decision_record_id"), default="Unavailable")),
                ("Preview ID", _fmt(payload.get("preview_id"), default="Unavailable")),
            ],
            options,
        )
    )

    completed_age = _minutes_age(payload.get("completed_at"))
    lines.extend(
        _section(
            "Timing",
            [
                ("Duration", _duration_seconds(payload)),
                ("Decision age", f"{completed_age} minutes" if completed_age is not None else "Unavailable"),
                ("Next evaluation", _next_evaluation(payload)),
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

    lines.extend(_section("Recommendation", [("Action", _operator_recommendation(payload))], options))

    explanation = diagnostics.get("deterministic_explanation") or []
    if explanation and options.verbose:
        lines.append("Verbose deterministic codes")
        lines.append("--------------------------" if not options.unicode_enabled else "──────────────────────────")
        for item in explanation:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


def render_preview_show_text(payload: dict[str, Any], options: RenderOptions) -> str:
    preview = payload.get("preview") or {}
    decision = payload.get("decision_record") or {}
    snapshot = payload.get("decision_snapshot") or {}
    cycle = payload.get("cycle") or {}
    lines = _frame_header("PREVIEW EVIDENCE", options)
    lines.extend(
        _section(
            "Overall",
            [
                ("Preview", _fmt(preview.get("crypto_order_preview_id"), default="Unavailable")),
                ("Status", _style(_fmt(preview.get("status")), _state_tone(_fmt(preview.get("status"))), options)),
                ("Decision", _badge(preview.get("side"), replayed=False, options=options)),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Market",
            [
                ("Provider", _fmt(preview.get("provider"), default="Unavailable")),
                ("Environment", _fmt(preview.get("environment"), default="Unavailable")),
                ("Product", _fmt(preview.get("product_id"), default="Unavailable")),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Risk",
            [
                ("Readiness", _fmt(preview.get("readiness_verdict"), default="Unavailable")),
                ("Risk verdict", _fmt(preview.get("risk_verdict"), default="Unavailable")),
                ("Trade accepted", _fmt(decision.get("trade_accepted"), default="Unavailable")),
            ],
            options,
        )
    )
    lines.extend(
        _section(
            "Decision Record",
            [
                ("Decision ID", _fmt(decision.get("decision_id"), default="Unavailable")),
                ("Timeframe", _fmt(decision.get("timeframe"), default="Unavailable")),
                ("Strategy", _fmt(snapshot.get("strategy_version"), default="Unavailable")),
                ("Config", _fmt(snapshot.get("configuration_version"), default="Unavailable")),
                ("Cycle", _fmt(cycle.get("cycle_id"), default="Unavailable")),
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
        lines.append(_style("Warnings", "warn", options))
        for message in warning_messages:
            lines.append(f"- {message}")
        lines.append("")

    strategy_inputs = snapshot.get("strategy_inputs") or {}
    signal_reason = strategy_inputs.get("signal_reason")
    if signal_reason:
        lines.append(f"Signal reason: {_fmt(signal_reason)}")
        lines.append("")

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
