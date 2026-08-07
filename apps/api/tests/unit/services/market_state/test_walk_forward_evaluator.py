"""Unit tests for the deterministic walk-forward Market State evaluation
harness.

Research/replay layer only -- see
`app/services/market_state/walk_forward_evaluator.py` and
`docs/MARKET_STATE_WALK_FORWARD_EVALUATION.md`. Pure function tests only; no
database, no network, no async fixtures.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.services.market_state.walk_forward_evaluator as walk_forward_evaluator_module
from app.services.market_state import DirectionState, MarketStateClassifierParams, classify_market_state
from app.services.market_state.walk_forward_contracts import (
    EvidenceStatus,
    StateDimension,
    WalkForwardBar,
    WalkForwardEvaluationParams,
)
from app.services.market_state.walk_forward_evaluator import (
    DEFAULT_WALK_FORWARD_PARAMS,
    EVALUATOR_VERSION,
    run_walk_forward_evaluation,
)

_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bars(closes: list[str], volumes: list[str] | None = None, *, step_minutes: int = 1) -> list[WalkForwardBar]:
    if volumes is None:
        volumes = ["1000"] * len(closes)
    assert len(closes) == len(volumes)
    result = []
    for i, (close_str, volume_str) in enumerate(zip(closes, volumes)):
        close = Decimal(close_str)
        result.append(
            WalkForwardBar(
                open_time=_BASE_TIME + timedelta(minutes=step_minutes * i),
                open=close,
                high=close + Decimal("0.01"),
                low=close - Decimal("0.01"),
                close=close,
                volume=Decimal(volume_str),
            )
        )
    return result


def _flat_ranging_bars_for_forward_return_test() -> list[WalkForwardBar]:
    """10 bars, 5-bar windows all classify RANGING (net_return well inside
    the default +-0.60% threshold); designed so the horizon=1 forward
    returns and their aggregate statistics are exactly hand-verifiable
    (see the module docstring test below for the derivation)."""

    closes = ["100.00", "100.05", "99.98", "100.02", "100.00", "100.10", "99.90", "100.20", "99.80", "100.30"]
    return _bars(closes)


def _three_block_direction_bars() -> list[WalkForwardBar]:
    """15 bars in 3 non-overlapping 5-bar blocks: a strong uptrend, a
    strong downtrend, and a flat/ranging block -- with window=5, step=5,
    this yields exactly 3 evaluation points, one per direction state, with
    hand-verifiable frequencies/transitions/persistence."""

    block1 = ["100", "110", "120", "130", "140"]  # net +40% -> TRENDING_UP
    block2 = ["140", "120", "100", "80", "60"]  # net ~-57.14% -> TRENDING_DOWN
    block3 = ["60", "60.1", "60", "60.1", "60"]  # net 0% -> RANGING
    return _bars(block1 + block2 + block3)


_SMALL_WINDOW_PARAMS = MarketStateClassifierParams(min_bars=5)


# ---------------------------------------------------------------------------
# Basic chronological walk-forward evaluation
# ---------------------------------------------------------------------------


def test_walk_forward_evaluation_produces_expected_evaluation_indices() -> None:
    bars = _bars([str(100 + i) for i in range(50)])
    params = WalkForwardEvaluationParams(classification_window_bars=20, evaluation_step_size_bars=5)

    result = run_walk_forward_evaluation(bars, walk_forward_params=params)

    expected_indices = list(range(19, 50, 5))  # window-1=19, step=5, up to len(bars)-1
    assert [obs.evaluation_index for obs in result.observations] == expected_indices


def test_evaluation_time_matches_the_bar_at_evaluation_index() -> None:
    bars = _bars([str(100 + i) for i in range(30)])
    result = run_walk_forward_evaluation(bars, walk_forward_params=DEFAULT_WALK_FORWARD_PARAMS)

    for obs in result.observations:
        assert obs.evaluation_time == bars[obs.evaluation_index].open_time


def test_result_carries_version_and_config_metadata() -> None:
    bars = _bars([str(100 + i) for i in range(25)])
    result = run_walk_forward_evaluation(bars)

    assert result.evaluator_version == EVALUATOR_VERSION
    assert result.classifier_version == "market_state_deterministic_v1"
    assert result.total_bars_supplied == 25
    assert result.walk_forward_params == DEFAULT_WALK_FORWARD_PARAMS


# ---------------------------------------------------------------------------
# Canonical classifier reuse (no duplicated classification logic)
# ---------------------------------------------------------------------------


def test_recorded_state_matches_calling_the_canonical_classifier_directly() -> None:
    """Black-box equivalence: the state recorded for each evaluation point
    must be identical to calling `classify_market_state` directly on the
    same window -- proving the evaluator delegates rather than
    reimplementing classification math."""

    bars = _bars([str(100 + (i % 7) - 3) for i in range(40)], [str(1000 + 17 * i) for i in range(40)])
    result = run_walk_forward_evaluation(bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20))

    for obs in result.observations:
        window = [
            b.to_ohlcv_bar() for b in bars[obs.evaluation_index - 20 + 1 : obs.evaluation_index + 1]
        ]
        direct_state = classify_market_state(window)
        assert obs.direction_state == direct_state.direction_state
        assert obs.volatility_state == direct_state.volatility_state
        assert obs.participation_state == direct_state.participation_state
        assert obs.confidence == direct_state.confidence
        assert obs.reason_codes == direct_state.reason_codes


def test_evaluator_calls_classify_market_state_exactly_once_per_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spy on the imported `classify_market_state` reference to confirm the
    evaluator actually delegates to it (call count matches observation
    count) rather than computing states some other way."""

    call_count = 0
    real_classify = walk_forward_evaluator_module.classify_market_state

    def _spy(*args: object, **kwargs: object):
        nonlocal call_count
        call_count += 1
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(walk_forward_evaluator_module, "classify_market_state", _spy)

    bars = _bars([str(100 + i) for i in range(45)])
    result = run_walk_forward_evaluation(bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, evaluation_step_size_bars=5))

    assert call_count == len(result.observations)
    assert call_count > 0


# ---------------------------------------------------------------------------
# No-lookahead behavior
# ---------------------------------------------------------------------------


def test_state_at_evaluation_time_is_unaffected_by_divergent_futures() -> None:
    """Paired no-lookahead test: two datasets share identical history
    through evaluation time T; their futures differ. The state recorded at
    T must be identical; their future-return outcomes may differ."""

    history = _bars([str(100 + i) for i in range(30)])
    future_bull = _bars([str(130 + 5 * i) for i in range(1, 6)], step_minutes=1)
    future_bear = _bars([str(130 - 5 * i) for i in range(1, 6)], step_minutes=1)

    # Re-stamp the futures to continue chronologically after `history`.
    def _continue_from(base: list[WalkForwardBar], extension: list[WalkForwardBar]) -> list[WalkForwardBar]:
        start_time = base[-1].open_time
        continued = []
        for i, bar in enumerate(extension):
            continued.append(dataclasses.replace(bar, open_time=start_time + timedelta(minutes=i + 1)))
        return base + continued

    timeline_bull = _continue_from(history, future_bull)
    timeline_bear = _continue_from(history, future_bear)

    params = WalkForwardEvaluationParams(classification_window_bars=20, forward_horizons_bars=(1, 4))
    result_bull = run_walk_forward_evaluation(timeline_bull, walk_forward_params=params)
    result_bear = run_walk_forward_evaluation(timeline_bear, walk_forward_params=params)

    # The observation evaluated at the last bar of `history` (index 29)
    # exists identically in both timelines and must have an identical
    # frozen state...
    obs_bull = next(o for o in result_bull.observations if o.evaluation_index == 29)
    obs_bear = next(o for o in result_bear.observations if o.evaluation_index == 29)

    assert obs_bull.direction_state == obs_bear.direction_state
    assert obs_bull.volatility_state == obs_bear.volatility_state
    assert obs_bull.participation_state == obs_bear.participation_state
    assert obs_bull.confidence == obs_bear.confidence
    assert obs_bull.reason_codes == obs_bear.reason_codes

    # ...but its forward returns, measured strictly after the state was
    # frozen, are free to (and here, do) differ, since they depend on the
    # diverging future.
    assert obs_bull.forward_returns_by_horizon != obs_bear.forward_returns_by_horizon


def test_classification_window_never_includes_bars_after_evaluation_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly inspect every window handed to the classifier and confirm
    every bar in it has an index <= the evaluation index."""

    recorded_windows: list[list[object]] = []
    real_classify = walk_forward_evaluator_module.classify_market_state

    def _spy(candles, **kwargs):  # type: ignore[no-untyped-def]
        recorded_windows.append(list(candles))
        return real_classify(candles, **kwargs)

    monkeypatch.setattr(walk_forward_evaluator_module, "classify_market_state", _spy)

    bars = _bars([str(100 + i) for i in range(40)])
    params = WalkForwardEvaluationParams(classification_window_bars=20, evaluation_step_size_bars=3)
    run_walk_forward_evaluation(bars, walk_forward_params=params)

    all_ohlcv_bars = [b.to_ohlcv_bar() for b in bars]
    for window in recorded_windows:
        assert len(window) == 20
        # Every bar in the window must come from a contiguous prefix of the
        # full series -- i.e. the window's last bar's position in the full
        # OHLCV series must be reachable without skipping past any bar not
        # yet seen. We verify this by confirming the window is exactly
        # some `all_ohlcv_bars[i - 19 : i + 1]` slice.
        assert window in [all_ohlcv_bars[i - 19 : i + 1] for i in range(19, 40)]


# ---------------------------------------------------------------------------
# Exact classification-window boundaries
# ---------------------------------------------------------------------------


def test_classification_window_is_exactly_the_trailing_n_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_windows: list[list[object]] = []
    real_classify = walk_forward_evaluator_module.classify_market_state

    def _spy(candles, **kwargs):  # type: ignore[no-untyped-def]
        recorded_windows.append(list(candles))
        return real_classify(candles, **kwargs)

    monkeypatch.setattr(walk_forward_evaluator_module, "classify_market_state", _spy)

    bars = _bars([str(100 + i) for i in range(25)])
    params = WalkForwardEvaluationParams(classification_window_bars=20, evaluation_step_size_bars=1)
    run_walk_forward_evaluation(bars, walk_forward_params=params)

    assert len(recorded_windows) == 6  # evaluation indices 19..24
    expected_first_window = [b.to_ohlcv_bar() for b in bars[0:20]]
    expected_last_window = [b.to_ohlcv_bar() for b in bars[5:25]]
    assert recorded_windows[0] == expected_first_window
    assert recorded_windows[-1] == expected_last_window


# ---------------------------------------------------------------------------
# Exact forward-horizon indexing / unavailable horizons
# ---------------------------------------------------------------------------


def test_forward_return_uses_exact_future_bar_at_horizon_offset() -> None:
    bars = _bars([str(100 + i) for i in range(25)])
    result = run_walk_forward_evaluation(
        bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, forward_horizons_bars=(1, 4))
    )

    obs = next(o for o in result.observations if o.evaluation_index == 19)
    # close[19]=119, close[20]=120 -> +0.840336%; close[23]=123 -> +3.361345%
    assert obs.forward_returns_by_horizon[1] == Decimal("0.840336")
    assert obs.forward_returns_by_horizon[4] == Decimal("3.361345")


def test_forward_return_is_none_when_horizon_exceeds_available_data() -> None:
    bars = _bars([str(100 + i) for i in range(22)])  # only 2 bars past the window
    result = run_walk_forward_evaluation(
        bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, forward_horizons_bars=(1, 4, 16))
    )

    last_obs = result.observations[-1]  # evaluation_index=21, last bar
    assert last_obs.forward_returns_by_horizon[1] is None
    assert last_obs.forward_returns_by_horizon[4] is None
    assert last_obs.forward_returns_by_horizon[16] is None

    first_obs = result.observations[0]  # evaluation_index=19, 2 bars remain after it
    assert first_obs.forward_returns_by_horizon[1] is not None
    assert first_obs.forward_returns_by_horizon[4] is None  # only 2 bars remain, needs 4
    assert first_obs.forward_returns_by_horizon[16] is None


def test_every_configured_horizon_key_is_always_present_even_when_unavailable() -> None:
    bars = _bars([str(100 + i) for i in range(20)])  # exactly one evaluation point, no future data
    result = run_walk_forward_evaluation(
        bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, forward_horizons_bars=(1, 4, 16))
    )

    obs = result.observations[0]
    assert set(obs.forward_returns_by_horizon.keys()) == {1, 4, 16}
    assert all(v is None for v in obs.forward_returns_by_horizon.values())


# ---------------------------------------------------------------------------
# State frequencies / transitions / persistence / joint state
# (hand-verified three-block scenario)
# ---------------------------------------------------------------------------


def test_state_frequencies_are_exact_for_three_block_scenario() -> None:
    bars = _three_block_direction_bars()
    params = WalkForwardEvaluationParams(classification_window_bars=5, evaluation_step_size_bars=5, minimum_sample_count=1)
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    assert len(result.observations) == 3
    direction_frequencies = {f.state_value: f for f in result.frequencies if f.state_dimension == StateDimension.DIRECTION}
    assert set(direction_frequencies) == {"trending_up", "trending_down", "ranging"}
    for freq in direction_frequencies.values():
        assert freq.observation_count == 1
        assert freq.observation_percentage == Decimal("33.3333")
        assert freq.evidence_status == EvidenceStatus.SUFFICIENT  # minimum_sample_count=1


def test_state_transitions_are_exact_for_three_block_scenario() -> None:
    bars = _three_block_direction_bars()
    params = WalkForwardEvaluationParams(classification_window_bars=5, evaluation_step_size_bars=5, minimum_sample_count=1)
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    direction_transitions = [t for t in result.transitions if t.state_dimension == StateDimension.DIRECTION]
    transition_map = {(t.from_state, t.to_state): t for t in direction_transitions}

    assert set(transition_map) == {("trending_up", "trending_down"), ("trending_down", "ranging")}
    up_to_down = transition_map[("trending_up", "trending_down")]
    assert up_to_down.transition_count == 1
    assert up_to_down.total_outgoing_transitions_from_state == 1
    assert up_to_down.conditional_probability == Decimal("1.0000")

    down_to_ranging = transition_map[("trending_down", "ranging")]
    assert down_to_ranging.transition_count == 1
    assert down_to_ranging.total_outgoing_transitions_from_state == 1
    assert down_to_ranging.conditional_probability == Decimal("1.0000")

    # "ranging" never transitions to anything (it's the final observation),
    # so it must never appear as a from_state.
    assert all(t.from_state != "ranging" for t in direction_transitions)


def test_state_persistence_is_exact_for_three_block_scenario() -> None:
    bars = _three_block_direction_bars()
    params = WalkForwardEvaluationParams(classification_window_bars=5, evaluation_step_size_bars=5, minimum_sample_count=1)
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    direction_persistence = {
        p.state_value: p for p in result.persistence if p.state_dimension == StateDimension.DIRECTION
    }
    # Only states that appeared as a "from" (i.e. had a following
    # observation) get a persistence row -- "ranging" is the final
    # observation and never transitions, so it must be absent.
    assert set(direction_persistence) == {"trending_up", "trending_down"}
    for persistence in direction_persistence.values():
        assert persistence.self_transition_count == 0
        assert persistence.total_outgoing_transitions == 1
        assert persistence.persistence_probability == Decimal("0.0000")


def test_joint_state_dimension_combines_all_three_axes() -> None:
    bars = _three_block_direction_bars()
    params = WalkForwardEvaluationParams(classification_window_bars=5, evaluation_step_size_bars=5, minimum_sample_count=1)
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    joint_frequencies = [f for f in result.frequencies if f.state_dimension == StateDimension.JOINT]
    assert len(joint_frequencies) == 3
    for freq in joint_frequencies:
        parts = freq.state_value.split("|")
        assert len(parts) == 3  # direction|volatility|participation

    # Each observation's joint_state_value must match the concatenation of
    # its own three axis values.
    for obs in result.observations:
        assert obs.joint_state_value == f"{obs.direction_state.value}|{obs.volatility_state.value}|{obs.participation_state.value}"


# ---------------------------------------------------------------------------
# Forward-return summaries: baseline, mean, median, positive%, min/max
# (hand-verified flat-ranging scenario)
# ---------------------------------------------------------------------------


def test_forward_return_summary_statistics_are_exact() -> None:
    bars = _flat_ranging_bars_for_forward_return_test()
    params = WalkForwardEvaluationParams(classification_window_bars=5, evaluation_step_size_bars=1, forward_horizons_bars=(1,), minimum_sample_count=1)
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    # All 6 windows in this fixture classify RANGING (see fixture docstring
    # and its hand-verification), so the direction=ranging summary must
    # equal the baseline exactly, and both must equal these hand-derived
    # values.
    baseline = next(
        s for s in result.forward_return_summaries if s.state_dimension == StateDimension.BASELINE and s.horizon_bars == 1
    )
    ranging_summary = next(
        s
        for s in result.forward_return_summaries
        if s.state_dimension == StateDimension.DIRECTION and s.state_value == "ranging" and s.horizon_bars == 1
    )

    for summary in (baseline, ranging_summary):
        assert summary.sample_count == 5
        assert summary.mean_return == Decimal("0.060460")
        assert summary.median_return == Decimal("0.100000")
        assert summary.positive_return_percentage == Decimal("60.0000")
        assert summary.minimum_return == Decimal("-0.399202")
        assert summary.maximum_return == Decimal("0.501002")
        assert summary.evidence_status == EvidenceStatus.SUFFICIENT

    assert baseline.state_value == "unconditional"


def test_forward_return_summary_is_none_when_zero_samples() -> None:
    bars = _bars([str(100 + i) for i in range(20)])  # exactly one point, no future data at all
    result = run_walk_forward_evaluation(
        bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, forward_horizons_bars=(1,), minimum_sample_count=1)
    )

    baseline = next(s for s in result.forward_return_summaries if s.state_dimension == StateDimension.BASELINE)
    assert baseline.sample_count == 0
    assert baseline.mean_return is None
    assert baseline.median_return is None
    assert baseline.positive_return_percentage is None
    assert baseline.minimum_return is None
    assert baseline.maximum_return is None
    assert baseline.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE


def test_direction_alone_versus_joint_state_summaries_are_both_exposed() -> None:
    """The evaluator must expose both single-axis and joint-axis forward
    return summaries side by side, so a caller can compare them -- it must
    not compute or assert an automated verdict about which is "better"."""

    bars = _bars([str(100 + (i % 5)) for i in range(60)], [str(1000 + (i % 3) * 200) for i in range(60)])
    result = run_walk_forward_evaluation(bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, minimum_sample_count=1))

    direction_summaries = [s for s in result.forward_return_summaries if s.state_dimension == StateDimension.DIRECTION]
    joint_summaries = [s for s in result.forward_return_summaries if s.state_dimension == StateDimension.JOINT]
    assert len(direction_summaries) > 0
    assert len(joint_summaries) > 0
    # No field anywhere claims one is superior -- confirmed structurally by
    # the contract simply not having any such field (see
    # walk_forward_contracts.py).
    assert not hasattr(direction_summaries[0], "is_better_than_baseline")


# ---------------------------------------------------------------------------
# Minimum sample evidence flags
# ---------------------------------------------------------------------------


def test_low_sample_summaries_are_marked_insufficient_evidence_but_not_suppressed() -> None:
    bars = _three_block_direction_bars()
    params = WalkForwardEvaluationParams(
        classification_window_bars=5, evaluation_step_size_bars=5, minimum_sample_count=100
    )
    result = run_walk_forward_evaluation(bars, classifier_params=_SMALL_WINDOW_PARAMS, walk_forward_params=params)

    direction_frequencies = [f for f in result.frequencies if f.state_dimension == StateDimension.DIRECTION]
    assert len(direction_frequencies) == 3  # data is not suppressed
    for freq in direction_frequencies:
        assert freq.observation_count == 1  # raw count still present
        assert freq.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE

    for transition in result.transitions:
        assert transition.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE
    for persistence in result.persistence:
        assert persistence.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE
    for summary in result.forward_return_summaries:
        assert summary.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE


def test_high_minimum_sample_count_still_reports_zero_sample_summaries_as_insufficient() -> None:
    bars = _bars([str(100 + i) for i in range(20)])
    result = run_walk_forward_evaluation(
        bars, walk_forward_params=WalkForwardEvaluationParams(classification_window_bars=20, minimum_sample_count=1)
    )
    baseline = result.forward_return_summaries[0]
    assert baseline.sample_count == 0
    assert baseline.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE  # 0 < 1


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_identical_input_produces_identical_full_result() -> None:
    closes = [str(100 + Decimal("0.7") * (i % 11) - 3) for i in range(60)]
    volumes = [str(1000 + 23 * (i % 7)) for i in range(60)]

    result_a = run_walk_forward_evaluation(_bars(list(closes), list(volumes)))
    result_b = run_walk_forward_evaluation(_bars(list(closes), list(volumes)))

    assert result_a.observations == result_b.observations
    assert result_a.frequencies == result_b.frequencies
    assert result_a.transitions == result_b.transitions
    assert result_a.persistence == result_b.persistence
    assert result_a.forward_return_summaries == result_b.forward_return_summaries


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_empty_bars_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one bar"):
        run_walk_forward_evaluation([])


def test_non_increasing_timestamps_raise_value_error() -> None:
    bars = _bars([str(100 + i) for i in range(25)])
    bad_bars = list(bars)
    bad_bars[10] = dataclasses.replace(bad_bars[10], open_time=bad_bars[9].open_time)  # duplicate, not increasing

    with pytest.raises(ValueError, match="strictly increasing open_time"):
        run_walk_forward_evaluation(bad_bars)


def test_decreasing_timestamps_raise_value_error() -> None:
    bars = _bars([str(100 + i) for i in range(25)])
    bad_bars = list(bars)
    bad_bars[10] = dataclasses.replace(bad_bars[10], open_time=bad_bars[9].open_time - timedelta(minutes=1))

    with pytest.raises(ValueError, match="strictly increasing open_time"):
        run_walk_forward_evaluation(bad_bars)


def test_window_smaller_than_classifier_min_bars_raises_value_error() -> None:
    bars = _bars([str(100 + i) for i in range(25)])
    params = WalkForwardEvaluationParams(classification_window_bars=10)  # < default classifier min_bars=20

    with pytest.raises(ValueError, match="must be >="):
        run_walk_forward_evaluation(bars, walk_forward_params=params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"classification_window_bars": 1},
        {"classification_window_bars": 0},
        {"evaluation_step_size_bars": 0},
        {"evaluation_step_size_bars": -1},
        {"forward_horizons_bars": ()},
        {"forward_horizons_bars": (0, 1)},
        {"forward_horizons_bars": (-1, 4)},
        {"forward_horizons_bars": (1, 1, 4)},
        {"minimum_sample_count": 0},
        {"minimum_sample_count": -5},
    ],
)
def test_invalid_walk_forward_params_raise_value_error(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        WalkForwardEvaluationParams(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Empty and insufficient datasets
# ---------------------------------------------------------------------------


def test_insufficient_bars_for_even_one_window_returns_empty_result_gracefully() -> None:
    bars = _bars([str(100 + i) for i in range(10)])  # fewer than default window (20)
    result = run_walk_forward_evaluation(bars)

    assert result.observations == ()
    assert result.frequencies == ()
    assert result.transitions == ()
    assert result.persistence == ()
    # Forward-return summaries are the one exception: per "do not suppress
    # the data," the baseline row for each configured horizon is still
    # emitted, honestly reporting zero samples and INSUFFICIENT_EVIDENCE,
    # rather than silently vanishing along with the (nonexistent) states.
    assert len(result.forward_return_summaries) == len(DEFAULT_WALK_FORWARD_PARAMS.forward_horizons_bars)
    for summary in result.forward_return_summaries:
        assert summary.state_dimension == StateDimension.BASELINE
        assert summary.sample_count == 0
        assert summary.mean_return is None
        assert summary.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert result.total_bars_supplied == 10


def test_exactly_enough_bars_for_one_window_produces_one_observation() -> None:
    bars = _bars([str(100 + i) for i in range(20)])
    result = run_walk_forward_evaluation(bars)

    assert len(result.observations) == 1
    assert result.observations[0].evaluation_index == 19


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_walk_forward_evaluation_params_is_frozen() -> None:
    params = WalkForwardEvaluationParams()
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.classification_window_bars = 99  # type: ignore[misc]


def test_walk_forward_bar_is_frozen() -> None:
    bar = _bars(["100"])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = Decimal("200")  # type: ignore[misc]


def test_observation_is_frozen() -> None:
    bars = _bars([str(100 + i) for i in range(20)])
    result = run_walk_forward_evaluation(bars)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.observations[0].confidence = Decimal("0.9")  # type: ignore[misc]
