from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_experiment_recommendation import DecisionExperimentRecommendation
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord


RECOMMENDATION_ENGINE_VERSION = "recommendation_v1"


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationDraft:
    recommendation_type: str
    recommendation_category: str
    confidence_level: str
    expected_impact_level: str
    required_human_review_level: str
    supporting_evidence_refs: list[dict[str, Any]]
    originating_decision_ids: list[str]
    explanation: str
    suggested_experiment: dict[str, Any]
    evidence_state: str
    state_reason: str | None
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RecommendationGenerationResult:
    scanned_decisions: int
    inserted_recommendations: int
    skipped_existing: int


@dataclass(frozen=True, slots=True)
class ExperimentRecommendationReadModel:
    recommendation_id: uuid.UUID
    recommendation_type: str
    recommendation_category: str
    confidence_level: str
    expected_impact_level: str
    required_human_review_level: str
    supporting_evidence_refs: list[dict[str, Any]]
    originating_decision_ids: list[str]
    explanation: str
    suggested_experiment: dict[str, Any]
    evidence_state: str
    state_reason: str | None
    provenance: dict[str, Any]
    advisory_only: bool
    created_at: Any


async def generate_experiment_recommendations_v1(
    *,
    db: AsyncSession,
    decision_ids: list[uuid.UUID] | None = None,
) -> RecommendationGenerationResult:
    decisions = await _load_decisions(db=db, decision_ids=decision_ids)
    inserted = 0
    skipped_existing = 0

    for decision in decisions:
        quality_score = await _load_latest_quality_score(db=db, decision_id=decision.decision_id)
        counterfactuals = await _load_counterfactuals(db=db, decision_id=decision.decision_id)

        draft = build_experiment_recommendation_draft(
            decision_record=decision,
            quality_score=quality_score,
            counterfactual_results=counterfactuals,
        )

        idempotency_key = build_recommendation_idempotency_key(
            engine_version=RECOMMENDATION_ENGINE_VERSION,
            draft=draft,
        )

        existing = await db.scalar(
            select(DecisionExperimentRecommendation.id)
            .where(DecisionExperimentRecommendation.idempotency_key == idempotency_key)
            .limit(1)
        )
        if existing is not None:
            skipped_existing += 1
            continue

        async with db.begin():
            db.add(
                DecisionExperimentRecommendation(
                    idempotency_key=idempotency_key,
                    recommendation_engine_version=RECOMMENDATION_ENGINE_VERSION,
                    recommendation_type=draft.recommendation_type,
                    recommendation_category=draft.recommendation_category,
                    confidence_level=draft.confidence_level,
                    expected_impact_level=draft.expected_impact_level,
                    required_human_review_level=draft.required_human_review_level,
                    supporting_evidence_refs=draft.supporting_evidence_refs,
                    originating_decision_ids=draft.originating_decision_ids,
                    explanation=draft.explanation,
                    suggested_experiment=draft.suggested_experiment,
                    evidence_state=draft.evidence_state,
                    state_reason=draft.state_reason,
                    provenance=draft.provenance,
                    advisory_only=True,
                )
            )

        inserted += 1

    return RecommendationGenerationResult(
        scanned_decisions=len(decisions),
        inserted_recommendations=inserted,
        skipped_existing=skipped_existing,
    )


async def read_experiment_recommendations(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID | None = None,
) -> list[ExperimentRecommendationReadModel]:
    result = await db.execute(
        select(DecisionExperimentRecommendation)
        .order_by(DecisionExperimentRecommendation.created_at.desc(), DecisionExperimentRecommendation.id.desc())
    )
    rows = list(result.scalars().all())

    if decision_id is not None:
        needle = str(decision_id)
        rows = [item for item in rows if needle in item.originating_decision_ids]

    return [
        ExperimentRecommendationReadModel(
            recommendation_id=item.id,
            recommendation_type=item.recommendation_type,
            recommendation_category=item.recommendation_category,
            confidence_level=item.confidence_level,
            expected_impact_level=item.expected_impact_level,
            required_human_review_level=item.required_human_review_level,
            supporting_evidence_refs=item.supporting_evidence_refs,
            originating_decision_ids=item.originating_decision_ids,
            explanation=item.explanation,
            suggested_experiment=item.suggested_experiment,
            evidence_state=item.evidence_state,
            state_reason=item.state_reason,
            provenance=item.provenance,
            advisory_only=item.advisory_only,
            created_at=item.created_at,
        )
        for item in rows
    ]


def build_experiment_recommendation_draft(
    *,
    decision_record: DecisionRecord,
    quality_score: DecisionQualityScore | None,
    counterfactual_results: list[DecisionCounterfactualResult],
) -> ExperimentRecommendationDraft:
    decision_id = str(decision_record.decision_id)

    supporting_refs = _build_supporting_refs(
        quality_score=quality_score,
        counterfactual_results=counterfactual_results,
    )
    provenance = {
        "engine_version": RECOMMENDATION_ENGINE_VERSION,
        "source_ids": {
            "decision_records": [decision_id],
            "decision_quality_scores": [str(quality_score.id)] if quality_score is not None else [],
            "decision_counterfactual_results": sorted(str(item.id) for item in counterfactual_results),
        },
        "lineage": {
            "decision_record_lineage": decision_record.source_lineage,
            "counterfactual_horizons": sorted(item.horizon_minutes for item in counterfactual_results),
        },
    }

    if quality_score is None and not counterfactual_results:
        return ExperimentRecommendationDraft(
            recommendation_type="recurring_decision_pattern",
            recommendation_category="pattern",
            confidence_level="low",
            expected_impact_level="low",
            required_human_review_level="standard",
            supporting_evidence_refs=supporting_refs,
            originating_decision_ids=[decision_id],
            explanation="Recommendation generated with unavailable quality and counterfactual evidence; collect more evidence before action.",
            suggested_experiment={
                "name": "collect_evidence_baseline",
                "hypothesis": "Additional decision quality and counterfactual outcomes will clarify recurring patterns.",
                "procedure": ["accumulate additional resolved horizons", "re-evaluate recommendation"],
            },
            evidence_state="unavailable",
            state_reason="quality_and_counterfactual_unavailable",
            provenance=provenance,
        )

    unresolved_counterfactual = bool(counterfactual_results) and not any(
        item.evaluation_state == "resolved" for item in counterfactual_results
    )
    if unresolved_counterfactual:
        return ExperimentRecommendationDraft(
            recommendation_type="hypothesis_test",
            recommendation_category="hypothesis",
            confidence_level="low",
            expected_impact_level="medium",
            required_human_review_level="priority",
            supporting_evidence_refs=supporting_refs,
            originating_decision_ids=[decision_id],
            explanation="Counterfactual evidence is currently unresolved; test WAIT vs directional hypothesis once horizons resolve.",
            suggested_experiment={
                "name": "wait_vs_directional_resolution",
                "hypothesis": "Resolved horizons will indicate whether chosen action alignment remains stable.",
                "procedure": ["wait for horizon resolution", "compare chosen vs hindsight-best action"],
            },
            evidence_state="unknown",
            state_reason="counterfactual_unresolved",
            provenance=provenance,
        )

    quality_value = _quality_value(quality_score=quality_score)
    confidence = _confidence_level(quality_value=quality_value, counterfactual_results=counterfactual_results)

    lesson_tags = _lesson_tags(counterfactual_results)
    if any(tag in {"trend_filter_incorrect", "volatility_filter_saved_trade"} for tag in lesson_tags):
        return ExperimentRecommendationDraft(
            recommendation_type="risk_observation",
            recommendation_category="risk",
            confidence_level=confidence,
            expected_impact_level="medium",
            required_human_review_level="required",
            supporting_evidence_refs=supporting_refs,
            originating_decision_ids=[decision_id],
            explanation="Risk-related lesson tags indicate recurring risk behavior worth structured review.",
            suggested_experiment={
                "name": "risk_rule_observation_review",
                "hypothesis": "Observed risk tags may reveal conditions where current controls over- or under-constrain decisions.",
                "procedure": ["review tagged decisions", "simulate paper-only decision review with fixed safeguards"],
            },
            evidence_state="known",
            state_reason=None,
            provenance=provenance,
        )

    if quality_value < Decimal("0.6000"):
        return ExperimentRecommendationDraft(
            recommendation_type="strategy_parameter_investigation",
            recommendation_category="strategy",
            confidence_level=confidence,
            expected_impact_level="high",
            required_human_review_level="required",
            supporting_evidence_refs=supporting_refs,
            originating_decision_ids=[decision_id],
            explanation="Decision quality score suggests underperformance in process quality; investigate parameter sensitivity.",
            suggested_experiment={
                "name": "parameter_sensitivity_slice",
                "hypothesis": "Small bounded parameter changes may improve decision process quality without execution changes.",
                "procedure": ["define bounded candidate parameters", "run offline backtest comparisons", "document review outcome"],
            },
            evidence_state="known",
            state_reason=None,
            provenance=provenance,
        )

    if any(item.actual_action_correct is False for item in counterfactual_results if item.evaluation_state == "resolved"):
        return ExperimentRecommendationDraft(
            recommendation_type="experiment_run",
            recommendation_category="experiment",
            confidence_level=confidence,
            expected_impact_level="medium",
            required_human_review_level="priority",
            supporting_evidence_refs=supporting_refs,
            originating_decision_ids=[decision_id],
            explanation="Resolved counterfactual mismatch detected; run a targeted hypothesis experiment on action selection criteria.",
            suggested_experiment={
                "name": "action_selection_hypothesis",
                "hypothesis": "Specific evidence thresholds may reduce mismatch between chosen and hindsight-best actions.",
                "procedure": ["define threshold variants", "evaluate on historical decision slices", "compare process metrics"],
            },
            evidence_state="known",
            state_reason=None,
            provenance=provenance,
        )

    return ExperimentRecommendationDraft(
        recommendation_type="recurring_decision_pattern",
        recommendation_category="pattern",
        confidence_level=confidence,
        expected_impact_level="low",
        required_human_review_level="standard",
        supporting_evidence_refs=supporting_refs,
        originating_decision_ids=[decision_id],
        explanation="Recurring stable decision pattern observed; continue monitoring with periodic hypothesis checks.",
        suggested_experiment={
            "name": "pattern_stability_monitor",
            "hypothesis": "Current decision pattern remains stable across additional horizons.",
            "procedure": ["track pattern frequency weekly", "escalate on drift"],
        },
        evidence_state="known",
        state_reason=None,
        provenance=provenance,
    )


def build_recommendation_idempotency_key(*, engine_version: str, draft: ExperimentRecommendationDraft) -> str:
    payload = {
        "engine_version": engine_version,
        "recommendation_type": draft.recommendation_type,
        "recommendation_category": draft.recommendation_category,
        "confidence_level": draft.confidence_level,
        "expected_impact_level": draft.expected_impact_level,
        "required_human_review_level": draft.required_human_review_level,
        "supporting_evidence_refs": draft.supporting_evidence_refs,
        "originating_decision_ids": draft.originating_decision_ids,
        "explanation": draft.explanation,
        "suggested_experiment": draft.suggested_experiment,
        "evidence_state": draft.evidence_state,
        "state_reason": draft.state_reason,
        "provenance": draft.provenance,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"recommendation:{digest}"


async def _load_decisions(*, db: AsyncSession, decision_ids: list[uuid.UUID] | None) -> list[DecisionRecord]:
    statement = select(DecisionRecord).order_by(DecisionRecord.timestamp.asc(), DecisionRecord.decision_id.asc())
    if decision_ids:
        unique_ids = sorted(set(decision_ids))
        statement = statement.where(DecisionRecord.decision_id.in_(unique_ids))

    result = await db.execute(statement)
    return list(result.scalars().all())


async def _load_latest_quality_score(*, db: AsyncSession, decision_id: uuid.UUID) -> DecisionQualityScore | None:
    return await db.scalar(
        select(DecisionQualityScore)
        .where(DecisionQualityScore.decision_id == decision_id)
        .order_by(DecisionQualityScore.created_at.desc(), DecisionQualityScore.id.desc())
        .limit(1)
    )


async def _load_counterfactuals(*, db: AsyncSession, decision_id: uuid.UUID) -> list[DecisionCounterfactualResult]:
    result = await db.execute(
        select(DecisionCounterfactualResult)
        .where(DecisionCounterfactualResult.decision_id == decision_id)
        .order_by(DecisionCounterfactualResult.horizon_minutes.asc(), DecisionCounterfactualResult.id.asc())
    )
    return list(result.scalars().all())


def _quality_value(*, quality_score: DecisionQualityScore | None) -> Decimal:
    if quality_score is None:
        return Decimal("0.5000")
    return Decimal(str(quality_score.composite_score))


def _confidence_level(
    *,
    quality_value: Decimal,
    counterfactual_results: list[DecisionCounterfactualResult],
) -> str:
    resolved = [item for item in counterfactual_results if item.evaluation_state == "resolved"]
    if not resolved:
        return "low"

    correctness_ratio = Decimal(
        str(sum(1 for item in resolved if item.actual_action_correct is True) / len(resolved))
    )

    if quality_value >= Decimal("0.7500") and correctness_ratio >= Decimal("0.6600"):
        return "high"
    if quality_value >= Decimal("0.5500") and correctness_ratio >= Decimal("0.3300"):
        return "medium"
    return "low"


def _lesson_tags(counterfactual_results: list[DecisionCounterfactualResult]) -> set[str]:
    tags: set[str] = set()
    for item in counterfactual_results:
        for tag_entry in item.lesson_tags or []:
            tag = tag_entry.get("tag")
            if isinstance(tag, str) and tag:
                tags.add(tag)
    return tags


def _build_supporting_refs(
    *,
    quality_score: DecisionQualityScore | None,
    counterfactual_results: list[DecisionCounterfactualResult],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if quality_score is None:
        refs.append({"source": "decision_quality_scores", "state": "unavailable", "reason": "not_found"})
    else:
        refs.append(
            {
                "source": "decision_quality_scores",
                "record_id": str(quality_score.id),
                "field": "composite_score",
                "value": format(Decimal(str(quality_score.composite_score)), "f"),
                "state": "known",
            }
        )

    if not counterfactual_results:
        refs.append({"source": "decision_counterfactual_results", "state": "unavailable", "reason": "not_found"})
    else:
        for item in counterfactual_results:
            refs.append(
                {
                    "source": "decision_counterfactual_results",
                    "record_id": str(item.id),
                    "horizon_minutes": item.horizon_minutes,
                    "evaluation_state": item.evaluation_state,
                    "actual_action_correct": item.actual_action_correct,
                    "state": "known" if item.evaluation_state == "resolved" else "unknown",
                }
            )

    refs.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return refs
