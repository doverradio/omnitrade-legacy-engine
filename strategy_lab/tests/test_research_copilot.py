from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import socket

import pytest

from strategy_lab.pattern_intelligence.models import Finding, FindingCategory, FindingGroup, RecurrenceOutcome
from strategy_lab.research_copilot import DeterministicTemplateProvider, ExplanationContext, PrimaryCauseClassification, StatementLabel


def finding(detector_id: str = "late_entry_v1", measurements=None, sufficient=True) -> Finding:
    recurrence = RecurrenceOutcome("training", 4, 8, Decimal("-0.003"), Decimal("-0.002"), Decimal("0.4"), Decimal("0.3"), Decimal("0.01"), Decimal("-0.02"), Decimal("0.3"), (Decimal("-0.01"), Decimal("0.004")), sufficient)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Finding(f"{detector_id}:1:3", detector_id, "1.0.0", FindingCategory.OBSERVATION, FindingGroup.STRATEGY_BEHAVIOR, detector_id.replace("_v1", "").replace("_", " ").title(), 1, 3, now, now, measurements or {"move_consumed_before_entry": Decimal("0.78")}, {}, ("measured",), ("condition",), sufficient, (recurrence,))


def context(**kwargs) -> ExplanationContext:
    values = {"selected_range": (1, 3), "partition": "entire_dataset", "strategy_version": "002"}
    values.update(kwargs)
    return ExplanationContext(**values)


def test_same_findings_produce_identical_explanations() -> None:
    provider = DeterministicTemplateProvider()
    first = provider.explain_selection("analysis", [finding()], context())
    second = provider.explain_selection("analysis", [finding()], context())
    assert first == second
    assert first.content_hash == second.content_hash


def test_every_statement_references_valid_findings_and_observations_have_measurements() -> None:
    source = finding()
    result = DeterministicTemplateProvider().explain_selection("analysis", [source], context())
    assert all(set(item.source_finding_ids) <= {source.finding_id} for item in result.statements)
    assert all(item.measurements for item in result.statements if item.label == StatementLabel.OBSERVATION)


def test_loss_analysis_labels_late_entry_as_supported_hypothesis() -> None:
    result = DeterministicTemplateProvider().explain_trade("analysis", [finding()], context(trade={"net_return_pct": "-0.72"}))
    assert result.primary_cause.classification == PrimaryCauseClassification.LATE_ENTRY
    assert result.statements[0].label == StatementLabel.HYPOTHESIS
    assert "not proof of causation" in result.statements[0].text


def test_fee_drag_isolated_from_non_negative_gross_behavior() -> None:
    result = DeterministicTemplateProvider().explain_trade("analysis", [finding()], context(
        trade={"net_return_pct": "-0.40"},
        replay_metrics={"gross_return_pct": "0.10", "net_return_pct": "-0.40", "fees_paid": "0.40", "estimated_slippage": "0.10"},
    ))
    assert result.primary_cause.classification == PrimaryCauseClassification.FEE_DRAG
    assert result.primary_cause.measurements["gross_return_pct"] == Decimal("0.10")


def test_missed_opportunity_requires_replay_comparison_evidence() -> None:
    generic = replace(finding("volatility_contraction_v1"), group=FindingGroup.VOLATILITY)
    result = DeterministicTemplateProvider().show_missed("analysis", [generic], context())
    assert result.statements[0].label == StatementLabel.INSUFFICIENT_EVIDENCE
    missed = DeterministicTemplateProvider().show_missed("analysis", [finding("missed_entry_v1")], context())
    assert missed.statements[0].label == StatementLabel.OBSERVATION


def test_counterfactual_improvement_requires_deterministic_replay_output() -> None:
    source = finding()
    absent = DeterministicTemplateProvider().explain_trade("analysis", [source], context(trade={"net_return_pct": "-0.72"}))
    assert absent.counterfactual_improvement is None
    supplied = DeterministicTemplateProvider().explain_trade("analysis", [source], context(
        trade={"net_return_pct": "-0.72"},
        counterfactual_replay={"source": "deterministic_replay", "replay_id": "replay-1", "changed_condition": "delay entry", "observed_result_pct": "-0.72", "counterfactual_result_pct": "0.08", "partition": "validation", "cost_model": {"fee_pct": "0.002"}, "comparable_occurrences": 8, "validation_final_test_agree": True, "status": "validated", "source_finding_ids": [source.finding_id]},
    ))
    assert supplied.counterfactual_improvement.estimated_delta_percentage_points == "0.80"


def test_overfitting_warning_compares_training_and_validation() -> None:
    source = finding()
    validation = replace(source.recurrence[0], partition="validation", average_forward_return=Decimal("-0.01"))
    source = replace(source, recurrence=(replace(source.recurrence[0], average_forward_return=Decimal("0.01")), validation))
    result = DeterministicTemplateProvider().overfitting_warnings("analysis", [source], context())
    assert any(item.section == "PARTITION DIVERGENCE" for item in result.statements)


def test_overfitting_warns_on_threshold_sensitivity_and_outlier_dominance() -> None:
    result = DeterministicTemplateProvider().overfitting_warnings("analysis", [finding()], context(
        sensitivity_results=({"sharp_change": True, "outlier_dominance_ratio": "0.60"},),
    ))
    assert {item.section for item in result.statements} >= {"THRESHOLD SENSITIVITY", "OUTLIER DOMINANCE"}


def test_loss_report_contains_every_required_section() -> None:
    result = DeterministicTemplateProvider().explain_trade("analysis", [finding()], context(trade={"net_return_pct": "-0.72"}))
    assert {item.section for item in result.statements} >= {
        "PRIMARY CAUSE", "ALTERNATIVE ENTRY OPPORTUNITIES", "ALTERNATIVE EXIT OPPORTUNITIES",
        "HISTORICAL RECURRENCE", "COUNTERFACTUAL IMPROVEMENT", "CONFIDENCE", "LIMITATIONS",
    }


def test_provider_rejects_observation_without_measurements() -> None:
    provider = DeterministicTemplateProvider()
    with pytest.raises(ValueError, match="Observations require"):
        provider._statement(0, StatementLabel.OBSERVATION, "TEST", "Unsupported", (finding().finding_id,), {})


def test_insufficient_pattern_finding_is_not_promoted_to_observation() -> None:
    source = replace(finding(), category=FindingCategory.INSUFFICIENT_EVIDENCE)
    result = DeterministicTemplateProvider().explain_selection("analysis", [source], context())
    assert all(item.label != StatementLabel.OBSERVATION for item in result.statements)


def test_deterministic_provider_makes_no_network_request(monkeypatch) -> None:
    def reject_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    result = DeterministicTemplateProvider().explain_selection("analysis", [finding()], context())
    assert result.provider == "DeterministicTemplateProvider"