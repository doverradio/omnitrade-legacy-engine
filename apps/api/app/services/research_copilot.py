from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from app.schemas.strategy_lab_offline import ResearchCopilotRequest, ResearchCopilotTradeRequest
from app.services.pattern_intelligence import build_selection_analysis, build_trade_analysis

from strategy_lab.pattern_intelligence.models import AnalysisResult, json_value
from strategy_lab.research_copilot import DeterministicTemplateProvider, ExplanationContext
from strategy_lab.research_copilot.models import ResearchExplanation


_PROVIDER = DeterministicTemplateProvider()


def explain_selection(payload: ResearchCopilotRequest) -> dict[str, object]:
    analysis, replay, _ = build_selection_analysis(payload)
    explanation = _PROVIDER.explain_selection(analysis.analysis_id, analysis.findings, _context(payload, analysis, replay))
    return _response(analysis, explanation, payload)


def show_missed(payload: ResearchCopilotRequest) -> dict[str, object]:
    analysis, replay, _ = build_selection_analysis(payload)
    explanation = _PROVIDER.show_missed(analysis.analysis_id, analysis.findings, _context(payload, analysis, replay))
    return _response(analysis, explanation, payload)


def explain_trade(payload: ResearchCopilotTradeRequest) -> dict[str, object]:
    return _trade_response(payload, _PROVIDER.explain_trade)


def explain_success(payload: ResearchCopilotTradeRequest) -> dict[str, object]:
    return _trade_response(payload, _PROVIDER.explain_success)


def overfitting_warnings(payload: ResearchCopilotRequest) -> dict[str, object]:
    analysis, replay, _ = build_selection_analysis(payload)
    explanation = _PROVIDER.overfitting_warnings(analysis.analysis_id, analysis.findings, _context(payload, analysis, replay))
    return _response(analysis, explanation, payload)


def _trade_response(
    payload: ResearchCopilotTradeRequest,
    explain: Callable[[str, tuple, ExplanationContext], ResearchExplanation],
) -> dict[str, object]:
    analysis, replay, trade = build_trade_analysis(payload)
    explanation = explain(analysis.analysis_id, analysis.findings, _context(payload, analysis, replay, trade))
    return _response(analysis, explanation, payload)


def _context(payload, analysis: AnalysisResult, replay: dict[str, object], trade: dict[str, object] | None = None) -> ExplanationContext:
    start, end = analysis.selected_range
    events = tuple(event for event in replay["events"] if start <= int(event["candle_index"]) <= end)
    return ExplanationContext(
        selected_range=analysis.selected_range,
        partition=analysis.partition,
        strategy_version=payload.strategy_version,
        trade=trade,
        replay_metrics=replay["metrics"],
        strategy_events=events,
        final_test_used_for_development=payload.final_test_used_for_development,
        hypotheses_tested_on_partition=payload.hypotheses_tested_on_partition,
        sensitivity_results=tuple(payload.sensitivity_results),
    )


def _response(analysis: AnalysisResult, explanation: ResearchExplanation, payload) -> dict[str, object]:
    source_ids = {source_id for statement in explanation.statements for source_id in statement.source_finding_ids}
    evidence = [finding for finding in analysis.findings if finding.finding_id in source_ids]
    return json_value({
        **asdict(explanation),
        "evidence": {
            "source_findings": evidence,
            "detector_versions": analysis.detector_versions,
            "selected_candles": analysis.selected_range,
            "recurrence_evidence": {finding.finding_id: finding.recurrence for finding in evidence},
            "partition": analysis.partition,
            "cost_model": {"fee_pct": payload.parameters.fee_pct, "slippage_pct": payload.parameters.slippage_pct},
            "configuration": analysis.configuration,
        },
    })