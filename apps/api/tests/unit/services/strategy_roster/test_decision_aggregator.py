from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.strategy_roster.decision_aggregator import (
    AGGREGATE_STRATEGY_IDENTITY,
    AGGREGATE_STRATEGY_VERSION,
    AggregationConfig,
    StrategyOutcomeSummary,
    StrategyProposalInput,
    _strategy_weight,
    aggregate_strategy_proposals,
    resolve_action_position_transition,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ROSTER_RUN_ID = "run-1"
ASSET_ID = "asset-1"
CANDLE_CLOSE = NOW


def _config(**overrides: object) -> AggregationConfig:
    defaults: dict[str, object] = dict(
        config_version="v1",
        min_eligible_strategies=2,
        min_buy_agreement=Decimal("0.60"),
        min_sell_agreement=Decimal("0.60"),
        min_confidence=Decimal("0.40"),
        max_evidence_age_minutes=30,
        min_outcome_sample_size=20,
        veto_on_data_quality_failure=True,
    )
    defaults.update(overrides)
    return AggregationConfig(**defaults)


def _proposal(
    *,
    slug: str,
    action: str,
    confidence: Decimal | None = Decimal("0.80"),
    strength: Decimal | None = None,
    evaluation_status: str = "EVALUATED",
    registered_and_enabled: bool = True,
    evaluated_at: datetime = NOW,
    roster_run_id: str = ROSTER_RUN_ID,
    asset_id: str = ASSET_ID,
    candle_close_time: datetime = CANDLE_CLOSE,
    outcome_evidence: StrategyOutcomeSummary | None = None,
    recent_failure_streak: int = 0,
) -> StrategyProposalInput:
    return StrategyProposalInput(
        strategy_slug=slug,
        strategy_identity=f"{slug}@1.0.0",
        strategy_version="1.0.0",
        action=action,
        confidence=confidence,
        strength=strength,
        evaluation_status=evaluation_status,
        evaluated_at=evaluated_at,
        roster_run_id=roster_run_id,
        asset_id=asset_id,
        candle_close_time=candle_close_time,
        registered_and_enabled=registered_and_enabled,
        outcome_evidence=outcome_evidence,
        recent_failure_streak=recent_failure_streak,
    )


# 1. all HOLD -> aggregate HOLD
def test_all_hold_produces_aggregate_hold() -> None:
    proposals = [_proposal(slug="ma_crossover", action="HOLD"), _proposal(slug="momentum", action="HOLD"), _proposal(slug="breakout", action="HOLD")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    assert result.final_action == "HOLD"
    assert result.failed_closed is False


# 2. strong eligible BUY agreement with no position -> aggregate BUY
def test_strong_buy_agreement_no_position_produces_buy() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="BUY"), _proposal(slug="breakout", action="BUY")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    assert result.final_action == "BUY"
    assert result.eligible_strategy_count == 3


# 3. weak BUY agreement -> HOLD
def test_weak_buy_agreement_produces_hold() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="HOLD"), _proposal(slug="breakout", action="SELL")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=True, now=NOW, config=_config())
    assert result.final_action == "HOLD"


# 4. conflicting BUY and SELL proposals -> deterministic result (equal weight -> HOLD, not an error)
def test_conflicting_buy_and_sell_is_deterministic() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="SELL")]
    result_a = aggregate_strategy_proposals(proposals=proposals, position_open=True, now=NOW, config=_config())
    result_b = aggregate_strategy_proposals(proposals=list(reversed(proposals)), position_open=True, now=NOW, config=_config())
    assert result_a.final_action == result_b.final_action == "HOLD"
    assert result_a.weighted_buy_score == result_b.weighted_buy_score
    assert result_a.weighted_sell_score == result_b.weighted_sell_score


# 5. SELL proposals with no position -> no unsupported short
def test_sell_agreement_without_position_does_not_short() -> None:
    proposals = [_proposal(slug="ma_crossover", action="SELL"), _proposal(slug="momentum", action="SELL"), _proposal(slug="breakout", action="SELL")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    assert result.final_action == "HOLD"
    assert "CHECK_FAILED:sell_signal_no_position_to_close" in result.deterministic_explanation


# 6. SELL agreement with an open position -> eligible exit recommendation
def test_sell_agreement_with_open_position_recommends_exit() -> None:
    proposals = [_proposal(slug="ma_crossover", action="SELL"), _proposal(slug="momentum", action="SELL"), _proposal(slug="breakout", action="SELL")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=True, now=NOW, config=_config())
    assert result.final_action == "SELL"


# 7. stale proposal exclusion
def test_stale_proposal_is_excluded() -> None:
    stale_time = NOW - timedelta(minutes=90)
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", evaluated_at=stale_time),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    stale_contribution = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert stale_contribution.eligible is False
    assert stale_contribution.exclusion_reason == "stale_evidence"
    assert result.eligible_strategy_count == 2


# 8. failed strategy exclusion
def test_failed_evaluation_status_is_excluded() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", evaluation_status="FAILED"),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    failed_contribution = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert failed_contribution.eligible is False
    assert failed_contribution.exclusion_reason == "proposal_not_evaluated"
    assert result.final_action == "BUY"
    assert result.eligible_strategy_count == 2


# 9. insufficient eligible strategies -> HOLD / fail closed
def test_insufficient_eligible_strategies_fails_closed_to_hold() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config(min_eligible_strategies=2))
    assert result.final_action == "HOLD"
    assert result.failed_closed is True
    assert "CHECK_FAILED:insufficient_eligible_strategies" in result.deterministic_explanation


# 10. risk/data-quality veto preserves HOLD
def test_data_quality_veto_forces_hold_regardless_of_agreement() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="BUY"), _proposal(slug="breakout", action="BUY")]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config(), data_quality_failed=True)
    assert result.final_action == "HOLD"
    assert result.failed_closed is True
    assert result.eligible_strategy_count == 0


def test_data_quality_veto_can_be_disabled_via_config() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="BUY"), _proposal(slug="breakout", action="BUY")]
    result = aggregate_strategy_proposals(
        proposals=proposals, position_open=False, now=NOW, config=_config(veto_on_data_quality_failure=False), data_quality_failed=True
    )
    assert result.final_action == "BUY"


# 12. deterministic replay produces identical result
def test_replay_of_identical_inputs_is_byte_identical() -> None:
    proposals = [_proposal(slug="ma_crossover", action="BUY"), _proposal(slug="momentum", action="BUY"), _proposal(slug="breakout", action="SELL")]
    config = _config()
    result_1 = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=config)
    result_2 = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=config)
    assert result_1 == result_2


# 16. MA crossover no longer silently wins merely by being first/only-registered
def test_ma_crossover_does_not_automatically_win_against_stronger_evidence() -> None:
    strong_outcome = StrategyOutcomeSummary(sample_size=100, overall_correct_pct=Decimal("80"), average_fee_adjusted_return_pct=Decimal("3.0"))
    weak_outcome = StrategyOutcomeSummary(sample_size=100, overall_correct_pct=Decimal("20"), average_fee_adjusted_return_pct=Decimal("-2.0"))
    proposals = [
        _proposal(slug="ma_crossover", action="SELL", outcome_evidence=weak_outcome),
        _proposal(slug="momentum", action="BUY", outcome_evidence=strong_outcome),
        _proposal(slug="breakout", action="BUY", outcome_evidence=strong_outcome),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    assert result.final_action == "BUY"
    # The reported identity is always the stable canonical aggregate identity,
    # never bound to whichever contributor happened to dominate this cycle...
    assert result.primary_strategy_identity == AGGREGATE_STRATEGY_IDENTITY
    assert result.primary_strategy_version == AGGREGATE_STRATEGY_VERSION
    # ...but the informational dominant contributor still proves ma_crossover's
    # weaker evidence did not win the vote merely by being first/only-registered.
    assert result.dominant_contributor_identity in {"momentum@1.0.0", "breakout@1.0.0"}
    assert result.dominant_contributor_identity != "ma_crossover@1.0.0"


def test_aggregate_identity_is_stable_regardless_of_contribution_ordering() -> None:
    strong_outcome = StrategyOutcomeSummary(sample_size=100, overall_correct_pct=Decimal("80"), average_fee_adjusted_return_pct=Decimal("3.0"))
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", outcome_evidence=strong_outcome),
        _proposal(slug="momentum", action="BUY", outcome_evidence=strong_outcome),
        _proposal(slug="breakout", action="BUY", outcome_evidence=strong_outcome),
    ]
    forward = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    reversed_order = aggregate_strategy_proposals(proposals=list(reversed(proposals)), position_open=False, now=NOW, config=_config())
    import random

    shuffled = list(proposals)
    random.Random(7).shuffle(shuffled)
    shuffled_result = aggregate_strategy_proposals(proposals=shuffled, position_open=False, now=NOW, config=_config())

    assert forward.primary_strategy_identity == reversed_order.primary_strategy_identity == shuffled_result.primary_strategy_identity == AGGREGATE_STRATEGY_IDENTITY
    assert forward.final_action == reversed_order.final_action == shuffled_result.final_action == "BUY"


def test_not_registered_or_enabled_strategy_excluded() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", registered_and_enabled=False),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    excluded = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert excluded.eligible is False
    assert excluded.exclusion_reason == "not_registered_or_enabled"


def test_confidence_below_threshold_excluded() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", confidence=Decimal("0.10")),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config(min_confidence=Decimal("0.40")))
    excluded = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert excluded.eligible is False
    assert excluded.exclusion_reason == "confidence_below_threshold"


def test_disqualifying_failure_streak_excludes_strategy() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", recent_failure_streak=3),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    excluded = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert excluded.eligible is False
    assert excluded.exclusion_reason == "strategy_health_disqualified"


def test_mismatched_scope_proposal_excluded() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY"),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="BUY", roster_run_id="different-run"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    excluded = next(item for item in result.contributions if item.strategy_slug == "breakout")
    assert excluded.eligible is False
    assert excluded.exclusion_reason == "mismatched_scope"


def test_scorecard_evidence_cannot_change_equal_weight_default() -> None:
    thin_outcome = StrategyOutcomeSummary(sample_size=3, overall_correct_pct=Decimal("90"), average_fee_adjusted_return_pct=Decimal("10.0"))
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", outcome_evidence=thin_outcome),
        _proposal(slug="momentum", action="BUY"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config(min_outcome_sample_size=20))
    contribution = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert contribution.evidence_basis == "equal_weight_default"
    assert contribution.weight == "1"


def test_missing_confidence_receives_reduced_magnitude_without_fabrication() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY", confidence=None, strength=None),
        _proposal(slug="momentum", action="BUY", confidence=Decimal("1"), strength=None),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    missing = next(item for item in result.contributions if item.strategy_slug == "ma_crossover")
    assert missing.raw_confidence is None
    assert missing.weighted_buy == "0.50"


@pytest.mark.parametrize(
    ("action", "position_state", "compounding_allowed", "expected"),
    [
        ("BUY", "FLAT", False, "OPEN_CANDIDATE"),
        ("BUY", "OPEN", False, "HOLD"),
        ("BUY", "OPEN", True, "ADD_CANDIDATE"),
        ("SELL", "OPEN", False, "CLOSE_CANDIDATE"),
        ("SELL", "FLAT", False, "HOLD"),
        ("HOLD", "FLAT", False, "HOLD"),
        ("HOLD", "OPEN", False, "HOLD"),
        ("BUY", "UNKNOWN", False, "HOLD"),
        ("SELL", "UNAVAILABLE", False, "HOLD"),
        ("AMBIGUOUS", "FLAT", False, "HOLD"),
    ],
)
def test_action_position_transition_table(action: str, position_state: str, compounding_allowed: bool, expected: str) -> None:
    assert resolve_action_position_transition(
        action=action,
        position_state=position_state,
        compounding_allowed=compounding_allowed,
    ) == expected


def _outcome(sample_size: int, overall_correct_pct: Decimal | None) -> StrategyOutcomeSummary:
    return StrategyOutcomeSummary(sample_size=sample_size, overall_correct_pct=overall_correct_pct, average_fee_adjusted_return_pct=None)


# --- Evidence-based weighting: _strategy_weight ---


def test_strategy_weight_no_outcome_evidence_is_equal_weight() -> None:
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=None)
    weight, evidence_basis = _strategy_weight(proposal, config=_config())
    assert weight == Decimal("1")
    assert evidence_basis == "equal_weight_default"


def test_strategy_weight_sample_size_below_minimum_is_equal_weight() -> None:
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=_outcome(19, Decimal("90")))
    weight, evidence_basis = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight == Decimal("1")
    assert evidence_basis == "equal_weight_default"


def test_strategy_weight_missing_correctness_pct_falls_back_to_equal_weight() -> None:
    # Sample size clears the bar but no correctness figure was computed --
    # never fabricate a signal, fall back exactly like the no-evidence case.
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=_outcome(50, None))
    weight, evidence_basis = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight == Decimal("1")
    assert evidence_basis == "equal_weight_default"


def test_strategy_weight_strong_performer_exceeds_neutral() -> None:
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=_outcome(50, Decimal("80")))
    weight, evidence_basis = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight > Decimal("1.0")
    assert evidence_basis == "outcome_evidence_weighted"


def test_strategy_weight_weak_performer_is_below_neutral() -> None:
    proposal = _proposal(slug="ma_crossover", action="SELL", outcome_evidence=_outcome(50, Decimal("20")))
    weight, evidence_basis = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight < Decimal("1.0")
    assert evidence_basis == "outcome_evidence_weighted"


def test_strategy_weight_never_exceeds_maximum_clamp() -> None:
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=_outcome(50, Decimal("100")))
    weight, _ = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight == Decimal("1.5")


def test_strategy_weight_never_drops_below_minimum_clamp() -> None:
    proposal = _proposal(slug="ma_crossover", action="SELL", outcome_evidence=_outcome(50, Decimal("0")))
    weight, _ = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight == Decimal("0.5")


def test_strategy_weight_neutral_correctness_stays_at_baseline() -> None:
    proposal = _proposal(slug="ma_crossover", action="BUY", outcome_evidence=_outcome(50, Decimal("50")))
    weight, evidence_basis = _strategy_weight(proposal, config=_config(min_outcome_sample_size=20))
    assert weight == Decimal("1.0")
    assert evidence_basis == "outcome_evidence_weighted"


# --- Evidence-based weighting: full aggregation ---


def test_aggregation_unchanged_when_all_strategies_equal_weight() -> None:
    proposals = [
        _proposal(slug="ma_crossover", action="BUY"),
        _proposal(slug="momentum", action="BUY"),
        _proposal(slug="breakout", action="SELL"),
    ]
    result = aggregate_strategy_proposals(proposals=proposals, position_open=False, now=NOW, config=_config())
    assert result.final_action == "BUY"
    for item in result.contributions:
        assert item.weight == "1"
        assert item.evidence_basis == "equal_weight_default"
        assert item.equal_weight_fallback is True
        assert item.outcome_sample_size is None
        assert item.outcome_correctness_pct is None


def test_evidence_based_weighting_resolves_previously_undecidable_split() -> None:
    """Reproduces production's BUY=3/SELL=3/HOLD=1 stalemate: with equal
    weighting the vote is a genuine tie (weak_or_conflicting_agreement,
    thresholds untouched), but once real, sufficiently-sampled outcome
    evidence legitimately favors one side, the same 60% supermajority
    threshold is met without any threshold change or arbitrary tie-break."""
    strong_buy_track_record = _outcome(50, Decimal("95"))
    weak_sell_track_record = _outcome(50, Decimal("5"))

    tied_proposals = [
        _proposal(slug="buy1", action="BUY"),
        _proposal(slug="buy2", action="BUY"),
        _proposal(slug="buy3", action="BUY"),
        _proposal(slug="sell1", action="SELL"),
        _proposal(slug="sell2", action="SELL"),
        _proposal(slug="sell3", action="SELL"),
        _proposal(slug="hold1", action="HOLD"),
    ]
    tied_result = aggregate_strategy_proposals(proposals=tied_proposals, position_open=True, now=NOW, config=_config())
    assert tied_result.final_action == "HOLD"
    assert "CHECK_FAILED:weak_or_conflicting_agreement" in tied_result.deterministic_explanation
    assert tied_result.weighted_buy_score == tied_result.weighted_sell_score

    weighted_proposals = [
        _proposal(slug="buy1", action="BUY", outcome_evidence=strong_buy_track_record),
        _proposal(slug="buy2", action="BUY", outcome_evidence=strong_buy_track_record),
        _proposal(slug="buy3", action="BUY", outcome_evidence=strong_buy_track_record),
        _proposal(slug="sell1", action="SELL", outcome_evidence=weak_sell_track_record),
        _proposal(slug="sell2", action="SELL", outcome_evidence=weak_sell_track_record),
        _proposal(slug="sell3", action="SELL", outcome_evidence=weak_sell_track_record),
        _proposal(slug="hold1", action="HOLD"),
    ]
    weighted_result = aggregate_strategy_proposals(proposals=weighted_proposals, position_open=True, now=NOW, config=_config())
    assert weighted_result.final_action == "BUY"
    assert "CHECK_PASSED:buy_agreement_threshold_met" in weighted_result.deterministic_explanation
    assert weighted_result.thresholds_applied["min_buy_agreement"] == "0.60"

    buy_contribution = next(item for item in weighted_result.contributions if item.strategy_slug == "buy1")
    assert buy_contribution.evidence_basis == "outcome_evidence_weighted"
    assert buy_contribution.outcome_sample_size == 50
    assert buy_contribution.outcome_correctness_pct == "95"
    assert buy_contribution.equal_weight_fallback is False
