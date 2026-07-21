#!/usr/bin/env python3
"""OmniTrade Operator Console -- a read-only, real-time dashboard over the
orchestration worker's journalctl output.

NOT production code. Never writes to OmniTrade's database, config, or
services -- it only shells out to `journalctl -f` (read-only) and prints a
human-readable summary of what it reads. Safe to run alongside production
at any time; killing it (Ctrl-C) has zero effect on the orchestration worker.

Run:
    python tools/operator_console.py

Standard library only, no external dependencies.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime

SERVICE_UNIT = "omnitrade-orchestration.service"
JOURNAL_CMD = ["sudo", "journalctl", "-u", SERVICE_UNIT, "-f", "-o", "short-iso"]

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
_TIME_RE = re.compile(r"^\S*?T(\d{2}):(\d{2})")
# key=value, where value may be a bracketed/parenthesized/quoted group
# (handles rejected_candidates=[('BTC-USD', 'reason')], rejection_reasons=["x"]).
_KV_RE = re.compile(r'(\w+)=(\[[^\]]*\]|\([^)]*\)|"[^"]*"|\S+)')
_TERMINAL_EVENTS = {"automatic_ready_package_created", "automatic_ready_package_skipped"}


def _format_time(hour: str, minute: str) -> str:
    hour_int = int(hour)
    period = "AM" if hour_int < 12 else "PM"
    hour12 = hour_int % 12 or 12
    return f"{hour12}:{minute} {period}"


def parse_line(line: str) -> tuple[str, str, dict[str, str]] | None:
    """Return (time_of_day, event_name, fields), or None if this line isn't
    one of EVENT_NAMES. Tolerant of whatever prefix journalctl/python
    logging put in front of the message -- only the event keyword and what
    follows it are used, so the log format can change without breaking this."""
    match = _EVENT_RE.search(line)
    if match is None:
        return None
    event_name, payload = match.group(1), match.group(2)
    time_match = _TIME_RE.match(line)
    time_str = _format_time(*time_match.groups()) if time_match else _format_time(*datetime.now().strftime("%H %M").split())
    fields = {key: value.strip('"') for key, value in _KV_RE.findall(payload)}
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
    skip_reason: str | None = None
    underlying_reason: str | None = None
    reconciliation_triggered: bool = False
    reconciliation_records: int = 0

    def has_content(self) -> bool:
        return self.action is not None or self.cycle_id is not None

    def absorb(self, event: str, f: dict[str, str], time_str: str) -> None:
        if not self.time_str:
            self.time_str = time_str
        if event == "strategy_aggregate_completed":
            self.action = f.get("action", self.action)
            self.selected_decision_reason = f.get("reason", self.selected_decision_reason)
        elif event in ("net_edge_evaluated", "non_positive_net_edge_rejection_explained"):
            self.instrument = f.get("instrument") or f.get("asset") or self.instrument
            self.strategy_identity = f.get("strategy_identity") or f.get("strategy_id") or self.strategy_identity
            self.expected_gross_edge_pct = f.get("expected_gross_edge_pct", self.expected_gross_edge_pct)
            self.expected_net_edge_pct = f.get("expected_net_edge_pct", self.expected_net_edge_pct)
            self.expected_net_profit = f.get("expected_net_profit") or f.get("expected_net_dollars") or self.expected_net_profit
            self.fees_amount = f.get("round_trip_fee_amount") or f.get("fees_dollars") or self.fees_amount
            self.final_reason_code = f.get("final_reason_code", self.final_reason_code)
        elif event == "campaign_cycle_termination_resolved":
            self.decision_kind = f.get("decision_kind", self.decision_kind)
            self.proposed_action = f.get("proposed_action", self.proposed_action)
            self.selected_decision_reason = f.get("selected_decision_reason") or self.selected_decision_reason
        elif event == "automatic_ready_package_created":
            self.cycle_id = f.get("cycle_id", self.cycle_id)
            self.decision_record_id = f.get("decision_record_id", self.decision_record_id)
            self.package_id = f.get("package_id", self.package_id)
            self.package_created = True
        elif event == "automatic_ready_package_skipped":
            self.cycle_id = f.get("cycle_id", self.cycle_id)
            self.decision_record_id = f.get("decision_record_id", self.decision_record_id)
            self.skip_reason = f.get("reason", self.skip_reason)
            self.underlying_reason = f.get("underlying_reason", self.underlying_reason)
        elif event == "unresolved_reconciliation_gate_triggered":
            self.reconciliation_triggered = True
            self.reconciliation_records = int(f.get("matched_record_count", "0") or 0)
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


def _final_reason(cycle: Cycle) -> str | None:
    """Why this cycle didn't end in a ready package. None if a package WAS
    created (nothing left to explain)."""
    if cycle.package_created:
        return None
    return cycle.underlying_reason or cycle.skip_reason


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
        out += [c("🟡 HOLD", YELLOW), "", "Reason", "", cycle.selected_decision_reason or cycle.underlying_reason or "unknown", "", "No package created."]
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
        reason = _final_reason(cycle)
        if reason:
            out += ["", "Reason", "", reason]
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
        reason = _final_reason(cycle)
        if reason:
            return c(f"🔴 BUY blocked by {reason}", BRIGHT_RED)
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


if __name__ == "__main__":
    run()
