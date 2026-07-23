"""Regression tests for tools/operator_console.py's timestamp display fix.

Run: python3 -m pytest tools/test_operator_console.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import operator_console as oc  # noqa: E402

_LINE = "2024-01-15T23:09:00+0000 host proc[1]: strategy_aggregate_completed action=HOLD reason=strategy_hold_signal"
_LINE_SUMMER = "2024-07-15T23:09:00+0000 host proc[1]: strategy_aggregate_completed action=HOLD reason=strategy_hold_signal"
_LINE_EQUIVALENT_OFFSET = "2024-01-16T04:09:00+0500 host proc[1]: strategy_aggregate_completed action=HOLD reason=strategy_hold_signal"


@pytest.fixture(autouse=True)
def _clean_tz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("OMNITRADE_OPERATOR_TIMEZONE", raising=False)


def test_journalctl_invoked_with_utc_flag() -> None:
    """Root cause of the display bug: journalctl without --utc renders in
    whatever timezone the VPS's OS happens to be configured with, which the
    console then displayed unconverted. --utc makes the source unambiguous."""
    assert "--utc" in oc.JOURNAL_CMD


def test_respects_operator_configured_tz_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    time_str, _event, _fields = oc.parse_line(_LINE)
    assert time_str == "6:09 PM"  # 23:09 UTC -> EST (UTC-5) in January


def test_omnitrade_operator_timezone_takes_precedence_over_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNITRADE_OPERATOR_TIMEZONE is the OmniTrade-level operator setting;
    it must win even if TZ happens to be set to something else."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setenv("OMNITRADE_OPERATOR_TIMEZONE", "America/New_York")
    time_str, _event, _fields = oc.parse_line(_LINE)
    assert time_str == "6:09 PM"  # America/New_York wins, not Asia/Kolkata


def test_command_line_timezone_configures_operator_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    oc._configure_display_timezone("America/New_York")
    time_str, _event, _fields = oc.parse_line(_LINE)
    assert time_str == "6:09 PM"
    assert oc._display_timezone_label() == "America/New_York"


def test_invalid_explicit_timezone_fails_instead_of_silently_using_vps_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNITRADE_OPERATOR_TIMEZONE", "Not/A_Real_Zone")
    with pytest.raises(ValueError, match="Invalid IANA timezone in OMNITRADE_OPERATOR_TIMEZONE"):
        oc._display_timezone()


def test_source_numeric_offset_is_parsed_not_assumed_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """04:09 at +05:00 and 23:09 at +00:00 are the same instant. Both
    must render identically, proving the parser honors the journal offset
    and the display layer converts exactly once."""
    monkeypatch.setenv("OMNITRADE_OPERATOR_TIMEZONE", "America/New_York")
    utc_time, _event, _fields = oc.parse_line(_LINE)
    offset_time, _event, _fields = oc.parse_line(_LINE_EQUIVALENT_OFFSET)
    assert utc_time == offset_time == "6:09 PM"


def test_dst_is_handled_without_a_hardcoded_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same wall-clock UTC time, different calendar month: EST (winter) and
    EDT (summer) must differ by exactly one hour. A hardcoded offset would
    get one of these two wrong."""
    monkeypatch.setenv("TZ", "America/New_York")
    winter_time_str, _e, _f = oc.parse_line(_LINE)
    summer_time_str, _e, _f = oc.parse_line(_LINE_SUMMER)
    assert winter_time_str == "6:09 PM"  # EST = UTC-5
    assert summer_time_str == "7:09 PM"  # EDT = UTC-4


def test_falls_back_to_os_local_timezone_when_tz_unset() -> None:
    """With no TZ env var, conversion still occurs (never raw UTC digits
    passed straight through) -- it uses whatever the OS's own local
    timezone is, which in a correctly configured environment matches the
    operator's actual wall-clock time even though this test can't assert a
    specific numeric offset (the test machine's local tz is unknown)."""
    time_str, _event, _fields = oc.parse_line(_LINE)
    from datetime import datetime, timezone as _tz

    dt_utc = datetime(2024, 1, 15, 23, 9, 0, tzinfo=_tz.utc)
    expected_local = dt_utc.astimezone()
    hour12 = expected_local.hour % 12 or 12
    period = "AM" if expected_local.hour < 12 else "PM"
    assert time_str == f"{hour12}:{expected_local.minute:02d} {period}"


def test_unparseable_timestamp_falls_back_to_utc_now_not_a_crash() -> None:
    """A line matching an EVENT_NAME but with no leading ISO timestamp (e.g.
    a malformed/truncated journal line) must not raise -- it falls back to
    the current UTC instant, still going through the same UTC->display
    conversion, rather than skipping conversion entirely."""
    line = "strategy_aggregate_completed action=HOLD reason=strategy_hold_signal"
    result = oc.parse_line(line)
    assert result is not None
    time_str, _event, _fields = result
    assert time_str.endswith("AM") or time_str.endswith("PM")


def test_cards_and_totals_reuse_the_same_converted_event_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The card and every last-cycle summary are the console's only timestamp
    displays; they must reuse parse_line's single conversion policy."""
    monkeypatch.setenv("OMNITRADE_OPERATOR_TIMEZONE", "America/New_York")
    time_str, event, fields = oc.parse_line(_LINE)
    cycle = oc.Cycle()
    cycle.absorb(event, fields, time_str)
    totals = oc.Totals()
    totals.record(cycle)

    assert time_str == "6:09 PM"
    assert "6:09 PM" in oc.render_card(cycle)
    assert "last_cycle=6:09 PM" in totals.render()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("automatic_package_authorized_under_mandate", "Mandate Authorized"),
        ("automatic_package_dry_run_passed", "Dry Run Passed"),
        ("automatic_package_activated", "Package Activated"),
    ],
)
def test_ep3_progression_events_render_without_execution_claims(event: str, expected: str) -> None:
    line = f"2024-01-15T23:09:00+0000 host proc[1]: {event} package_id=11111111-1111-1111-1111-111111111111 decision_record_id=22222222-2222-2222-2222-222222222222"
    time_str, parsed_event, fields = oc.parse_line(line)
    cycle = oc.Cycle()
    cycle.absorb(parsed_event, fields, time_str)
    rendered = oc.render_card(cycle)
    assert expected in rendered
    assert "submitted" not in rendered.lower()
    assert "filled" not in rendered.lower()
    assert "position opened" not in rendered.lower()


def test_ep3_failed_closed_reason_is_displayed() -> None:
    line = "2024-01-15T23:09:00+0000 host proc[1]: automatic_package_progression_failed_closed package_id=11111111-1111-1111-1111-111111111111 reason=mandate_expired failed_closed=True"
    time_str, event, fields = oc.parse_line(line)
    cycle = oc.Cycle()
    cycle.absorb(event, fields, time_str)
    assert "mandate_expired" in oc.render_card(cycle)
