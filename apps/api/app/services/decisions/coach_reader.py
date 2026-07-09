from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.decisions.package import DecisionPackageBuilder, DecisionPackageContract
from app.services.decisions.replay_candidates import ReplayCandidateReadModel, list_replay_candidates_v0


@dataclass(frozen=True, slots=True)
class CoachReplayReviewReadModel:
    decision_id: uuid.UUID
    decision_package_id: str
    package_hash: str
    package_version: str
    replay_ready: bool
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    missing_evidence: list[str]
    suggested_followups: list[str]
    advisory_only: bool


async def list_ai_coach_replay_reviews_v0(
    *,
    db: AsyncSession,
) -> list[CoachReplayReviewReadModel]:
    candidates = await list_replay_candidates_v0(db=db)
    if not candidates:
        return []

    builder = DecisionPackageBuilder()
    reviews: list[CoachReplayReviewReadModel] = []

    for candidate in candidates:
        package = await builder.build_decision_package(db=db, decision_id=candidate.decision_id)
        if package is None:
            continue
        reviews.append(_build_review(candidate=candidate, package=package))

    return reviews


def _build_review(*, candidate: ReplayCandidateReadModel, package: DecisionPackageContract) -> CoachReplayReviewReadModel:
    action = _signal_action(package=package)
    market_symbol = _asset_symbol(package=package)

    strengths: list[str] = []
    weaknesses: list[str] = []

    if candidate.replay_ready:
        strengths.append("decision_package_certification_passed")
    else:
        weaknesses.append("decision_package_certification_failed")

    if package.decision_snapshot is not None:
        strengths.append("decision_snapshot_available")
    else:
        weaknesses.append("decision_snapshot_unavailable")

    if package.decision_record.confidence is not None:
        strengths.append("confidence_recorded")
    else:
        weaknesses.append("confidence_unavailable")

    if package.explainability_records:
        strengths.append("explainability_evidence_available")
    else:
        weaknesses.append("explainability_evidence_unavailable")

    missing_evidence = _collect_missing_evidence(candidate=candidate, package=package)
    if missing_evidence:
        weaknesses.append("missing_optional_evidence_present")

    strengths = sorted(set(strengths))
    weaknesses = sorted(set(weaknesses))

    followups = _build_suggested_followups(missing_evidence=missing_evidence)

    summary = (
        f"Advisory review for {market_symbol} decision {package.decision_id}: "
        f"action={action}, replay_ready={str(candidate.replay_ready).lower()}, "
        f"missing_evidence_count={len(missing_evidence)}."
    )

    return CoachReplayReviewReadModel(
        decision_id=package.decision_id,
        decision_package_id=candidate.decision_package_id,
        package_hash=candidate.package_hash,
        package_version=candidate.package_version,
        replay_ready=candidate.replay_ready,
        summary=summary,
        strengths=strengths,
        weaknesses=weaknesses,
        missing_evidence=missing_evidence,
        suggested_followups=followups,
        advisory_only=True,
    )


def _signal_action(*, package: DecisionPackageContract) -> str:
    signals = package.decision_record.generated_signals
    if not signals:
        return "unknown"
    first = signals[0]
    if not isinstance(first, dict):
        return "unknown"
    value = first.get("action")
    if not isinstance(value, str) or not value:
        return "unknown"
    return value


def _asset_symbol(*, package: DecisionPackageContract) -> str:
    asset = package.decision_record.asset
    symbol = asset.get("symbol") if isinstance(asset, dict) else None
    if isinstance(symbol, str) and symbol:
        return symbol
    return "unknown_asset"


def _collect_missing_evidence(
    *,
    candidate: ReplayCandidateReadModel,
    package: DecisionPackageContract,
) -> list[str]:
    values = set(candidate.missing_artifacts)
    values.update(candidate.unavailable_artifacts)

    for field in fields(package.availability_state):
        state = getattr(package.availability_state, field.name)
        if state != "known":
            values.add(field.name)

    return sorted(values)


def _build_suggested_followups(*, missing_evidence: list[str]) -> list[str]:
    if not missing_evidence:
        return [
            "maintain_replay_readiness_monitoring",
            "run_human_review_for_pattern_validation",
        ]

    followups: list[str] = []
    for item in missing_evidence:
        followups.append(f"capture_or_link_{item}")

    followups.append("run_human_review_for_missing_evidence")
    return sorted(set(followups))