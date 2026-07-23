#!/usr/bin/env python3
"""OmniTrade Operator Console -- a read-only, real-time dashboard over the
orchestration worker's journalctl output.

NOT production code. Never writes to OmniTrade's database, config, or
services -- it only shells out to `journalctl -f` (read-only) and prints a
human-readable summary of what it reads. Safe to run alongside production
at any time; killing it (Ctrl-C) has zero effect on the orchestration worker.

Run:
    python tools/operator_console.py --timezone America/New_York

The display timezone may also be supplied with
OMNITRADE_OPERATOR_TIMEZONE (preferred) or the standard TZ environment
variable. If none is configured, the VPS operating-system timezone is used.

Standard library only, no external dependencies.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SERVICE_UNIT = "omnitrade-orchestration.service"
# --utc pins journalctl's timestamps to UTC regardless of this VPS's system
# timezone -- without it, journalctl renders in whatever local tz the box
# happens to be configured with (commonly UTC on cloud images), and that
# ambiguity was the actual root cause of the display bug: there was no way
# to tell, from the line alone, what tz its digits were already in.
JOURNAL_CMD = ["sudo", "journalctl", "-u", SERVICE_UNIT, "-f", "-o", "short-iso", "--utc"]

# --- Colors ---
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BRIGHT_GREEN, BRIGHT_RED, YELLOW = "\033[92m", "\033[91m", "\033[93m"
BLUE, CYAN, MAGENTA = "\033[94m", "\033[96m", "\033[95m"


def c(text: object, color: str) -> str:
    return f"{color}{text}{RESET}"


# --- Event recognition ---
# Add new event names here as OmniTrade grows (Position Opened/Closed, Risk
# Limit Triggered, Trailing Stop Updated, Take Profit Hit, Daily P&L,
# Portfolio Value, Capital Allocation, Decision Arena Score, AI Coach
# Review, Operator Alerts, ...) -- nothing else needs to change to start
# accumulating their fields onto the current cycle card; see Cycle.absorb.
EVENT_NAMES = (
    "strategy_aggregate_completed",
    "net_edge_evaluated",
    "non_positive_net_edge_rejection_explained",
    "campaign_cycle_termination_resolved",
    "automatic_ready_package_created",
    "automatic_ready_package_skipped",
    "unresolved_reconciliation_gate_triggered",
    "unresolved_reconciliation_record_detail",
)
_EVENT_RE = re.compile(r"\b(" + "|".join(EVENT_NAMES) + r")\b(.*)$")
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2}))")
# key=value, where value may be a bracketed/parenthesized/quoted group
# (handles rejected_candidates=[('BTC-USD', 'reason')], rejection_reasons=["x"]).
_KV_RE = re.compile(r'(\w+)=(\[[^\]]*\]|\([^)]*\)|"[^"]*"|\S+)')
_TERMINAL_EVENTS = {"automatic_ready_package_created", "automatic_ready_package_skipped"}


def _display_timezone() -> ZoneInfo | None:
    """The timezone timestamps are converted to for display, resolved fresh
    on every call so a long-running console session stays correct across a
    DST transition. Precedence: OMNITRADE_OPERATOR_TIMEZONE (an explicit
    OmniTrade-level operator setting, so it wins even if TZ is set to
    something else for unrelated reasons) -- there is no other existing
    OmniTrade configuration value for this (checked app/config.py and
    .env.example); then the standard TZ env var; then None, which signals
    "use the VPS OS timezone" and which datetime.astimezone(None) resolves
    DST-correctly for the specific instant being converted. An operator
    connected to a UTC VPS must configure their own IANA timezone explicitly;
    an SSH session cannot infer the remote operator's workstation timezone."""
    for variable in ("OMNITRADE_OPERATOR_TIMEZONE", "TZ"):
        tz_name = os.environ.get(variable)
        if not tz_name:
            continue
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid IANA timezone in {variable}: {tz_name}") from exc
    return None


def _configure_display_timezone(tz_name: str | None) -> None:
    """Apply and validate the command-line timezone override before the
    journal stream starts. Environment-based configuration remains dynamic
    so a long-running process retains the existing DST-correct behavior."""
    if tz_name:
        os.environ["OMNITRADE_OPERATOR_TIMEZONE"] = tz_name
    _display_timezone()


def _display_timezone_label() -> str:
    configured = os.environ.get("OMNITRADE_OPERATOR_TIMEZONE") or os.environ.get("TZ")
    if configured:
        return configured
    local = datetime.now().astimezone().tzinfo
    return str(local or "OS local")


def _format_time(dt_source: datetime) -> str:
    """Converts an aware journal datetime to the configured display timezone.
    journalctl is invoked with --utc, but the numeric offset is still parsed
    from every line rather than discarded or assumed. Conversion happens
    exactly once, here at the display boundary."""
    if dt_source.tzinfo is None:
        raise ValueError("journal timestamp must be timezone-aware")
    local_dt = dt_source.astimezone(_display_timezone())
    hour12 = local_dt.hour % 12 or 12
    period = "AM" if local_dt.hour < 12 else "PM"
    return f"{hour12}:{local_dt.minute:02d} {period}"


def parse_line(line: str) -> tuple[str, str, dict[str, str | None]] | None:
    """Return (time_of_day, event_name, fields), or None if this line isn't
    one of EVENT_NAMES. Tolerant of whatever prefix journalctl/python
    logging put in front of the message -- only the event keyword and what
    follows it are used, so the log format can change without breaking this.

    OmniTrade logs Python None values via %s formatting, so an absent field
    shows up as the literal text "None" (e.g. "underlying_reason=None"), not
    as a missing key. Left as a string, that text is truthy and defeats every
    `a or b` fallback chain downstream -- it must be normalized back to real
    None here, once, at the parsing boundary, rather than special-cased at
    every call site that reads a field."""
    match = _EVENT_RE.search(line)
    if match is None:
        return None
    event_name, payload = match.group(1), match.group(2)
    time_match = _TIMESTAMP_RE.match(line)
    dt_source = (
        datetime.strptime(time_match.group(1).replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        if time_match
        else datetime.now(timezone.utc)
    )
    time_str = _format_time(dt_source)
    fields: dict[str, str | None] = {}
    for key, raw_value in _KV_RE.findall(payload):
        value = raw_value.strip('"')
        fields[key] = None if value == "None" else value
    return time_str, event_name, fields


# --- Cycle accumulation ---


@dataclass
class Cycle:
    """Everything observed so far about one trading cycle. Fields are
    populated opportunistically as matching events arrive -- a card is
    rendered with whatever is available, never blocked on a field that
    never showed up."""

    time_str: str = ""
    action: str | None = None
    instrument: str | None = None
    strategy_identity: str | None = None
    expected_gross_edge_pct: str | None = None
    expected_net_edge_pct: str | None = None
    expected_net_profit: str | None = None
    fees_amount: str | None = None
    final_reason_code: str | None = None
    decision_kind: str | None = None
    proposed_action: str | None = None
    selected_decision_reason: str | None = None
    cycle_id: str | None = None
    decision_record_id: str | None = None
    package_id: str | None = None
    package_created: bool = False
    package_skipped: bool = False
    skip_reason: str | None = None
    underlying_reason: str | None = None
    rejection_reasons: str | None = None
    reconciliation_triggered: bool = False
    reconciliation_records: int = 0

    def has_content(self) -> bool:
        return self.action is not None or self.cycle_id is not None

    def absorb(self, event: str, f: dict[str, str | None], time_str: str) -> None:
        # f.get(key) is used everywhere below, never f.get(key, default) --
        # a field logged as the literal text "None" is normalized to real
        # None by parse_line, and dict.get's default only applies when the
        # key is absent, not when its value is None. Using `or` instead
        # treats both cases the same way: "no value here, keep what we had."
        if not self.time_str:
            self.time_str = time_str
        if event == "strategy_aggregate_completed":
            self.action = f.get("action") or self.action
            self.selected_decision_reason = f.get("reason") or self.selected_decision_reason
        elif event in ("net_edge_evaluated", "non_positive_net_edge_rejection_explained"):
            self.instrument = f.get("instrument") or f.get("asset") or self.instrument
            self.strategy_identity = f.get("strategy_identity") or f.get("strategy_id") or self.strategy_identity
            self.expected_gross_edge_pct = f.get("expected_gross_edge_pct") or self.expected_gross_edge_pct
            self.expected_net_edge_pct = f.get("expected_net_edge_pct") or self.expected_net_edge_pct
            self.expected_net_profit = f.get("expected_net_profit") or f.get("expected_net_dollars") or self.expected_net_profit
            self.fees_amount = f.get("round_trip_fee_amount") or f.get("fees_dollars") or self.fees_amount
            self.final_reason_code = f.get("final_reason_code") or self.final_reason_code
        elif event == "campaign_cycle_termination_resolved":
            self.decision_kind = f.get("decision_kind") or self.decision_kind
            self.proposed_action = f.get("proposed_action") or self.proposed_action
            self.selected_decision_reason = f.get("selected_decision_reason") or self.selected_decision_reason
        elif event == "automatic_ready_package_created":
            self.cycle_id = f.get("cycle_id") or self.cycle_id
            self.decision_record_id = f.get("decision_record_id") or self.decision_record_id
            self.package_id = f.get("package_id") or self.package_id
            self.package_created = True
        elif event == "automatic_ready_package_skipped":
            self.cycle_id = f.get("cycle_id") or self.cycle_id
            self.decision_record_id = f.get("decision_record_id") or self.decision_record_id
            self.skip_reason = f.get("reason") or self.skip_reason
            self.underlying_reason = f.get("underlying_reason") or self.underlying_reason
            self.rejection_reasons = f.get("rejection_reasons") or self.rejection_reasons
            self.package_skipped = True
        elif event == "unresolved_reconciliation_gate_triggered":
            self.reconciliation_triggered = True
            self.reconciliation_records = int(f.get("matched_record_count") or 0)
        elif event == "unresolved_reconciliation_record_detail":
            self.reconciliation_triggered = True


# --- Rendering helpers ---


def _short(value: str | None) -> str:
    if not value:
        return "-"
    return value[:8] + "..." if len(value) > 11 else value


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except ValueError:
        return value
    sign = "+" if amount >= 0 else "-"
    text = f"{abs(amount):.5g}"
    return f"{sign}${text}"


_UNKNOWN_REASON = "Unknown (reason not present in log)"


def _clean_reason_list(value: str | None) -> str | None:
    """rejection_reasons/rejected_candidates arrive as a bracketed list
    string, e.g. ["non_positive_net_edge"] or [('BTC-USD', 'reason')].
    Strip the brackets/quotes for a readable last-resort display; None if
    the list was empty."""
    if not value:
        return None
    cleaned = value.strip("[]").replace('"', "").replace("'", "").strip()
    return cleaned or None


def _final_reason(cycle: Cycle) -> str | None:
    """Best available reason this cycle didn't end in a ready package,
    falling back across every field that might carry it -- underlying_reason
    is the most specific, skip_reason the gate-level cause, final_reason_code
    the net-edge verdict, selected_decision_reason the roster-level signal,
    rejection_reasons the last-resort raw list. None only if a package WAS
    created (nothing to explain) or truly nothing was ever logged."""
    if cycle.package_created:
        return None
    return (
        cycle.underlying_reason
        or cycle.skip_reason
        or cycle.final_reason_code
        or cycle.selected_decision_reason
        or _clean_reason_list(cycle.rejection_reasons)
    )


def _display_reason(cycle: Cycle) -> str:
    """Like _final_reason, but never returns None/empty -- always something
    human-readable to print, even when the logs genuinely carried no reason."""
    return _final_reason(cycle) or _UNKNOWN_REASON


def _is_reconciliation_blocked(cycle: Cycle) -> bool:
    return cycle.reconciliation_triggered or _final_reason(cycle) == "unresolved_reconciliation_exists"


def _status_lines(cycle: Cycle) -> list[str]:
    lines = [c("✅ Strategy Approved", BRIGHT_GREEN)]
    econ_ok = cycle.final_reason_code not in {"non_positive_net_edge", "expected_edge_unavailable"}
    lines.append(c("✅ Economics Approved", BRIGHT_GREEN) if econ_ok else c(f"❌ Economics Rejected ({cycle.final_reason_code})", BRIGHT_RED))
    lines.append(c("✅ Risk Approved", BRIGHT_GREEN) if econ_ok else c("⬜ Risk Not Reached", DIM))
    proposed = cycle.decision_kind or cycle.proposed_action
    if proposed:
        lines.append(c(f"🟦 {proposed}", BLUE))
    if cycle.package_created:
        lines.append(c("✅ Ready Package Created", BRIGHT_GREEN))
    elif _is_reconciliation_blocked(cycle):
        lines.append(c("❌ Reconciliation Blocked", BRIGHT_RED))
    elif _final_reason(cycle):
        lines.append(c(f"❌ {_final_reason(cycle)}", BRIGHT_RED))
    return lines


def render_card(cycle: Cycle) -> str:
    bar = "═" * 49
    out = [bar, cycle.time_str, ""]
    action = (cycle.action or "").upper()

    if action == "HOLD":
        out += [c("🟡 HOLD", YELLOW), "", "Reason", "", _display_reason(cycle), "", "No package created."]
    elif action == "SELL":
        out += [c("🔴 SELL", BRIGHT_RED), ""]
        if cycle.expected_net_profit is not None:
            out += ["Profit", c(_money(cycle.expected_net_profit), CYAN), ""]
        if cycle.fees_amount is not None:
            out += ["Fees", c(f"${cycle.fees_amount}", CYAN), ""]
        out += ["Reconciliation", c("❌ Blocked", BRIGHT_RED) if _is_reconciliation_blocked(cycle) else c("✅ Complete", BRIGHT_GREEN)]
    else:  # BUY, or any future action -- same shape, future-proof by default
        emoji = "🟢" if action == "BUY" else "🔷"
        out += [c(f"{emoji} {action or 'UNKNOWN'}", BRIGHT_GREEN if action == "BUY" else BLUE), ""]
        if cycle.expected_net_profit is not None:
            out += ["Expected Net Profit", c(_money(cycle.expected_net_profit), CYAN), ""]
        if cycle.expected_net_edge_pct is not None:
            out += ["Expected Edge", c(f"{cycle.expected_net_edge_pct}%", MAGENTA), ""]
        out += ["Status", ""] + _status_lines(cycle)
        if cycle.package_skipped:
            out += ["", "Reason", "", _display_reason(cycle)]
        if cycle.cycle_id or cycle.decision_record_id:
            out.append("")
            if cycle.cycle_id:
                out.append(f"Cycle\n{_short(cycle.cycle_id)}")
            if cycle.decision_record_id:
                out.append(f"Decision\n{_short(cycle.decision_record_id)}")

    out.append(bar)
    return "\n".join(out)


def banner_for(cycle: Cycle) -> str:
    action = (cycle.action or "").upper()
    if action == "HOLD":
        return c("🟡 HOLD cycle completed", YELLOW)
    if action == "SELL":
        return c("🎉 SELL completed successfully", BRIGHT_GREEN) if cycle.package_created else c("🔴 SELL blocked", BRIGHT_RED)
    if action == "BUY":
        if cycle.package_created:
            return c("🟣 Ready package created", MAGENTA)
        if cycle.package_skipped:
            return c(f"🔴 BUY blocked by {_display_reason(cycle)}", BRIGHT_RED)
        return c("🟢 BUY signal seen, awaiting resolution...", BRIGHT_GREEN)
    return c("🟢 Waiting for BUY signal...", BRIGHT_GREEN)


# --- Totals / runtime ---


@dataclass
class Totals:
    cycles: int = 0
    buys: int = 0
    sells: int = 0
    holds: int = 0
    blocked: int = 0
    completed: int = 0
    start_time: float = field(default_factory=time.time)
    last_cycle: str | None = None
    last_buy: str | None = None
    last_sell: str | None = None

    def record(self, cycle: Cycle) -> None:
        self.cycles += 1
        self.last_cycle = cycle.time_str
        action = (cycle.action or "").upper()
        if action == "BUY":
            self.buys += 1
            self.last_buy = cycle.time_str
        elif action == "SELL":
            self.sells += 1
            self.last_sell = cycle.time_str
        elif action == "HOLD":
            self.holds += 1
        if cycle.package_created:
            self.completed += 1
        elif _final_reason(cycle):
            self.blocked += 1

    def render(self) -> str:
        uptime = int(time.time() - self.start_time)
        return (
            f"{BOLD}Cycles={self.cycles}  BUY={self.buys}  SELL={self.sells}  HOLD={self.holds}  "
            f"Blocked={self.blocked}  Completed={self.completed}{RESET}  "
            f"{DIM}| uptime={uptime}s last_cycle={self.last_cycle or '-'} "
            f"last_buy={self.last_buy or '-'} last_sell={self.last_sell or '-'}{RESET}"
        )


# --- Main loop ---
# Runs forever: connects to journalctl -f, renders a card per completed
# cycle, and transparently reconnects on any disconnect (worker restart,
# journald hiccup, transient error) until the operator hits Ctrl+C.

_RECONNECT_DELAY_SECONDS = 5


def print_startup_banner() -> None:
    bar = "═" * 49
    print(bar)
    print(c("OmniTrade Operator Console", BOLD))
    print()
    print("Watching:")
    print(SERVICE_UNIT)
    print()
    print("Status:")
    print(c("🟢 Connected", BRIGHT_GREEN))
    print(f"Display timezone: {_display_timezone_label()}")
    print()
    print("Waiting for next trading cycle...")
    print()
    print("Press Ctrl+C to exit.")
    print(bar)
    print()


def follow_journal(on_line) -> None:
    """Runs one journalctl -f session, feeding every line to on_line, until
    the stream ends for any reason -- then raises so the caller reconnects."""
    proc = subprocess.Popen(JOURNAL_CMD, stdout=subprocess.PIPE, text=True, bufsize=1)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line)
        raise ConnectionError("journalctl stream ended")
    finally:
        proc.terminate()


def run() -> None:
    print_startup_banner()

    totals = Totals()
    current = Cycle()

    def flush() -> None:
        nonlocal current
        if not current.has_content():
            return
        totals.record(current)
        print(totals.render())
        print(banner_for(current))
        print(render_card(current))
        print()
        print(c("(waiting...)", DIM))
        print()
        current = Cycle()

    def on_line(line: str) -> None:
        nonlocal current
        parsed = parse_line(line)
        if parsed is None:
            return
        time_str, event, fields = parsed
        if event == "strategy_aggregate_completed":
            flush()
        current.absorb(event, fields, time_str)
        if event in _TERMINAL_EVENTS:
            flush()

    try:
        while True:
            try:
                follow_journal(on_line)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(c(f"⚠ Lost journal stream: {exc}", BRIGHT_RED))
                print(c(f"Reconnecting in {_RECONNECT_DELAY_SECONDS} seconds...", DIM))
                time.sleep(_RECONNECT_DELAY_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        flush()
        print(c("Operator Console stopped. No OmniTrade state was modified.", DIM))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only OmniTrade orchestration journal console")
    parser.add_argument(
        "--timezone",
        metavar="IANA_ZONE",
        help="display timezone, e.g. America/New_York (overrides OMNITRADE_OPERATOR_TIMEZONE and TZ)",
    )
    args = parser.parse_args(argv)
    try:
        _configure_display_timezone(args.timezone)
    except ValueError as exc:
        parser.error(str(exc))
    run()


if __name__ == "__main__":
    main()
