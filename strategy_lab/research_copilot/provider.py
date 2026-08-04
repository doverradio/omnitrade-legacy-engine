from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from strategy_lab.pattern_intelligence.models import Finding, FindingCategory, FindingGroup, json_value

from .models import (
    AnalysisType,
    CandidateExperiment,
    CounterfactualImprovement,
    ExplanationContext,
    PrimaryCause,
    PrimaryCauseClassification,
    PROVIDER_VERSION,
    ResearchExplanation,
    ResearchStatement,
    StatementLabel,
    TEMPLATE_VERSION,
)


class ResearchExplanationProvider(Protocol):
    def explain_selection(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation: ...

    def show_missed(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation: ...

    def explain_trade(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation: ...

    def explain_success(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation: ...

    def overfitting_warnings(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation: ...


RESERVED_PROVIDER_NAMES = (
    "LocalSmallModelProvider",
    "LocalOllamaProvider",
    "FutureOmniTradeNeuralExplainer",
)


_CAUSES: tuple[tuple[str, PrimaryCauseClassification], ...] = (
    ("late_entry_v1", PrimaryCauseClassification.LATE_ENTRY),
    ("early_exit_v1", PrimaryCauseClassification.EARLY_EXIT),
    ("bearish_momentum_v1", PrimaryCauseClassification.NEGATIVE_MOMENTUM_ENTRY),
    ("volume_contraction_v1", PrimaryCauseClassification.LOW_VOLUME_ENTRY),
    ("profit_mode_never_activated_v1", PrimaryCauseClassification.PROFIT_ACTIVATION_NOT_REACHED),
    ("declining_close_exit_before_meaningful_profit_v1", PrimaryCauseClassification.DECLINING_CLOSE_EXIT),
    ("stop_too_close_v1", PrimaryCauseClassification.STOP_EXIT),
    ("missed_entry_v1", PrimaryCauseClassification.MISSED_LIMIT),
    ("repeated_buy_limit_replacement_v1", PrimaryCauseClassification.ENTRY_SCARCITY),
)

_MISSED_DETECTORS = {
    "missed_entry_v1",
    "narrowly_missed_buy_limit_v1",
    "late_entry_v1",
    "early_exit_v1",
    "capital_recovery_v1",
}


class DeterministicTemplateProvider:
    name = "DeterministicTemplateProvider"
    provider_version = PROVIDER_VERSION
    template_version = TEMPLATE_VERSION

    def explain_selection(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation:
        statements: list[ResearchStatement] = []
        for group in FindingGroup:
            grouped = [finding for finding in findings if finding.group == group]
            if grouped:
                dominant = sorted(grouped, key=lambda item: (not item.sufficient_evidence, item.finding_id))[0]
                statements.append(self._observation(len(statements), group.value, dominant))
                statements.extend(self._recurrence_statements(len(statements), dominant))
        contradictions = self._contradictions(findings)
        for left, right in contradictions:
            statements.append(self._statement(
                len(statements), StatementLabel.WARNING, "CONTRADICTIONS",
                f"{left.pattern_name} and {right.pattern_name} are both present; treat the range as mixed evidence rather than a single regime.",
                (left.finding_id, right.finding_id), {"left": left.pattern_name, "right": right.pattern_name},
            ))
        if not statements:
            statements.append(self._insufficient(0, "INSUFFICIENT EVIDENCE", findings, "No supported Pattern Intelligence finding is available for this range."))
        return self._finish(AnalysisType.EXPLAIN_SELECTION, analysis_id, findings, statements)

    def show_missed(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation:
        statements: list[ResearchStatement] = []
        compared = [finding for finding in findings if finding.detector_id in _MISSED_DETECTORS]
        for finding in sorted(compared, key=lambda item: item.finding_id):
            statements.append(self._observation(len(statements), "MISSED OPPORTUNITIES", finding))
        entries = [event for event in context.strategy_events if event.get("kind") == "entry"]
        breakouts = [finding for finding in findings if finding.detector_id in {"bullish_breakout_v1", "bearish_breakdown_v1"}]
        for finding in breakouts:
            participated = any(finding.start_index <= int(event.get("candle_index", -1)) <= finding.end_index for event in entries)
            if not participated:
                statements.append(self._statement(
                    len(statements), StatementLabel.OBSERVATION, "MISSED OPPORTUNITIES",
                    f"{finding.pattern_name} occurred without a strategy entry during the finding interval.",
                    (finding.finding_id,), {**finding.measurements, "strategy_entries_during_finding": 0},
                ))
        if not statements:
            statements.append(self._insufficient(0, "MISSED OPPORTUNITIES", findings, "No replay-backed difference between the strategy events and detected opportunities was found."))
        experiments = self._candidate_experiments(statements)
        return self._finish(AnalysisType.SHOW_MISSED, analysis_id, findings, statements, candidate_experiments=experiments)

    def explain_trade(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation:
        if context.trade is None or Decimal(str(context.trade.get("net_return_pct", "0"))) >= 0:
            statements = [self._insufficient(0, "LIMITATIONS", findings, "A completed losing trade is required for loss analysis.")]
            return self._finish(AnalysisType.EXPLAIN_TRADE, analysis_id, findings, statements, primary_cause=self._insufficient_cause(findings))
        cause = self._classify_primary_cause(findings, context)
        supporting = [finding for finding in findings if finding.finding_id in cause.supporting_finding_ids]
        statements = [self._statement(
            0, StatementLabel.HYPOTHESIS, "PRIMARY CAUSE",
            f"The supported primary classification is {cause.classification.value}; this is an evidence-backed classification, not proof of causation.",
            cause.supporting_finding_ids, cause.measurements,
        )]
        for finding in supporting[1:]:
            statements.append(self._observation(len(statements), "CONTRIBUTING FACTORS", finding))
        historical = self._historical_statements(len(statements), supporting)
        statements.extend(historical or [self._insufficient(len(statements), "HISTORICAL RECURRENCE", supporting or findings, "No sufficient Training recurrence evidence is available at the 4-candle horizon.")])
        alternatives = self._alternative_opportunities(len(statements), findings)
        statements.extend(alternatives)
        if not any(item.section == "ALTERNATIVE ENTRY OPPORTUNITIES" for item in alternatives):
            statements.append(self._insufficient(len(statements), "ALTERNATIVE ENTRY OPPORTUNITIES", findings, "No replay-backed alternative entry opportunity was detected in this trade analysis."))
        if not any(item.section == "ALTERNATIVE EXIT OPPORTUNITIES" for item in alternatives):
            statements.append(self._insufficient(len(statements), "ALTERNATIVE EXIT OPPORTUNITIES", findings, "No replay-backed alternative exit opportunity was detected in this trade analysis."))
        counterfactual = self._counterfactual(findings, context)
        if counterfactual is None:
            statements.append(self._insufficient(len(statements), "COUNTERFACTUAL IMPROVEMENT", findings, "No deterministic counterfactual replay output was supplied, so no improvement estimate is reported."))
        else:
            statements.append(self._statement(
                len(statements), StatementLabel.STATISTICAL_EVIDENCE, "COUNTERFACTUAL IMPROVEMENT",
                f"Replay changed '{counterfactual.changed_condition}' and measured a {counterfactual.estimated_delta_percentage_points} percentage-point delta.",
                counterfactual.source_finding_ids,
                {"observed_result_pct": counterfactual.observed_result_pct, "counterfactual_result_pct": counterfactual.counterfactual_result_pct,
                 "estimated_delta_percentage_points": counterfactual.estimated_delta_percentage_points, "replay_id": counterfactual.replay_id},
            ))
        confidence_label = StatementLabel.STATISTICAL_EVIDENCE if cause.confidence == "HIGH" else StatementLabel.INSUFFICIENT_EVIDENCE
        statements.append(self._statement(len(statements), confidence_label, "CONFIDENCE", f"Primary-classification confidence is {cause.confidence} based on recurrence sufficiency.", cause.supporting_finding_ids, cause.measurements))
        statements.append(self._statement(len(statements), StatementLabel.WARNING, "LIMITATIONS", "; ".join(cause.limitations), cause.supporting_finding_ids, cause.measurements))
        experiments = self._candidate_experiments(statements)
        return self._finish(AnalysisType.EXPLAIN_TRADE, analysis_id, findings, statements, cause, counterfactual, experiments)

    def explain_success(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation:
        if context.trade is None or Decimal(str(context.trade.get("net_return_pct", "0"))) <= 0:
            statements = [self._insufficient(0, "LIMITATIONS", findings, "A completed winning trade is required for success analysis.")]
            return self._finish(AnalysisType.EXPLAIN_SUCCESS, analysis_id, findings, statements)
        supportive_ids = {"bullish_momentum_v1", "bullish_price_volume_confirmation_v1", "bullish_breakout_v1", "breakout_retest_v1", "positive_slope_v1"}
        supportive = [finding for finding in findings if finding.detector_id in supportive_ids]
        statements = [self._observation(index, "PRIMARY SUCCESS FACTORS" if index == 0 else "SUPPORTING CONDITIONS", finding) for index, finding in enumerate(supportive)]
        historical = self._historical_statements(len(statements), supportive)
        statements.extend(historical or [self._insufficient(len(statements), "HISTORICAL RECURRENCE", supportive or findings, "No sufficient historical recurrence supports repeatability at the 4-candle horizon.")])
        if not supportive:
            statements.append(self._insufficient(0, "PRIMARY SUCCESS FACTORS", findings, "The profitable result has no supported success-factor finding in the analyzed trade range."))
        elif all(not finding.sufficient_evidence for finding in supportive):
            statements.append(self._statement(len(statements), StatementLabel.WARNING, "OUTLIER RISK", "Available success factors have insufficient recurrence evidence, so repeatability is unknown.", tuple(item.finding_id for item in supportive), {}))
        source = supportive or list(findings)
        if source:
            repeatable = any(finding.sufficient_evidence for finding in supportive)
            statements.append(self._statement(len(statements), StatementLabel.STATISTICAL_EVIDENCE if repeatable else StatementLabel.INSUFFICIENT_EVIDENCE, "REPEATABILITY", "At least one success factor has sufficient recurrence evidence." if repeatable else "Repeatability cannot be established from the available sample.", tuple(item.finding_id for item in source), {"sufficient_recurring_factor": repeatable}))
            statements.append(self._statement(len(statements), StatementLabel.WARNING, "LIMITATIONS", "Observed success factors are associative and do not prove that they produced the profitable result.", tuple(item.finding_id for item in source), {"causation_established": False}))
        return self._finish(AnalysisType.EXPLAIN_SUCCESS, analysis_id, findings, statements, candidate_experiments=self._candidate_experiments(statements))

    def overfitting_warnings(self, analysis_id: str, findings: Sequence[Finding], context: ExplanationContext) -> ResearchExplanation:
        statements: list[ResearchStatement] = []
        for finding in findings:
            outcomes = {(item.partition, item.forward_horizon): item for item in finding.recurrence}
            horizons = sorted({horizon for partition, horizon in outcomes if partition == "training"})
            for horizon in horizons:
                training = outcomes.get(("training", horizon))
                validation = outcomes.get(("validation", horizon))
                if training and validation and training.average_forward_return is not None and validation.average_forward_return is not None and training.average_forward_return > 0 >= validation.average_forward_return:
                    statements.append(self._statement(
                        len(statements), StatementLabel.WARNING, "PARTITION DIVERGENCE",
                        f"Training is positive while Validation is non-positive at the {horizon}-candle horizon.",
                        (finding.finding_id,), {"horizon": horizon, "training_average_return": training.average_forward_return, "validation_average_return": validation.average_forward_return},
                    ))
            if not finding.sufficient_evidence:
                statements.append(self._statement(len(statements), StatementLabel.WARNING, "SMALL SAMPLE", "Recurrence evidence is below the configured sufficiency threshold.", (finding.finding_id,), {"sufficient_evidence": False}))
        source_ids = tuple(finding.finding_id for finding in findings)
        if context.final_test_used_for_development and source_ids:
            statements.append(self._statement(len(statements), StatementLabel.WARNING, "FINAL TEST CONTAMINATION", "Final Test was marked as used during development; it cannot provide an untouched confirmation result.", source_ids, {"final_test_used_for_development": True}))
        if context.hypotheses_tested_on_partition > 20 and source_ids:
            statements.append(self._statement(len(statements), StatementLabel.WARNING, "REPEATED HYPOTHESIS TESTING", "More than 20 hypotheses were tested against the same partition, increasing selection-bias risk.", source_ids, {"hypotheses_tested_on_partition": context.hypotheses_tested_on_partition}))
        for sensitivity in context.sensitivity_results:
            if bool(sensitivity.get("sharp_change")) and source_ids:
                statements.append(self._statement(len(statements), StatementLabel.WARNING, "THRESHOLD SENSITIVITY", "A small threshold change produced a sharp evidence change.", source_ids, sensitivity))
            dominance = sensitivity.get("outlier_dominance_ratio")
            if dominance is not None and Decimal(str(dominance)) >= Decimal("0.50") and source_ids:
                statements.append(self._statement(len(statements), StatementLabel.WARNING, "OUTLIER DOMINANCE", "One occurrence contributes at least half of the supplied aggregate result.", source_ids, sensitivity))
        if not statements:
            statements.append(self._insufficient(0, "OVERFITTING WARNINGS", findings, "No supplied partition, sensitivity, or research-history evidence supports an overfitting warning."))
        return self._finish(AnalysisType.OVERFITTING_WARNINGS, analysis_id, findings, statements)

    def _classify_primary_cause(self, findings: Sequence[Finding], context: ExplanationContext) -> PrimaryCause:
        candidates: list[tuple[Finding, PrimaryCauseClassification]] = []
        for detector_id, classification in _CAUSES:
            candidates.extend((finding, classification) for finding in findings if finding.detector_id == detector_id)
        metrics = context.replay_metrics
        gross = Decimal(str(metrics.get("gross_return_pct", "0")))
        net = Decimal(str(metrics.get("net_return_pct", gross)))
        fees = Decimal(str(metrics.get("fees_paid", "0")))
        slippage = Decimal(str(metrics.get("estimated_slippage", "0")))
        if findings and gross >= 0 > net and fees > 0:
            candidates.insert(0, (findings[0], PrimaryCauseClassification.FEE_DRAG))
        elif findings and slippage > fees and net < gross:
            candidates.insert(0, (findings[0], PrimaryCauseClassification.SLIPPAGE_DRAG))
        if findings and context.trade and str(context.trade.get("exit_reason", "")) in {"initial_stop", "stop"}:
            candidates.insert(0, (findings[0], PrimaryCauseClassification.STOP_EXIT))
        if findings and context.trade and str(context.trade.get("exit_reason", "")) == "declining_closes" and not any(item[1] == PrimaryCauseClassification.DECLINING_CLOSE_EXIT for item in candidates):
            candidates.insert(0, (findings[0], PrimaryCauseClassification.DECLINING_CLOSE_EXIT))
        entries = [event for event in context.strategy_events if event.get("kind") == "entry"]
        breakout = next((finding for finding in findings if finding.detector_id in {"bullish_breakout_v1", "bearish_breakdown_v1"}), None)
        if breakout and not any(breakout.start_index <= int(event.get("candle_index", -1)) <= breakout.end_index for event in entries):
            candidates.append((breakout, PrimaryCauseClassification.BREAKOUT_NOT_CAPTURED))
        if not candidates:
            return self._insufficient_cause(findings)
        finding, classification = candidates[0]
        recurrence = tuple(json_value(item) for item in finding.recurrence if item.forward_horizon == 4)
        sufficient = any(item.get("sufficient_evidence") for item in recurrence)
        measurements = dict(finding.measurements)
        if classification in {PrimaryCauseClassification.FEE_DRAG, PrimaryCauseClassification.SLIPPAGE_DRAG}:
            measurements.update({"gross_return_pct": gross, "net_return_pct": net, "fees_paid": fees, "estimated_slippage": slippage})
        return PrimaryCause(
            classification=classification,
            supporting_finding_ids=(finding.finding_id,),
            measurements=measurements,
            historical_recurrence=recurrence,
            confidence="HIGH" if sufficient else "LIMITED",
            alternatives_considered=tuple(item[1] for item in candidates[1:4]),
            limitations=("Classification is associative and does not establish causation.", "Counterfactual improvement requires separate deterministic replay."),
        )

    def _counterfactual(self, findings: Sequence[Finding], context: ExplanationContext) -> CounterfactualImprovement | None:
        replay = context.counterfactual_replay
        if not replay or replay.get("source") != "deterministic_replay" or not replay.get("replay_id") or not findings:
            return None
        observed = Decimal(str(replay["observed_result_pct"]))
        counterfactual = Decimal(str(replay["counterfactual_result_pct"]))
        return CounterfactualImprovement(
            changed_condition=str(replay["changed_condition"]), observed_result_pct=str(observed), counterfactual_result_pct=str(counterfactual),
            estimated_delta_percentage_points=str(counterfactual - observed), partition=str(replay["partition"]),
            cost_model=dict(replay["cost_model"]), comparable_occurrences=int(replay["comparable_occurrences"]),
            validation_final_test_agree=replay.get("validation_final_test_agree"), status=str(replay.get("status", "exploratory")),
            source_finding_ids=tuple(str(item) for item in replay.get("source_finding_ids", (findings[0].finding_id,))), replay_id=str(replay["replay_id"]),
        )

    def _historical_statements(self, offset: int, findings: Sequence[Finding]) -> list[ResearchStatement]:
        statements: list[ResearchStatement] = []
        for finding in findings:
            outcome = next((item for item in finding.recurrence if item.partition == "training" and item.forward_horizon == 4), None)
            if outcome and outcome.sufficient_evidence:
                statements.append(self._statement(offset + len(statements), StatementLabel.STATISTICAL_EVIDENCE, "HISTORICAL RECURRENCE", f"Training contains {outcome.occurrence_count} comparable occurrences at the 4-candle horizon.", (finding.finding_id,), json_value(outcome)))
        return statements

    def _recurrence_statements(self, offset: int, finding: Finding) -> list[ResearchStatement]:
        outcomes = [item for item in finding.recurrence if item.forward_horizon == 4]
        if not outcomes or not any(item.sufficient_evidence for item in outcomes):
            return [self._insufficient(offset, "HISTORICAL RECURRENCE", (finding,), f"{finding.pattern_name} does not have sufficient 4-candle recurrence evidence.")]
        return [self._statement(offset, StatementLabel.STATISTICAL_EVIDENCE, "HISTORICAL RECURRENCE", f"{finding.pattern_name} has sufficient recurrence evidence in {sum(item.sufficient_evidence for item in outcomes)} partition(s).", (finding.finding_id,), {"outcomes": json_value(outcomes)})]

    def _alternative_opportunities(self, offset: int, findings: Sequence[Finding]) -> list[ResearchStatement]:
        statements = []
        for finding in findings:
            if finding.detector_id in {"missed_entry_v1", "narrowly_missed_buy_limit_v1"}:
                statements.append(self._observation(offset + len(statements), "ALTERNATIVE ENTRY OPPORTUNITIES", finding))
            if finding.detector_id in {"early_exit_v1", "unrealized_profit_left_on_table_v1"}:
                statements.append(self._observation(offset + len(statements), "ALTERNATIVE EXIT OPPORTUNITIES", finding))
        return statements

    def _candidate_experiments(self, statements: Sequence[ResearchStatement]) -> tuple[CandidateExperiment, ...]:
        hypothesis = next((item for item in statements if item.label in {StatementLabel.HYPOTHESIS, StatementLabel.OBSERVATION} and item.source_finding_ids), None)
        if hypothesis is None:
            return ()
        return (CandidateExperiment(
            experiment_id="CE-001",
            question=f"Would one controlled condition addressing {hypothesis.section.lower()} improve expected-cost results?",
            suggested_controlled_change="Change exactly one entry or exit condition derived from the linked evidence; keep all other parameters fixed.",
            required_tests=("Training", "Validation", "Final Test"), source_finding_ids=hypothesis.source_finding_ids,
        ),)

    def _contradictions(self, findings: Sequence[Finding]) -> list[tuple[Finding, Finding]]:
        pairs = (("bullish_momentum_v1", "bearish_momentum_v1"), ("positive_slope_v1", "negative_slope_v1"), ("volume_expansion_v1", "volume_contraction_v1"))
        by_detector = {finding.detector_id: finding for finding in findings}
        return [(by_detector[left], by_detector[right]) for left, right in pairs if left in by_detector and right in by_detector]

    def _observation(self, index: int, section: str, finding: Finding) -> ResearchStatement:
        if finding.category == FindingCategory.INSUFFICIENT_EVIDENCE:
            return self._statement(index, StatementLabel.INSUFFICIENT_EVIDENCE, section, f"{finding.pattern_name} was reported for candles {finding.start_index} through {finding.end_index}, but supporting evidence is insufficient.", (finding.finding_id,), finding.measurements)
        if finding.category == FindingCategory.CONTRADICTION:
            return self._statement(index, StatementLabel.WARNING, section, f"{finding.pattern_name} is contradictory evidence for candles {finding.start_index} through {finding.end_index}.", (finding.finding_id,), finding.measurements)
        return self._statement(index, StatementLabel.OBSERVATION, section, f"{finding.pattern_name} was detected from candles {finding.start_index} through {finding.end_index}.", (finding.finding_id,), finding.measurements)

    def _insufficient(self, index: int, section: str, findings: Sequence[Finding], text: str) -> ResearchStatement:
        ids = tuple(finding.finding_id for finding in findings)
        return self._statement(index, StatementLabel.INSUFFICIENT_EVIDENCE, section, text, ids, {})

    def _insufficient_cause(self, findings: Sequence[Finding]) -> PrimaryCause:
        ids = tuple(finding.finding_id for finding in findings)
        return PrimaryCause(PrimaryCauseClassification.INSUFFICIENT_EVIDENCE, ids, {}, (), "INSUFFICIENT", (), ("No supported primary-cause classification is available.",))

    def _statement(self, index: int, label: StatementLabel, section: str, text: str, source_ids: Sequence[str], measurements: Mapping[str, Any]) -> ResearchStatement:
        if not source_ids:
            raise ValueError("Research statements require at least one source finding ID")
        if label == StatementLabel.OBSERVATION and not measurements:
            raise ValueError("Observations require source measurements")
        return ResearchStatement(f"statement_{index + 1:04d}", label, section, text, tuple(source_ids), json_value(measurements))

    def _finish(
        self, analysis_type: AnalysisType, analysis_id: str, findings: Sequence[Finding], statements: Sequence[ResearchStatement],
        primary_cause: PrimaryCause | None = None, counterfactual: CounterfactualImprovement | None = None,
        candidate_experiments: Sequence[CandidateExperiment] = (),
    ) -> ResearchExplanation:
        valid_ids = {finding.finding_id for finding in findings}
        for statement in statements:
            if not set(statement.source_finding_ids).issubset(valid_ids):
                raise ValueError("Research statement references an unknown source finding ID")
        result = ResearchExplanation(analysis_type, self.name, self.provider_version, self.template_version, analysis_id, tuple(statements), primary_cause, counterfactual, tuple(candidate_experiments))
        payload = json.dumps(json_value(asdict(result)), sort_keys=True, separators=(",", ":"))
        return replace(result, content_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest())
