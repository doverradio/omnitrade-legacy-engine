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
    assert "--utc" in oc.JOURNAL_BASE_CMD
    assert "--utc" in oc._build_journal_cmd(since_hours=None)


# --- --hours replay window (catch up after the console wasn't running) ---


def test_build_journal_cmd_without_since_hours_has_no_since_flag() -> None:
    """Default behavior (no --hours) is unchanged: live-only, no backlog."""
    cmd = oc._build_journal_cmd(since_hours=None)
    assert "--since" not in cmd
    assert cmd[-1] == "-f"


def test_build_journal_cmd_with_since_hours_computes_absolute_epoch_cutoff() -> None:
    """The cutoff is an absolute Unix-epoch instant computed here in Python,
    not a formatted date/timezone string handed to journalctl for it to
    parse itself. An earlier version used a "YYYY-MM-DD HH:MM:SS UTC"
    string; on at least one real VPS journalctl silently failed to honor
    that trailing "UTC" suffix and replayed nothing, with no error --
    "@<epoch>" removes that entire class of ambiguity (systemd's --since
    parser treats a leading '@' as an unambiguous Unix timestamp)."""
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc) - timedelta(hours=8)
    cmd = oc._build_journal_cmd(since_hours=8)
    assert "--since" in cmd
    since_value = cmd[cmd.index("--since") + 1]
    assert since_value.startswith("@")
    parsed = datetime.fromtimestamp(int(since_value[1:]), tz=timezone.utc)
    assert abs((parsed - before).total_seconds()) < 5
    assert cmd[-1] == "-f"


def test_resolve_since_cutoff_is_exactly_n_hours_before_now() -> None:
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff = oc._resolve_since_cutoff(since_hours=24)
    assert cutoff.tzinfo is not None
    assert abs((cutoff - before).total_seconds()) < 5


def test_follow_journal_surfaces_stderr_when_no_lines_were_produced(monkeypatch: pytest.MonkeyPatch) -> None:
    """If journalctl rejects its own arguments (bad --since value, missing
    permission, unsupported flag on an older version) and exits without
    emitting any lines, the reconnect warning must report journalctl's
    actual stderr -- not the generic 'stream ended' message that made a
    silently-broken --since indistinguishable from a genuinely quiet
    window."""
    monkeypatch.setattr(
        oc,
        "_build_journal_cmd",
        lambda *, since_hours=None: [
            "python3", "-c",
            "import sys; sys.stderr.write('boom: bad --since value\\n'); sys.exit(1)",
        ],
    )
    with pytest.raises(ConnectionError, match="boom: bad --since value"):
        oc.follow_journal(lambda _line: None, since_hours=8)


def test_hours_flag_is_parsed_and_forwarded_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(oc, "run", lambda *, since_hours=None: captured.update(since_hours=since_hours))
    oc.main(["--hours", "8"])
    assert captured["since_hours"] == 8.0


def test_omitting_hours_flag_preserves_default_live_only_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(oc, "run", lambda *, since_hours=None: captured.update(since_hours=since_hours))
    oc.main([])
    assert captured["since_hours"] is None


def test_nonpositive_hours_flag_is_rejected() -> None:
    with pytest.raises(SystemExit):
        oc.main(["--hours", "0"])
    with pytest.raises(SystemExit):
        oc.main(["--hours", "-5"])


def test_replay_window_is_used_only_for_the_first_connection_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient reconnect after the initial replay must resume live from
    'now', never re-replay the original overnight window again -- otherwise
    every journald hiccup would reprint hours of already-seen cycle cards."""
    seen_since_hours: list[float | None] = []
    call_count = {"n": 0}

    def _fake_follow_journal(on_line, *, since_hours=None) -> None:
        _ = on_line
        seen_since_hours.append(since_hours)
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise KeyboardInterrupt
        raise ConnectionError("simulated drop")

    monkeypatch.setattr(oc, "follow_journal", _fake_follow_journal)
    monkeypatch.setattr(oc.time, "sleep", lambda _seconds: None)

    oc.run(since_hours=8)

    assert seen_since_hours == [8, None]


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


def _absorb_lifecycle(state: oc.ProfitLifecycle, message: str) -> bool:
    line = f"2024-01-15T23:09:00+0000 host proc[1]: {message}"
    parsed = oc.parse_line(line)
    assert parsed is not None
    _time_str, event, fields = parsed
    return state.absorb(event, fields)


def test_lifecycle_requires_affirmative_buy_execution_events() -> None:
    state = oc.ProfitLifecycle()
    _absorb_lifecycle(state, "strategy_aggregate_completed action=BUY reason=buy_agreement_threshold_met")
    rendered = oc.render_profit_lifecycle(state)
    assert "Strategy BUY proposed" in rendered
    assert "Live Kraken BUY submitted: Not yet confirmed" in rendered
    assert "BUY filled: Not yet confirmed" in rendered
    assert "FIRST AUTONOMOUS PROFIT VERIFIED: Not yet confirmed" in rendered


def test_lifecycle_progresses_only_from_correlated_submission_and_fill_evidence() -> None:
    state = oc.ProfitLifecycle()
    events = [
        "strategy_aggregate_completed action=BUY reason=buy_agreement_threshold_met",
        "net_edge_evaluated final_reason_code=accepted expected_net_profit=0.01",
        "automatic_ready_package_created package_id=package-1 cycle_id=cycle-1",
        "automatic_package_activated package_id=package-1 activation_id=activation-1",
        "kraken_order_submission_started live_crypto_order_id=buy-1 provider=kraken environment=live product_id=BTC-USD side=BUY client_order_id=client-buy",
        "kraken_order_submitted live_crypto_order_id=buy-1 provider_order_id=provider-buy",
        "reconciliation_completed live_crypto_order_id=buy-1 reconciliation_status=filled provider_fill_observed=True",
        "autonomous_proof_sell_worker_cycle action=selected attempt_id=attempt-1 stage=SELECTED provider_call_made=False",
        "kraken_order_submission_started live_crypto_order_id=sell-1 provider=kraken environment=live product_id=BTC-USD side=SELL client_order_id=client-sell",
        "kraken_order_submitted live_crypto_order_id=sell-1 provider_order_id=provider-sell",
        "reconciliation_completed live_crypto_order_id=sell-1 reconciliation_status=filled provider_fill_observed=True",
    ]
    for event in events:
        assert _absorb_lifecycle(state, event) is True

    rendered = oc.render_profit_lifecycle(state)
    for milestone in (
        "Economics approved", "READY package created", "Package activated",
        "Live Kraken BUY submitted", "BUY filled", "BUY reconciled",
        "SELL candidate selected", "Live Kraken SELL submitted", "SELL filled", "SELL reconciled",
    ):
        assert any("✅" in line and milestone in line for line in rendered.splitlines())
    assert "Risk explicitly approved: Not yet confirmed" in rendered
    assert "BUY accounting completed: Not yet confirmed" in rendered
    assert "Eligible autonomous custody established: Not yet confirmed" in rendered
    assert "Realized gross P&L, total costs, and realized net P&L: Not yet confirmed" in rendered
    assert state.positive_profit_verified is False


def test_lifecycle_replay_is_idempotent() -> None:
    state = oc.ProfitLifecycle()
    event = "kraken_order_submission_started live_crypto_order_id=buy-1 side=BUY"
    assert _absorb_lifecycle(state, event) is True
    assert _absorb_lifecycle(state, event) is False
    assert state.buy_order_id == "buy-1"
    assert len(state.seen_event_keys) == 1


def test_lifecycle_rejects_events_from_another_product() -> None:
    state = oc.ProfitLifecycle(product_id="BTC-USD")
    assert _absorb_lifecycle(
        state,
        "strategy_aggregate_completed product_id=ETH-USD action=BUY reason=buy_agreement_threshold_met",
    ) is False
    assert _absorb_lifecycle(
        state,
        "kraken_order_submission_started product_id=SOL-USD live_crypto_order_id=sol-buy side=BUY",
    ) is False
    assert state.strategy_buy_proposed is False
    assert state.buy_order_id is None


def test_unrelated_or_unfilled_reconciliation_does_not_claim_completion() -> None:
    state = oc.ProfitLifecycle()
    _absorb_lifecycle(state, "kraken_order_submission_started live_crypto_order_id=buy-1 side=BUY")
    _absorb_lifecycle(state, "reconciliation_completed live_crypto_order_id=other reconciliation_status=filled provider_fill_observed=True")
    _absorb_lifecycle(state, "reconciliation_completed live_crypto_order_id=buy-1 reconciliation_status=pending provider_fill_observed=False")
    assert state.buy_filled is False
    assert state.buy_reconciled is False


def test_sell_package_never_means_sell_execution_or_profit() -> None:
    cycle = oc.Cycle(action="SELL", package_created=True)
    assert "completed successfully" not in oc.banner_for(cycle)

    state = oc.ProfitLifecycle()
    _absorb_lifecycle(state, "automatic_ready_package_created package_id=sell-package")
    rendered = oc.render_profit_lifecycle(state)
    assert "Live Kraken SELL submitted: Not yet confirmed" in rendered
    assert "FIRST AUTONOMOUS PROFIT VERIFIED: Not yet confirmed" in rendered


def test_positive_profit_banner_requires_affirmative_positive_realized_net_pnl_evidence() -> None:
    state = oc.ProfitLifecycle(realized_gross_pnl="0.25", total_costs="0.05", realized_net_pnl="0")
    assert "🎉 FIRST AUTONOMOUS PROFIT VERIFIED" not in oc.render_profit_lifecycle(state)
    state.realized_net_pnl = "-0.01"
    assert "🎉 FIRST AUTONOMOUS PROFIT VERIFIED" not in oc.render_profit_lifecycle(state)
    state.realized_net_pnl = "0.20"
    assert "🎉 FIRST AUTONOMOUS PROFIT VERIFIED" not in oc.render_profit_lifecycle(state)
    state.realized_pnl_evidence_observed = True
    assert "🎉 FIRST AUTONOMOUS PROFIT VERIFIED" in oc.render_profit_lifecycle(state)
