from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_record import DecisionRecord
from app.services.decisions.package import DecisionPackageBuilder, DecisionPackageContract


@dataclass(frozen=True, slots=True)
class ReplayCandidateReadModel:
    decision_package_id: str
    decision_id: uuid.UUID
    package_hash: str
    package_version: str
    replay_ready: bool
    missing_artifacts: list[str]
    unavailable_artifacts: list[str]
    candidate_reason: str
    created_at: datetime


async def list_replay_candidates_v0(
    *,
    db: AsyncSession,
) -> list[ReplayCandidateReadModel]:
    result = await db.execute(
        select(DecisionRecord.decision_id)
        .order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
    )
    decision_ids = list(result.scalars().all())

    items: list[ReplayCandidateReadModel] = []
    for decision_id in decision_ids:
        candidate = await certify_decision_package_readiness_v0(db=db, decision_id=decision_id)
        if candidate is not None:
            items.append(candidate)

    return items


async def certify_decision_package_readiness_v0(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> ReplayCandidateReadModel | None:
    builder = DecisionPackageBuilder()
    package = await builder.build_decision_package(db=db, decision_id=decision_id)
    if package is None:
        return None

    # Certification requires repeated assembly to verify deterministic package identity.
    package_repeat = await builder.build_decision_package(db=db, decision_id=decision_id)

    missing_artifacts, unavailable_artifacts = _collect_missing_and_unavailable(package)

    buildable = package is not None
    deterministic = (
        package_repeat is not None
        and package.content_hash == package_repeat.content_hash
        and package.schema_version == package_repeat.schema_version
    )
    hashable = bool(package.content_hash) and ":" in package.content_hash
    version_pinned = _is_version_pinned(package)
    explicit_missing_fields = _has_explicit_missing_field_states(package)
    safe_for_replay = buildable and deterministic and hashable and version_pinned and explicit_missing_fields

    if safe_for_replay and missing_artifacts:
        candidate_reason = "replay_ready_with_missing_optional_artifacts"
    elif safe_for_replay:
        candidate_reason = "replay_ready"
    else:
        failed_checks: list[str] = []
        if not buildable:
            failed_checks.append("buildable")
        if not deterministic:
            failed_checks.append("deterministic")
        if not hashable:
            failed_checks.append("hashable")
        if not version_pinned:
            failed_checks.append("version_pinned")
        if not explicit_missing_fields:
            failed_checks.append("explicit_missing_fields")
        candidate_reason = f"not_replay_ready:{','.join(failed_checks)}"

    decision_package_id = _build_decision_package_id(
        decision_id=package.decision_id,
        package_hash=package.content_hash,
        package_version=package.schema_version,
    )

    return ReplayCandidateReadModel(
        decision_package_id=decision_package_id,
        decision_id=package.decision_id,
        package_hash=package.content_hash,
        package_version=package.schema_version,
        replay_ready=safe_for_replay,
        missing_artifacts=missing_artifacts,
        unavailable_artifacts=unavailable_artifacts,
        candidate_reason=candidate_reason,
        created_at=package.built_at,
    )


def _build_decision_package_id(*, decision_id: uuid.UUID, package_hash: str, package_version: str) -> str:
    payload = f"{decision_id}:{package_hash}:{package_version}"
    digest = sha256(payload.encode("ascii"), usedforsecurity=False).hexdigest()
    return f"dpkg:{digest}"


def _collect_missing_and_unavailable(package: DecisionPackageContract) -> tuple[list[str], list[str]]:
    missing_artifacts: list[str] = []
    unavailable_artifacts: list[str] = []

    for field in fields(package.availability_state):
        state = getattr(package.availability_state, field.name)
        if state != "known":
            missing_artifacts.append(field.name)
        if state == "unavailable":
            unavailable_artifacts.append(field.name)

    return missing_artifacts, unavailable_artifacts


def _is_version_pinned(package: DecisionPackageContract) -> bool:
    if not package.schema_version:
        return False
    if not package.decision_record.version:
        return False

    if package.decision_snapshot is not None and not package.decision_snapshot.decision_engine_version:
        return False

    return True


def _has_explicit_missing_field_states(package: DecisionPackageContract) -> bool:
    valid_states = {"known", "unknown", "unavailable"}
    for field in fields(package.availability_state):
        if getattr(package.availability_state, field.name) not in valid_states:
            return False
    return True