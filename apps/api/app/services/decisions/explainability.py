from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_record import DecisionRecord


EvidenceRole = Literal["supporting", "opposing", "confidence_factor", "risk_adjustment"]
AvailabilityState = Literal["known", "unknown", "unavailable"]
DecisionExplainabilityStatus = Literal["approved", "resized", "rejected", "wait", "unknown"]


@dataclass(frozen=True, slots=True)
class ExplainabilityEvidenceDraft:
    evidence_role: EvidenceRole
    evidence_name: str
    evidence_payload: dict[str, Any]
    provenance: dict[str, Any]
    availability_state: AvailabilityState
    state_reason: str | None


@dataclass(frozen=True, slots=True)
class DecisionExplainabilityReadModel:
    decision_id: uuid.UUID
    decision_status: DecisionExplainabilityStatus
    explanation: str
    supporting_evidence: list[dict[str, Any]]
    opposing_evidence: list[dict[str, Any]]
    confidence_factors: list[dict[str, Any]]
    risk_adjustments: list[dict[str, Any]]


async def persist_explainability_evidence_for_decision(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> int:
    decision_record = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision_record is None:
        return 0

    drafts = build_explainability_evidence_drafts(decision_record=decision_record)
    inserted_count = 0

    for index, draft in enumerate(drafts):
        idempotency_key = build_explainability_idempotency_key(
            decision_id=decision_id,
            evidence_role=draft.evidence_role,
            evidence_name=draft.evidence_name,
            evidence_payload=draft.evidence_payload,
            provenance=draft.provenance,
            availability_state=draft.availability_state,
            state_reason=draft.state_reason,
            index=index,
        )

        existing = await db.scalar(
            select(DecisionExplainabilityRecord.id)
            .where(DecisionExplainabilityRecord.idempotency_key == idempotency_key)
            .limit(1)
        )
        if existing is not None:
            continue

        async with db.begin():
            db.add(
                DecisionExplainabilityRecord(
                    decision_id=decision_id,
                    idempotency_key=idempotency_key,
                    evidence_role=draft.evidence_role,
                    evidence_name=draft.evidence_name,
                    evidence_payload=draft.evidence_payload,
                    provenance=draft.provenance,
                    availability_state=draft.availability_state,
                    state_reason=draft.state_reason,
                )
            )

        inserted_count += 1

    return inserted_count


def build_explainability_idempotency_key(
    *,
    decision_id: uuid.UUID,
    evidence_role: EvidenceRole,
    evidence_name: str,
    evidence_payload: dict[str, Any],
    provenance: dict[str, Any],
    availability_state: AvailabilityState,
    state_reason: str | None,
    index: int,
) -> str:
    serialized = json.dumps(
        {
            "decision_id": str(decision_id),
            "evidence_role": evidence_role,
            "evidence_name": evidence_name,
            "evidence_payload": evidence_payload,
            "provenance": provenance,
            "availability_state": availability_state,
            "state_reason": state_reason,
            "index": index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"explainability:{digest}"


def build_explainability_evidence_drafts(
    *,
    decision_record: DecisionRecord,
) -> list[ExplainabilityEvidenceDraft]:
    source_lineage = decision_record.source_lineage

    supporting = _build_role_drafts(
        evidence_role="supporting",
        raw_records=decision_record.supporting_strategies or [],
        source_lineage=source_lineage,
        unknown_reason="supporting_evidence_unknown",
        unavailable_reason="supporting_evidence_unavailable",
    )
    opposing = _build_role_drafts(
        evidence_role="opposing",
        raw_records=decision_record.opposing_strategies or [],
        source_lineage=source_lineage,
        unknown_reason="opposing_evidence_unknown",
        unavailable_reason="opposing_evidence_unavailable",
    )
    risk_adjustments = _build_role_drafts(
        evidence_role="risk_adjustment",
        raw_records=decision_record.risk_adjustments or [],
        source_lineage=source_lineage,
        unknown_reason="risk_adjustment_unknown",
        unavailable_reason="risk_adjustment_unavailable",
    )

    confidence_factors = _build_confidence_factor_drafts(decision_record=decision_record)

    return [*supporting, *opposing, *confidence_factors, *risk_adjustments]


def _build_confidence_factor_drafts(
    *,
    decision_record: DecisionRecord,
) -> list[ExplainabilityEvidenceDraft]:
    source_lineage = decision_record.source_lineage
    model_output_lineage = sorted(source_lineage.get("model_outputs", []))

    factors: list[dict[str, Any]] = []
    if decision_record.confidence is not None:
        factors.append({"factor": "ai_confidence", "value": _to_str(decision_record.confidence)})
    if decision_record.signal_strength is not None:
        factors.append({"factor": "signal_strength", "value": _to_str(decision_record.signal_strength)})
    if decision_record.confidence_calibration is not None:
        factors.append({"factor": "confidence_calibration", "value": decision_record.confidence_calibration})

    if factors:
        return [
            ExplainabilityEvidenceDraft(
                evidence_role="confidence_factor",
                evidence_name=item["factor"],
                evidence_payload={"value": item["value"]},
                provenance={
                    "source_refs": ["decision_record.confidence", "decision_record.signal_strength"],
                    "record_ids": {"model_outputs": model_output_lineage},
                },
                availability_state="known",
                state_reason=None,
            )
            for item in factors
        ]

    availability_state: AvailabilityState
    state_reason: str
    # Preserve explicit state semantics when confidence factors are absent.
    if model_output_lineage:
        availability_state = "unknown"
        state_reason = "confidence_factor_unknown"
    else:
        availability_state = "unavailable"
        state_reason = "confidence_factor_unavailable"

    return [
        ExplainabilityEvidenceDraft(
            evidence_role="confidence_factor",
            evidence_name="confidence_factor_state",
            evidence_payload={"value": None},
            provenance={
                "source_refs": ["decision_record.confidence", "decision_record.signal_strength"],
                "record_ids": {"model_outputs": model_output_lineage},
            },
            availability_state=availability_state,
            state_reason=state_reason,
        )
    ]


def _build_role_drafts(
    *,
    evidence_role: Literal["supporting", "opposing", "risk_adjustment"],
    raw_records: list[dict[str, Any]],
    source_lineage: dict[str, list[str]],
    unknown_reason: str,
    unavailable_reason: str,
) -> list[ExplainabilityEvidenceDraft]:
    relevant_lineage = _lineage_for_role(evidence_role=evidence_role, source_lineage=source_lineage)

    if raw_records:
        drafts: list[ExplainabilityEvidenceDraft] = []
        for index, payload in enumerate(raw_records):
            evidence_name = str(payload.get("model_name") or payload.get("event_type") or f"{evidence_role}_{index}")
            drafts.append(
                ExplainabilityEvidenceDraft(
                    evidence_role=evidence_role,
                    evidence_name=evidence_name,
                    evidence_payload=payload,
                    provenance={
                        "source_refs": _source_refs_for_role(evidence_role=evidence_role),
                        "record_ids": relevant_lineage,
                    },
                    availability_state="known",
                    state_reason=None,
                )
            )
        return drafts

    if any(relevant_lineage.values()):
        state: AvailabilityState = "unknown"
        reason = unknown_reason
    else:
        state = "unavailable"
        reason = unavailable_reason

    return [
        ExplainabilityEvidenceDraft(
            evidence_role=evidence_role,
            evidence_name=f"{evidence_role}_state",
            evidence_payload={"value": None},
            provenance={
                "source_refs": _source_refs_for_role(evidence_role=evidence_role),
                "record_ids": relevant_lineage,
            },
            availability_state=state,
            state_reason=reason,
        )
    ]


def _source_refs_for_role(*, evidence_role: EvidenceRole) -> list[str]:
    if evidence_role in {"supporting", "opposing"}:
        return ["decision_record.supporting_strategies", "decision_record.opposing_strategies"]
    if evidence_role == "risk_adjustment":
        return ["decision_record.risk_adjustments"]
    return ["decision_record.confidence", "decision_record.signal_strength", "decision_record.confidence_calibration"]


def _lineage_for_role(
    *,
    evidence_role: EvidenceRole,
    source_lineage: dict[str, list[str]],
) -> dict[str, list[str]]:
    if evidence_role in {"supporting", "opposing"}:
        return {
            "signals": sorted(source_lineage.get("signals", [])),
            "model_outputs": sorted(source_lineage.get("model_outputs", [])),
        }
    if evidence_role == "risk_adjustment":
        return {
            "risk_events": sorted(source_lineage.get("risk_events", [])),
        }
    return {
        "signals": sorted(source_lineage.get("signals", [])),
        "model_outputs": sorted(source_lineage.get("model_outputs", [])),
    }


async def read_decision_explainability(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> DecisionExplainabilityReadModel | None:
    decision_record = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision_record is None:
        return None

    result = await db.execute(
        select(DecisionExplainabilityRecord)
        .where(DecisionExplainabilityRecord.decision_id == decision_id)
        .order_by(
            DecisionExplainabilityRecord.created_at.asc(),
            DecisionExplainabilityRecord.id.asc(),
        )
    )
    records = list(result.scalars().all())

    # Ensure the read model can always represent explainability states explicitly.
    if not records:
        return DecisionExplainabilityReadModel(
            decision_id=decision_id,
            decision_status=_resolve_decision_status(decision_record=decision_record),
            explanation="Explainability evidence unavailable for decision record.",
            supporting_evidence=[{"state": "unavailable", "reason": "supporting_evidence_unavailable"}],
            opposing_evidence=[{"state": "unavailable", "reason": "opposing_evidence_unavailable"}],
            confidence_factors=[{"state": "unavailable", "reason": "confidence_factor_unavailable"}],
            risk_adjustments=[{"state": "unavailable", "reason": "risk_adjustment_unavailable"}],
        )

    grouped: dict[str, list[dict[str, Any]]] = {
        "supporting": [],
        "opposing": [],
        "confidence_factor": [],
        "risk_adjustment": [],
    }
    for item in records:
        grouped[item.evidence_role].append(
            {
                "evidence_name": item.evidence_name,
                "evidence_payload": item.evidence_payload,
                "provenance": item.provenance,
                "availability_state": item.availability_state,
                "state_reason": item.state_reason,
            }
        )

    decision_status = _resolve_decision_status(decision_record=decision_record)
    explanation = _decision_explanation_text(decision_status=decision_status)

    return DecisionExplainabilityReadModel(
        decision_id=decision_id,
        decision_status=decision_status,
        explanation=explanation,
        supporting_evidence=grouped["supporting"],
        opposing_evidence=grouped["opposing"],
        confidence_factors=grouped["confidence_factor"],
        risk_adjustments=grouped["risk_adjustment"],
    )


def _resolve_decision_status(*, decision_record: DecisionRecord) -> DecisionExplainabilityStatus:
    action = None
    if decision_record.generated_signals:
        first = decision_record.generated_signals[0]
        if isinstance(first, dict):
            action = first.get("action")

    if action == "hold":
        return "wait"

    if decision_record.trade_accepted:
        resized = any(
            str(item.get("action_taken", "")).lower() == "resized"
            for item in (decision_record.risk_adjustments or [])
        )
        return "resized" if resized else "approved"

    if decision_record.trade_rejected_reason:
        return "rejected"

    return "unknown"


def _decision_explanation_text(*, decision_status: DecisionExplainabilityStatus) -> str:
    if decision_status == "approved":
        return "Decision approved based on supporting evidence, confidence factors, and risk adjustments."
    if decision_status == "resized":
        return "Decision approved with resize due to risk adjustment evidence."
    if decision_status == "rejected":
        return "Decision rejected due to opposing evidence and/or risk adjustment outcomes."
    if decision_status == "wait":
        return "WAIT decision selected because available evidence did not justify action."
    return "Decision status unknown due to incomplete or unavailable explainability evidence."


def _to_str(value: Any) -> str:
    return format(value, "f") if hasattr(value, "as_tuple") else str(value)
