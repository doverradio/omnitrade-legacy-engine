from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord


FOUR_DP = Decimal("0.0001")
ONE = Decimal("1")
ZERO = Decimal("0")

DEFAULT_SCORING_MODEL_VERSION = "dqe_v1"
DEFAULT_COMPONENT_WEIGHTS: dict[str, Decimal] = {
    "rule_compliance": Decimal("0.18"),
    "risk_discipline": Decimal("0.16"),
    "explainability_completeness": Decimal("0.16"),
    "counterfactual_outcome_quality": Decimal("0.18"),
    "consistency": Decimal("0.10"),
    "execution_correctness": Decimal("0.12"),
    "profit_contribution": Decimal("0.10"),
}


@dataclass(frozen=True, slots=True)
class DecisionQualityComponentScore:
    name: str
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class DecisionQualityScoreDraft:
    scoring_model_version: str
    composite_score: Decimal
    component_scores: list[DecisionQualityComponentScore]
    weight_profile: dict[str, Decimal]
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DecisionQualityReadModel:
    decision_id: uuid.UUID
    scoring_model_version: str
    composite_score: Decimal
    component_scores: list[dict[str, Any]]
    weight_profile: dict[str, str]
    provenance: dict[str, Any]


async def persist_decision_quality_score(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
    scoring_model_version: str = DEFAULT_SCORING_MODEL_VERSION,
    component_weights: dict[str, Decimal] | None = None,
) -> bool:
    decision = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision is None:
        return False

    explainability_result = await db.execute(
        select(DecisionExplainabilityRecord)
        .where(DecisionExplainabilityRecord.decision_id == decision_id)
        .order_by(DecisionExplainabilityRecord.created_at.asc(), DecisionExplainabilityRecord.id.asc())
    )
    explainability_records = list(explainability_result.scalars().all())

    counterfactual_result = await db.execute(
        select(DecisionCounterfactualResult)
        .where(DecisionCounterfactualResult.decision_id == decision_id)
        .order_by(DecisionCounterfactualResult.horizon_minutes.asc(), DecisionCounterfactualResult.id.asc())
    )
    counterfactual_records = list(counterfactual_result.scalars().all())

    weights = _normalize_weights(component_weights=component_weights)
    draft = build_decision_quality_score_draft(
        decision_record=decision,
        explainability_records=explainability_records,
        counterfactual_records=counterfactual_records,
        scoring_model_version=scoring_model_version,
        component_weights=weights,
    )

    idempotency_key = build_decision_quality_idempotency_key(
        decision_id=decision_id,
        scoring_model_version=scoring_model_version,
        component_scores=draft.component_scores,
        weight_profile=draft.weight_profile,
        provenance=draft.provenance,
    )

    existing = await db.scalar(
        select(DecisionQualityScore.id)
        .where(DecisionQualityScore.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return False

    async with db.begin():
        db.add(
            DecisionQualityScore(
                decision_id=decision_id,
                idempotency_key=idempotency_key,
                scoring_model_version=draft.scoring_model_version,
                composite_score=draft.composite_score,
                component_scores=_serialize_components(draft.component_scores),
                weight_profile={k: _to_str(v) for k, v in sorted(draft.weight_profile.items())},
                provenance=draft.provenance,
            )
        )

    return True


async def read_latest_decision_quality_score(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> DecisionQualityReadModel | None:
    record = await db.scalar(
        select(DecisionQualityScore)
        .where(DecisionQualityScore.decision_id == decision_id)
        .order_by(DecisionQualityScore.created_at.desc(), DecisionQualityScore.id.desc())
        .limit(1)
    )
    if record is None:
        return None

    return DecisionQualityReadModel(
        decision_id=record.decision_id,
        scoring_model_version=record.scoring_model_version,
        composite_score=record.composite_score,
        component_scores=list(record.component_scores),
        weight_profile=dict(record.weight_profile),
        provenance=dict(record.provenance),
    )


def build_decision_quality_score_draft(
    *,
    decision_record: DecisionRecord,
    explainability_records: list[DecisionExplainabilityRecord],
    counterfactual_records: list[DecisionCounterfactualResult],
    scoring_model_version: str = DEFAULT_SCORING_MODEL_VERSION,
    component_weights: dict[str, Decimal] | None = None,
) -> DecisionQualityScoreDraft:
    weights = _normalize_weights(component_weights=component_weights)

    component_values: dict[str, tuple[Decimal, str]] = {
        "rule_compliance": _score_rule_compliance(decision_record=decision_record),
        "risk_discipline": _score_risk_discipline(decision_record=decision_record),
        "explainability_completeness": _score_explainability_completeness(
            explainability_records=explainability_records,
        ),
        "counterfactual_outcome_quality": _score_counterfactual_outcome_quality(
            counterfactual_records=counterfactual_records,
        ),
        "consistency": _score_consistency(counterfactual_records=counterfactual_records),
        "execution_correctness": _score_execution_correctness(decision_record=decision_record),
        "profit_contribution": _score_profit_contribution(decision_record=decision_record),
    }

    component_scores: list[DecisionQualityComponentScore] = []
    weighted_sum = ZERO
    for name in sorted(component_values):
        score, rationale = component_values[name]
        weight = weights[name]
        weighted = _quantize(score * weight)
        component_scores.append(
            DecisionQualityComponentScore(
                name=name,
                score=_quantize(score),
                weight=_quantize(weight),
                weighted_score=weighted,
                rationale=rationale,
            )
        )
        weighted_sum += weighted

    provenance = {
        "source_ids": {
            "decision_record": str(decision_record.decision_id),
            "explainability_records": sorted(str(item.id) for item in explainability_records),
            "counterfactual_results": sorted(str(item.id) for item in counterfactual_records),
        },
        "lineage": {
            "decision_record_lineage": decision_record.source_lineage,
            "counterfactual_horizons": sorted(item.horizon_minutes for item in counterfactual_records),
            "explainability_roles": sorted(item.evidence_role for item in explainability_records),
        },
        "scoring_model_version": scoring_model_version,
    }

    return DecisionQualityScoreDraft(
        scoring_model_version=scoring_model_version,
        composite_score=_quantize(weighted_sum),
        component_scores=component_scores,
        weight_profile=weights,
        provenance=provenance,
    )


def build_decision_quality_idempotency_key(
    *,
    decision_id: uuid.UUID,
    scoring_model_version: str,
    component_scores: list[DecisionQualityComponentScore],
    weight_profile: dict[str, Decimal],
    provenance: dict[str, Any],
) -> str:
    payload = {
        "decision_id": str(decision_id),
        "scoring_model_version": scoring_model_version,
        "component_scores": _serialize_components(component_scores),
        "weight_profile": {k: _to_str(v) for k, v in sorted(weight_profile.items())},
        "provenance": provenance,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"dqe:{digest}"


def _score_rule_compliance(*, decision_record: DecisionRecord) -> tuple[Decimal, str]:
    action = _decision_action(decision_record=decision_record)
    if action == "wait" and not decision_record.trade_accepted:
        return Decimal("1.0"), "wait_action_without_execution"
    if decision_record.trade_accepted and not decision_record.trade_rejected_reason:
        return Decimal("1.0"), "accepted_without_conflicting_rejection"
    if not decision_record.trade_accepted and decision_record.trade_rejected_reason:
        return Decimal("0.9"), "explicit_rejection_reason_present"
    return Decimal("0.4"), "inconsistent_decision_state"


def _score_risk_discipline(*, decision_record: DecisionRecord) -> tuple[Decimal, str]:
    adjustments = decision_record.risk_adjustments or []
    if any(str(item.get("action_taken", "")).lower() == "resized" for item in adjustments):
        return Decimal("1.0"), "risk_resize_applied"
    if any(str(item.get("action_taken", "")).lower() == "blocked" for item in adjustments):
        return Decimal("1.0"), "risk_block_applied"
    if adjustments:
        return Decimal("0.8"), "risk_adjustments_present"
    if decision_record.trade_accepted:
        return Decimal("0.6"), "accepted_without_explicit_risk_adjustment"
    return Decimal("0.7"), "non_accepted_without_risk_events"


def _score_explainability_completeness(
    *,
    explainability_records: list[DecisionExplainabilityRecord],
) -> tuple[Decimal, str]:
    required_roles = {"supporting", "opposing", "confidence_factor", "risk_adjustment"}
    if not explainability_records:
        return Decimal("0.0"), "no_explainability_records"

    known_roles = {
        item.evidence_role
        for item in explainability_records
        if item.availability_state == "known"
    }
    covered = len(required_roles.intersection(known_roles))
    return Decimal(covered) / Decimal(len(required_roles)), "known_role_coverage"


def _score_counterfactual_outcome_quality(
    *,
    counterfactual_records: list[DecisionCounterfactualResult],
) -> tuple[Decimal, str]:
    resolved = [item for item in counterfactual_records if item.evaluation_state == "resolved"]
    if not resolved:
        return Decimal("0.5"), "no_resolved_counterfactuals"

    correct = [item for item in resolved if item.actual_action_correct is True]
    return Decimal(len(correct)) / Decimal(len(resolved)), "resolved_horizon_correctness_rate"


def _score_consistency(
    *,
    counterfactual_records: list[DecisionCounterfactualResult],
) -> tuple[Decimal, str]:
    resolved = [item for item in counterfactual_records if item.evaluation_state == "resolved"]
    if len(resolved) <= 1:
        return Decimal("0.5"), "insufficient_horizons_for_consistency"

    outcomes = {bool(item.actual_action_correct) for item in resolved}
    if len(outcomes) == 1:
        return Decimal("1.0"), "all_horizons_consistent"
    return Decimal("0.5"), "mixed_horizon_correctness"


def _score_execution_correctness(*, decision_record: DecisionRecord) -> tuple[Decimal, str]:
    action = _decision_action(decision_record=decision_record)
    has_execution = decision_record.execution_details is not None

    if action == "wait" and not has_execution:
        return Decimal("1.0"), "wait_without_execution"
    if action in {"buy", "sell"} and decision_record.trade_accepted and has_execution:
        return Decimal("1.0"), "accepted_action_has_execution_details"
    if action in {"buy", "sell"} and not decision_record.trade_accepted and not has_execution:
        return Decimal("0.9"), "non_accepted_action_has_no_execution"
    return Decimal("0.2"), "execution_state_mismatch"


def _score_profit_contribution(*, decision_record: DecisionRecord) -> tuple[Decimal, str]:
    pnl = decision_record.pnl or {}
    raw_pct = pnl.get("pct") or pnl.get("percentage")
    if raw_pct is None:
        return Decimal("0.5"), "profit_unknown_neutral"

    try:
        pct = Decimal(str(raw_pct))
    except Exception:
        return Decimal("0.5"), "profit_unparseable_neutral"

    clamped = min(max(pct, Decimal("-0.10")), Decimal("0.10"))
    normalized = (clamped + Decimal("0.10")) / Decimal("0.20")
    return normalized, "profit_factor_normalized"


def _decision_action(*, decision_record: DecisionRecord) -> str:
    action = "wait"
    if decision_record.generated_signals:
        first = decision_record.generated_signals[0]
        if isinstance(first, dict):
            raw = str(first.get("action") or "").lower()
            if raw in {"buy", "sell"}:
                action = raw
            elif raw in {"hold", "wait"}:
                action = "wait"
    return action


def _normalize_weights(*, component_weights: dict[str, Decimal] | None) -> dict[str, Decimal]:
    weights = dict(DEFAULT_COMPONENT_WEIGHTS)
    if component_weights is not None:
        weights.update(component_weights)

    keys = sorted(DEFAULT_COMPONENT_WEIGHTS)
    total = sum((weights[key] for key in keys), ZERO)
    if total <= ZERO:
        raise ValueError("component weights must have positive total")

    normalized = {key: _quantize(weights[key] / total) for key in keys}
    # Correct quantization drift by balancing the final key.
    drift = ONE - sum(normalized.values(), ZERO)
    last_key = keys[-1]
    normalized[last_key] = _quantize(normalized[last_key] + drift)

    return normalized


def _serialize_components(components: list[DecisionQualityComponentScore]) -> list[dict[str, str]]:
    return [
        {
            "name": item.name,
            "score": _to_str(item.score),
            "weight": _to_str(item.weight),
            "weighted_score": _to_str(item.weighted_score),
            "rationale": item.rationale,
        }
        for item in components
    ]


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def _to_str(value: Decimal) -> str:
    return format(value, "f")
