from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


PROVIDER_VERSION = "1.0.0"
TEMPLATE_VERSION = "1.0.0"


class StatementLabel(str, Enum):
    OBSERVATION = "OBSERVATION"
    STATISTICAL_EVIDENCE = "STATISTICAL EVIDENCE"
    HYPOTHESIS = "HYPOTHESIS"
    RECOMMENDATION = "RECOMMENDATION"
    WARNING = "WARNING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT EVIDENCE"


class AnalysisType(str, Enum):
    EXPLAIN_SELECTION = "EXPLAIN_SELECTION"
    SHOW_MISSED = "SHOW_MISSED"
    EXPLAIN_TRADE = "EXPLAIN_TRADE"
    EXPLAIN_SUCCESS = "EXPLAIN_SUCCESS"
    OVERFITTING_WARNINGS = "OVERFITTING_WARNINGS"


class PrimaryCauseClassification(str, Enum):
    LATE_ENTRY = "LATE_ENTRY"
    EARLY_EXIT = "EARLY_EXIT"
    NEGATIVE_MOMENTUM_ENTRY = "NEGATIVE_MOMENTUM_ENTRY"
    LOW_VOLUME_ENTRY = "LOW_VOLUME_ENTRY"
    BREAKOUT_NOT_CAPTURED = "BREAKOUT_NOT_CAPTURED"
    PROFIT_ACTIVATION_NOT_REACHED = "PROFIT_ACTIVATION_NOT_REACHED"
    DECLINING_CLOSE_EXIT = "DECLINING_CLOSE_EXIT"
    STOP_EXIT = "STOP_EXIT"
    FEE_DRAG = "FEE_DRAG"
    SLIPPAGE_DRAG = "SLIPPAGE_DRAG"
    ENTRY_SCARCITY = "ENTRY_SCARCITY"
    MISSED_LIMIT = "MISSED_LIMIT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class ResearchStatement:
    statement_id: str
    label: StatementLabel
    section: str
    text: str
    source_finding_ids: tuple[str, ...]
    measurements: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrimaryCause:
    classification: PrimaryCauseClassification
    supporting_finding_ids: tuple[str, ...]
    measurements: Mapping[str, Any]
    historical_recurrence: tuple[Mapping[str, Any], ...]
    confidence: str
    alternatives_considered: tuple[PrimaryCauseClassification, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CounterfactualImprovement:
    changed_condition: str
    observed_result_pct: str
    counterfactual_result_pct: str
    estimated_delta_percentage_points: str
    partition: str
    cost_model: Mapping[str, Any]
    comparable_occurrences: int
    validation_final_test_agree: bool | None
    status: str
    source_finding_ids: tuple[str, ...]
    replay_id: str


@dataclass(frozen=True)
class CandidateExperiment:
    experiment_id: str
    question: str
    suggested_controlled_change: str
    required_tests: tuple[str, ...]
    source_finding_ids: tuple[str, ...]
    status: str = "PROPOSED"
    executable_rule: bool = False


@dataclass(frozen=True)
class ExplanationContext:
    selected_range: tuple[int, int]
    partition: str
    strategy_version: str
    trade: Mapping[str, Any] | None = None
    replay_metrics: Mapping[str, Any] = field(default_factory=dict)
    strategy_events: Sequence[Mapping[str, Any]] = ()
    counterfactual_replay: Mapping[str, Any] | None = None
    final_test_used_for_development: bool = False
    hypotheses_tested_on_partition: int = 0
    sensitivity_results: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class ResearchExplanation:
    analysis_type: AnalysisType
    provider: str
    provider_version: str
    template_version: str
    source_analysis_id: str
    statements: tuple[ResearchStatement, ...]
    primary_cause: PrimaryCause | None = None
    counterfactual_improvement: CounterfactualImprovement | None = None
    candidate_experiments: tuple[CandidateExperiment, ...] = ()
    content_hash: str = ""
