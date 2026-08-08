"""Observational PROCESS-stage trace events for the INPUT -> PROCESS -> OUTPUT
debugging plan.

This module is deliberately inert: it only formats plain, JSON-safe dicts. It
never touches the database, never raises (a malformed value is coerced to a
string rather than propagated), and its return value is never consulted by
any caller to decide what happens next. That is what makes it safe to splice
into the real campaign-cycle composition (authoritative.py) and the real
BUY_LIMIT entry-attempt worker (autonomous_limit_entry_worker.py) inline,
without risk of a trace-emission bug changing a trading decision: a caller
that stops reading this module's output entirely would still trade exactly
the same way.

Every existing gate/check keeps its own name, reason vocabulary, and
accept/reject outcome untouched -- this module only adds a second, uniform
label (`process_stage`) on top of whatever evidence a call site already
computed, and a place (the `process_trace` list already threaded into
`AutonomousCycleRun.cycle_context` / `AutonomousLimitEntryAttempt.
evidence_provenance`) to persist it point-in-time.

See docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md for the
gates this currently instruments (a narrow, BUY-only slice); the top-level
stage vocabulary below is the conceptual INPUT/PROCESS/OUTPUT model, not a
new subsystem.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

STAGE_OBSERVE_MARKET = "OBSERVE_MARKET"
STAGE_DETERMINE_MARKET_STATE = "DETERMINE_MARKET_STATE"
STAGE_DETERMINE_OPPORTUNITY = "DETERMINE_OPPORTUNITY"
STAGE_CONSTRUCT_TRADE = "CONSTRUCT_TRADE"
STAGE_AUTHORIZE_TRADE = "AUTHORIZE_TRADE"
STAGE_EXECUTE = "EXECUTE"
STAGE_MONITOR = "MONITOR"
STAGE_EXIT = "EXIT"
STAGE_RETURN_CAPITAL = "RETURN_CAPITAL"

PROCESS_STAGES = (
    STAGE_OBSERVE_MARKET,
    STAGE_DETERMINE_MARKET_STATE,
    STAGE_DETERMINE_OPPORTUNITY,
    STAGE_CONSTRUCT_TRADE,
    STAGE_AUTHORIZE_TRADE,
    STAGE_EXECUTE,
    STAGE_MONITOR,
    STAGE_EXIT,
    STAGE_RETURN_CAPITAL,
)

# The dict key this trace is stored under wherever it is embedded (campaign
# cycle composition, attempt evidence_provenance) -- one constant so every
# reader (future UI/API consumer included) agrees on where to look.
PROCESS_TRACE_KEY = "process_trace"

_SCHEMA_VERSION = "v1"


def _safe_scalar_str(value: Decimal | str | int | None) -> str | None:
    """Formats a Decimal (repo convention: `format(value, "f")`, never a raw
    float, to avoid float-precision drift in persisted evidence) or passes
    through an already-stringlike value. Never raises: an unexpected type
    degrades to `str(value)` rather than blowing up a trace call site that
    sits inline in a real trading decision path."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        try:
            return format(value, "f")
        except Exception:
            return str(value)
    if isinstance(value, (str, int)):
        return str(value)
    try:
        return str(value)
    except Exception:
        return None


def _safe_identity_str(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


def build_process_trace_event(
    *,
    process_stage: str,
    gate: str,
    verdict: str,
    reason: str | None,
    now: datetime,
    instrument: str | None = None,
    candidate_id: str | None = None,
    decision_record_id: UUID | str | None = None,
    attempt_id: UUID | str | None = None,
    cycle_id: UUID | str | None = None,
    observed_value: Decimal | str | None = None,
    threshold: Decimal | str | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    """Builds one structured PROCESS trace event. Pure formatting only --
    every value here is either already computed by the real gate/check at
    the call site, or a plain label describing it. This function is never
    the source of a trading decision; it only describes one that was already
    made.

    `candidate_id` should reuse an existing stable identity (decision_record_id
    when available) rather than mint a new one -- callers are expected to
    pass `candidate_id=decision_record_id` (or a deterministic fallback) when
    a real decision_record_id is not yet resolved.
    """
    decision_record_id_str = _safe_identity_str(decision_record_id)
    return {
        "schema_version": _SCHEMA_VERSION,
        "process_stage": process_stage,
        "gate": gate,
        "verdict": verdict,
        "reason": reason,
        "instrument": instrument,
        "candidate_id": candidate_id or decision_record_id_str,
        "decision_record_id": decision_record_id_str,
        "attempt_id": _safe_identity_str(attempt_id),
        "cycle_id": _safe_identity_str(cycle_id),
        "observed_value": _safe_scalar_str(observed_value),
        "threshold": _safe_scalar_str(threshold),
        "next": next_step,
        "timestamp": now.isoformat(),
    }


def append_trace_event(evidence_provenance: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
    """Returns a NEW evidence_provenance dict with `event` appended to its
    process_trace list. Always reassigns (never mutates the list in place)
    so SQLAlchemy's ORM change-tracking on the JSONB column -- which does
    not observe in-place mutation of a plain dict/list without the Mutable
    extension -- reliably sees the write on the next flush."""
    base = dict(evidence_provenance or {})
    existing_trace = list(base.get(PROCESS_TRACE_KEY) or [])
    existing_trace.append(event)
    base[PROCESS_TRACE_KEY] = existing_trace
    return base
