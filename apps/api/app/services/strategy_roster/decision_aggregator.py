from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

_VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
_DISQUALIFYING_FAILURE_STREAK = 3
MISSING_CONFIDENCE_MAGNITUDE = Decimal("0.50")

# Evidence-based weighting bounds. A strategy's realized correctness only ever
# nudges its vote around the neutral 1.0x baseline -- never below 0.5x or
# above 1.5x -- so no single strategy, however strong its track record, can
# dominate the ensemble outright. The baseline (50%) is the coin-flip point:
# correctness at or below it never raises weight above neutral.
_OUTCOME_WEIGHT_NEUTRAL = Decimal("1.0")
_OUTCOME_WEIGHT_MIN = Decimal("0.5")
_OUTCOME_WEIGHT_MAX = Decimal("1.5")
_OUTCOME_WEIGHT_BASELINE_CORRECT_PCT = Decimal("50")
_OUTCOME_WEIGHT_SLOPE_PER_PCT = (_OUTCOME_WEIGHT_MAX - _OUTCOME_WEIGHT_NEUTRAL) / (Decimal("100") - _OUTCOME_WEIGHT_BASELINE_CORRECT_PCT)


def resolve_action_position_transition(*, action: str, position_state: str, compounding_allowed: bool = False) -> str:
    """Return the only governed campaign transition for a signal/position pair.

    Unknown values always fail closed. Compounding is represented explicitly;
    callers must still prove capital, exposure, order, package, reconciliation,
    and execution-authority gates before treating ADD_CANDIDATE as actionable.
    """
    normalized_action = str(action).strip().upper()
    normalized_position = str(position_state).strip().upper()
    if normalized_position not in {"FLAT", "OPEN"} or normalized_action not in _VALID_ACTIONS:
        return "HOLD"
    if normalized_action == "HOLD":
        return "HOLD"
    if normalized_action == "BUY":
        if normalized_position == "FLAT":
            return "OPEN_CANDIDATE"
        return "ADD_CANDIDATE" if compounding_allowed else "HOLD"
    return "CLOSE_CANDIDATE" if normalized_position == "OPEN" else "HOLD"

# Canonical, stable identity for every aggregate decision. An ensemble outcome
# must never be attributed to whichever individual contributor happened to be
# highest-weighted this cycle -- that would make the reported identity
# order/weight-dependent and would fight the campaign-continuity check (which
# requires the SAME strategy identity across cycles governing an open
# position) every time a different contributor "won." A real, active Strategy
# catalog row + ParameterSet must exist for this identity (see
# app.services.capital_campaign_orchestration.authoritative._ensure_aggregate_strategy_catalog_entry)
# so canonical package composition can resolve it exactly like any other
# strategy identity.
AGGREGATE_STRATEGY_SLUG = "strategy_roster_aggregate"
AGGREGATE_STRATEGY_VERSION = "1.0.0"
AGGREGATE_STRATEGY_IDENTITY = f"{AGGREGATE_STRATEGY_SLUG}@{AGGREGATE_STRATEGY_VERSION}"


@dataclass(frozen=True, slots=True)
class AggregationConfig:
    """Conservative, configuration-backed thresholds. Never hardcoded inline in the algorithm."""

    config_version: str
    min_eligible_strategies: int
    min_buy_agreement: Decimal
    min_sell_agreement: Decimal
    min_confidence: Decimal
    max_evidence_age_minutes: int
    min_outcome_sample_size: int
    veto_on_data_quality_failure: bool


@dataclass(frozen=True, slots=True)
class StrategyOutcomeSummary:
    """Real, persisted paper-outcome evidence for one strategy (sourced from
    strategy_outcomes scorecards). Never synthetic. None/insufficient fields mean
    "no reliable evidence yet" and the aggregator must fall back to a neutral weight."""

    sample_size: int
    overall_correct_pct: Decimal | None
    average_fee_adjusted_return_pct: Decimal | None
    regime_match: bool | None = None


@dataclass(frozen=True, slots=True)
class StrategyProposalInput:
    strategy_slug: str
    strategy_identity: str
    strategy_version: str
    action: str
    confidence: Decimal | None
    strength: Decimal | None
    evaluation_status: str
    evaluated_at: datetime
    roster_run_id: str
    asset_id: str
    candle_close_time: datetime
    registered_and_enabled: bool
    outcome_evidence: StrategyOutcomeSummary | None = None
    recent_failure_streak: int = 0


@dataclass(frozen=True, slots=True)
class StrategyContributionRecord:
    strategy_slug: str
    strategy_identity: str
    raw_action: str
    raw_confidence: str | None
    raw_strength: str | None
    eligible: bool
    exclusion_reason: str | None
    weight: str
    evidence_basis: str
    weighted_buy: str
    weighted_sell: str
    weighted_hold: str
    # Explainability for how `weight` was derived, so any aggregation result
    # can be reconstructed exactly. None/False when the strategy has no
    # persisted outcome evidence at all (excluded contributions, or eligible
    # ones that never accumulated any outcome evidence).
    outcome_sample_size: int | None = None
    outcome_correctness_pct: str | None = None
    equal_weight_fallback: bool = False


@dataclass(frozen=True, slots=True)
class AggregationResult:
    final_action: str
    eligible_strategy_count: int
    weighted_buy_score: Decimal
    weighted_sell_score: Decimal
    weighted_hold_score: Decimal
    contributions: tuple[StrategyContributionRecord, ...]
    # Always the canonical AGGREGATE_STRATEGY_IDENTITY/AGGREGATE_STRATEGY_VERSION
    # when the aggregator actually evaluated proposals, and None only when there
    # was nothing to evaluate at all. Never an individual contributor's identity
    # -- see the module-level comment on AGGREGATE_STRATEGY_IDENTITY.
    primary_strategy_identity: str | None
    primary_strategy_version: str | None
    # Informational only: the single highest-weighted eligible contributor
    # toward the final action, for observability/debugging. Never used as the
    # reported strategy identity and never fed into continuity/coherence checks.
    dominant_contributor_identity: str | None
    explanation: str
    deterministic_explanation: tuple[str, ...]
    thresholds_applied: dict[str, str]
    failed_closed: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_fresh(evaluated_at: datetime, *, now: datetime, max_age_minutes: int) -> bool:
    age_seconds = (_as_utc(now) - _as_utc(evaluated_at)).total_seconds()
    # Tolerate up to 60s of clock skew for "future" timestamps; reject anything older
    # than the configured freshness window.
    return -60 <= age_seconds <= max_age_minutes * 60


def _split_identity(identity: str) -> tuple[str, str] | None:
    if identity.count("@") != 1:
        return None
    slug, version = identity.split("@", 1)
    slug = slug.strip()
    version = version.strip()
    if not slug or not version:
        return None
    return slug, version


def _effective_signal_magnitude(proposal: StrategyProposalInput) -> Decimal:
    raw = proposal.strength if proposal.strength is not None else proposal.confidence
    if raw is None:
        # Absence remains absence in persisted evidence. It is not fabricated
        # into a confidence value and receives a deliberately reduced neutral
        # contribution rather than the maximum possible magnitude.
        return MISSING_CONFIDENCE_MAGNITUDE
    return max(Decimal("0"), min(Decimal("1"), raw))


def _strategy_weight(proposal: StrategyProposalInput, *, config: AggregationConfig) -> tuple[Decimal, str]:
    """Equal weight by default; nudged by real, persisted outcome evidence
    once a strategy has accumulated enough of it.

    Horizon alignment, freshness, deduplication, regime compatibility,
    look-ahead protection and confidence intervals are not all governed yet
    for the underlying scorecards, so evidence is only trusted once it clears
    `config.min_outcome_sample_size` -- below that, or with no evidence at
    all, behavior is identical to the original equal-weight default. Never
    fabricates a correctness signal: a strategy with an unset
    `overall_correct_pct` falls back to equal weight even if its sample size
    clears the bar. See `_OUTCOME_WEIGHT_*` for the deterministic, clamped
    mapping from realized correctness to weight.
    """
    evidence = proposal.outcome_evidence
    if evidence is None or evidence.sample_size < config.min_outcome_sample_size or evidence.overall_correct_pct is None:
        return Decimal("1"), "equal_weight_default"

    offset = (evidence.overall_correct_pct - _OUTCOME_WEIGHT_BASELINE_CORRECT_PCT) * _OUTCOME_WEIGHT_SLOPE_PER_PCT
    weight = max(_OUTCOME_WEIGHT_MIN, min(_OUTCOME_WEIGHT_MAX, _OUTCOME_WEIGHT_NEUTRAL + offset))
    return weight, "outcome_evidence_weighted"


def _evaluate_eligibility(
    proposal: StrategyProposalInput,
    *,
    now: datetime,
    config: AggregationConfig,
    scope_roster_run_id: str,
    scope_asset_id: str,
    scope_candle_close_time: datetime,
) -> str | None:
    """Returns None if eligible, else the exclusion reason. Fail-closed: any
    ambiguity results in exclusion, never a guessed inclusion."""
    if proposal.roster_run_id != scope_roster_run_id or proposal.asset_id != scope_asset_id or proposal.candle_close_time != scope_candle_close_time:
        return "mismatched_scope"
    if not proposal.registered_and_enabled:
        return "not_registered_or_enabled"
    if proposal.evaluation_status != "EVALUATED":
        return "proposal_not_evaluated"
    if proposal.action not in _VALID_ACTIONS:
        return "invalid_action"
    if _split_identity(proposal.strategy_identity) is None:
        return "invalid_strategy_identity"
    split_version = _split_identity(proposal.strategy_identity)
    if split_version is not None and split_version[1] != proposal.strategy_version:
        return "invalid_strategy_identity"
    if not _is_fresh(proposal.evaluated_at, now=now, max_age_minutes=config.max_evidence_age_minutes):
        return "stale_evidence"
    if proposal.confidence is not None and proposal.confidence < config.min_confidence:
        return "confidence_below_threshold"
    if proposal.recent_failure_streak >= _DISQUALIFYING_FAILURE_STREAK:
        return "strategy_health_disqualified"
    return None


def aggregate_strategy_proposals(
    *,
    proposals: list[StrategyProposalInput],
    position_open: bool,
    now: datetime,
    config: AggregationConfig,
    data_quality_failed: bool = False,
) -> AggregationResult:
    thresholds_applied = {
        "config_version": config.config_version,
        "min_eligible_strategies": str(config.min_eligible_strategies),
        "min_buy_agreement": str(config.min_buy_agreement),
        "min_sell_agreement": str(config.min_sell_agreement),
        "min_confidence": str(config.min_confidence),
        "max_evidence_age_minutes": str(config.max_evidence_age_minutes),
        "min_outcome_sample_size": str(config.min_outcome_sample_size),
    }

    if data_quality_failed and config.veto_on_data_quality_failure:
        return AggregationResult(
            final_action="HOLD",
            eligible_strategy_count=0,
            weighted_buy_score=Decimal("0"),
            weighted_sell_score=Decimal("0"),
            weighted_hold_score=Decimal("0"),
            contributions=tuple(),
            primary_strategy_identity=AGGREGATE_STRATEGY_IDENTITY,
            primary_strategy_version=AGGREGATE_STRATEGY_VERSION,
            dominant_contributor_identity=None,
            explanation="data_quality_veto",
            deterministic_explanation=("CHECK_FAILED:data_quality_veto",),
            thresholds_applied=thresholds_applied,
            failed_closed=True,
        )

    scope_roster_run_id = proposals[0].roster_run_id if proposals else ""
    scope_asset_id = proposals[0].asset_id if proposals else ""
    scope_candle_close_time = proposals[0].candle_close_time if proposals else now

    contributions: list[StrategyContributionRecord] = []
    weighted_buy = Decimal("0")
    weighted_sell = Decimal("0")
    weighted_hold = Decimal("0")
    eligible_count = 0

    for proposal in sorted(proposals, key=lambda item: item.strategy_slug):
        exclusion_reason = _evaluate_eligibility(
            proposal,
            now=now,
            config=config,
            scope_roster_run_id=scope_roster_run_id,
            scope_asset_id=scope_asset_id,
            scope_candle_close_time=scope_candle_close_time,
        )
        eligible = exclusion_reason is None
        if not eligible:
            contributions.append(
                StrategyContributionRecord(
                    strategy_slug=proposal.strategy_slug,
                    strategy_identity=proposal.strategy_identity,
                    raw_action=proposal.action,
                    raw_confidence=None if proposal.confidence is None else str(proposal.confidence),
                    raw_strength=None if proposal.strength is None else str(proposal.strength),
                    eligible=False,
                    exclusion_reason=exclusion_reason,
                    weight="0",
                    evidence_basis="excluded",
                    weighted_buy="0",
                    weighted_sell="0",
                    weighted_hold="0",
                )
            )
            continue

        eligible_count += 1
        weight, evidence_basis = _strategy_weight(proposal, config=config)
        magnitude = _effective_signal_magnitude(proposal)
        weighted_value = weight * magnitude

        buy_share = weighted_value if proposal.action == "BUY" else Decimal("0")
        sell_share = weighted_value if proposal.action == "SELL" else Decimal("0")
        hold_share = weighted_value if proposal.action == "HOLD" else Decimal("0")
        weighted_buy += buy_share
        weighted_sell += sell_share
        weighted_hold += hold_share

        contributions.append(
            StrategyContributionRecord(
                strategy_slug=proposal.strategy_slug,
                strategy_identity=proposal.strategy_identity,
                raw_action=proposal.action,
                raw_confidence=None if proposal.confidence is None else str(proposal.confidence),
                raw_strength=None if proposal.strength is None else str(proposal.strength),
                eligible=True,
                exclusion_reason=None,
                weight=str(weight),
                evidence_basis=evidence_basis,
                weighted_buy=str(buy_share),
                weighted_sell=str(sell_share),
                weighted_hold=str(hold_share),
                outcome_sample_size=None if proposal.outcome_evidence is None else proposal.outcome_evidence.sample_size,
                outcome_correctness_pct=(
                    None
                    if proposal.outcome_evidence is None or proposal.outcome_evidence.overall_correct_pct is None
                    else str(proposal.outcome_evidence.overall_correct_pct)
                ),
                equal_weight_fallback=evidence_basis == "equal_weight_default",
            )
        )

    total_weight = weighted_buy + weighted_sell + weighted_hold
    explanation_tokens: list[str] = []

    if eligible_count < config.min_eligible_strategies:
        explanation_tokens.append("CHECK_FAILED:insufficient_eligible_strategies")
        return AggregationResult(
            final_action="HOLD",
            eligible_strategy_count=eligible_count,
            weighted_buy_score=weighted_buy,
            weighted_sell_score=weighted_sell,
            weighted_hold_score=weighted_hold,
            contributions=tuple(contributions),
            primary_strategy_identity=AGGREGATE_STRATEGY_IDENTITY,
            primary_strategy_version=AGGREGATE_STRATEGY_VERSION,
            dominant_contributor_identity=None,
            explanation="insufficient_eligible_strategies",
            deterministic_explanation=tuple(explanation_tokens),
            thresholds_applied=thresholds_applied,
            failed_closed=True,
        )

    buy_agreement = (weighted_buy / total_weight) if total_weight > 0 else Decimal("0")
    sell_agreement = (weighted_sell / total_weight) if total_weight > 0 else Decimal("0")

    tentative_action = "HOLD"
    if buy_agreement >= config.min_buy_agreement and weighted_buy > weighted_sell:
        tentative_action = "BUY"
        explanation_tokens.append("CHECK_PASSED:buy_agreement_threshold_met")
    elif sell_agreement >= config.min_sell_agreement and weighted_sell > weighted_buy:
        tentative_action = "SELL"
        explanation_tokens.append("CHECK_PASSED:sell_agreement_threshold_met")
    else:
        explanation_tokens.append("CHECK_FAILED:weak_or_conflicting_agreement")

    final_action = tentative_action
    if tentative_action == "SELL" and not position_open:
        # Position-aware rule: never create unsupported short exposure. A SELL
        # majority while flat contributes to HOLD/no-entry reasoning only.
        final_action = "HOLD"
        explanation_tokens.append("CHECK_FAILED:sell_signal_no_position_to_close")

    # Informational only: the single highest-weighted eligible contributor
    # toward the final action (or, for HOLD, overall), broken by slug for
    # determinism. This is NEVER the identity reported for coherence/continuity
    # purposes -- the aggregate always reports AGGREGATE_STRATEGY_IDENTITY
    # regardless of which individual strategy happened to dominate this cycle,
    # so an ensemble decision can never become accidentally bound to an
    # arbitrary contributor, and the reported identity is stable and
    # order-independent across cycles.
    action_for_identity = final_action
    ranked = [
        (
            Decimal(item.weight if action_for_identity == "HOLD" else (item.weighted_buy if action_for_identity == "BUY" else item.weighted_sell)),
            item.strategy_slug,
            item,
        )
        for item in contributions
        if item.eligible
    ]
    dominant_contributor_identity = None
    if ranked:
        ranked.sort(key=lambda entry: (-entry[0], entry[1]))
        dominant_contributor_identity = ranked[0][2].strategy_identity

    if final_action == "HOLD" and not explanation_tokens:
        explanation_tokens.append("CHECK_FAILED:weak_or_conflicting_agreement")

    return AggregationResult(
        final_action=final_action,
        eligible_strategy_count=eligible_count,
        weighted_buy_score=weighted_buy,
        weighted_sell_score=weighted_sell,
        weighted_hold_score=weighted_hold,
        contributions=tuple(contributions),
        primary_strategy_identity=AGGREGATE_STRATEGY_IDENTITY,
        primary_strategy_version=AGGREGATE_STRATEGY_VERSION,
        dominant_contributor_identity=dominant_contributor_identity,
        explanation=explanation_tokens[-1].split(":", 1)[-1] if explanation_tokens else "hold",
        deterministic_explanation=tuple(explanation_tokens),
        thresholds_applied=thresholds_applied,
        failed_closed=False,
    )
