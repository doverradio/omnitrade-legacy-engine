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


@pytest.fixture(autouse=True)
def _clean_tz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)


def test_journalctl_invoked_with_utc_flag() -> None:
    """Root cause of the display bug: journalctl without --utc renders in
    whatever timezone the VPS's OS happens to be configured with, which the
    console then displayed unconverted. --utc makes the source unambiguous."""
    assert "--utc" in oc.JOURNAL_CMD


def test_respects_operator_configured_tz_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    time_str, _event, _fields = oc.parse_line(_LINE)
    assert time_str == "6:09 PM"  # 23:09 UTC -> EST (UTC-5) in January


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
