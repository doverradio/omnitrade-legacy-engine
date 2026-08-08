from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.services.orchestration.process_trace import (
    PROCESS_STAGES,
    PROCESS_TRACE_KEY,
    STAGE_DETERMINE_OPPORTUNITY,
    append_trace_event,
    build_process_trace_event,
)

_NOW = datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)


def test_build_process_trace_event_shape_matches_conceptual_schema() -> None:
    decision_record_id = uuid4()
    event = build_process_trace_event(
        process_stage=STAGE_DETERMINE_OPPORTUNITY,
        gate="net_edge_gate",
        verdict="REJECT",
        reason="non_positive_net_edge",
        now=_NOW,
        instrument="BTC-USD",
        decision_record_id=decision_record_id,
        observed_value=Decimal("-0.0018"),
        threshold=Decimal("0"),
    )
    assert event["process_stage"] == "DETERMINE_OPPORTUNITY"
    assert event["gate"] == "net_edge_gate"
    assert event["verdict"] == "REJECT"
    assert event["reason"] == "non_positive_net_edge"
    assert event["instrument"] == "BTC-USD"
    assert event["decision_record_id"] == str(decision_record_id)
    # candidate_id defaults to decision_record_id when no explicit candidate_id given.
    assert event["candidate_id"] == str(decision_record_id)
    assert event["observed_value"] == "-0.0018"
    assert event["threshold"] == "0"
    assert event["timestamp"] == _NOW.isoformat()


def test_build_process_trace_event_never_raises_on_odd_input() -> None:
    class _Unformattable:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    event = build_process_trace_event(
        process_stage=STAGE_DETERMINE_OPPORTUNITY,
        gate="weird_gate",
        verdict="PASS",
        reason=None,
        now=_NOW,
        observed_value=_Unformattable(),
    )
    assert event["observed_value"] is None


def test_candidate_id_falls_back_when_no_decision_record_id() -> None:
    event = build_process_trace_event(
        process_stage=STAGE_DETERMINE_OPPORTUNITY,
        gate="market_evidence_gate",
        verdict="REJECT",
        reason="market_data_unavailable",
        now=_NOW,
        candidate_id="BTC-USD:campaign-1:1:2026-08-07T21:00:00+00:00",
    )
    assert event["candidate_id"] == "BTC-USD:campaign-1:1:2026-08-07T21:00:00+00:00"
    assert event["decision_record_id"] is None


def test_append_trace_event_never_mutates_input_and_always_returns_new_dict() -> None:
    original = {"strategy_identity": "ma_crossover@1"}
    event_one = build_process_trace_event(process_stage=STAGE_DETERMINE_OPPORTUNITY, gate="g1", verdict="PASS", reason=None, now=_NOW)
    updated_once = append_trace_event(original, event_one)

    assert original == {"strategy_identity": "ma_crossover@1"}
    assert PROCESS_TRACE_KEY not in original
    assert updated_once[PROCESS_TRACE_KEY] == [event_one]
    assert updated_once["strategy_identity"] == "ma_crossover@1"

    event_two = build_process_trace_event(process_stage=STAGE_DETERMINE_OPPORTUNITY, gate="g2", verdict="PASS", reason=None, now=_NOW)
    updated_twice = append_trace_event(updated_once, event_two)
    assert updated_twice[PROCESS_TRACE_KEY] == [event_one, event_two]
    # First reassignment's list is untouched by the second append.
    assert updated_once[PROCESS_TRACE_KEY] == [event_one]


def test_append_trace_event_handles_none_evidence_provenance() -> None:
    event = build_process_trace_event(process_stage=STAGE_DETERMINE_OPPORTUNITY, gate="g", verdict="PASS", reason=None, now=_NOW)
    result = append_trace_event(None, event)
    assert result[PROCESS_TRACE_KEY] == [event]


def test_all_nine_process_stages_defined_in_order() -> None:
    assert PROCESS_STAGES == (
        "OBSERVE_MARKET",
        "DETERMINE_MARKET_STATE",
        "DETERMINE_OPPORTUNITY",
        "CONSTRUCT_TRADE",
        "AUTHORIZE_TRADE",
        "EXECUTE",
        "MONITOR",
        "EXIT",
        "RETURN_CAPITAL",
    )
