from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.decisions.package import DecisionPackageBuilder, DecisionPackageContract
from app.services.decisions.replay_candidates import ReplayCandidateReadModel, list_replay_candidates_v0
from app.services.replay.interface import ReplayAgent, ReplayResult


class ReplayPackageNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class DefaultReplayAgent(ReplayAgent):
    replay_agent_id: uuid.UUID
    name: str = "Default Replay Agent"
    status: str = "Registered"

    async def replay(self, *, db: AsyncSession, decision_package_id: str) -> ReplayResult:
        candidate = await _resolve_candidate(db=db, decision_package_id=decision_package_id)
        if candidate is None:
            raise ReplayPackageNotFoundError(decision_package_id)

        package = await DecisionPackageBuilder().build_decision_package(db=db, decision_id=candidate.decision_id)
        if package is None:
            raise ReplayPackageNotFoundError(str(decision_package_id))

        return _build_replay_result(
            package=package,
            candidate=candidate,
            replay_agent_id=self.replay_agent_id,
        )


async def replay_decision_package_v0(*, db: AsyncSession, decision_package_id: str) -> ReplayResult:
    agent = DefaultReplayAgent(replay_agent_id=DEFAULT_REPLAY_AGENT_ID)
    return await agent.replay(db=db, decision_package_id=decision_package_id)


DEFAULT_REPLAY_AGENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


async def _resolve_candidate(*, db: AsyncSession, decision_package_id: str) -> ReplayCandidateReadModel | None:
    candidates = await list_replay_candidates_v0(db=db)
    for candidate in candidates:
        if candidate.decision_package_id == str(decision_package_id):
            return candidate
    return None


def _build_replay_result(
    *,
    package: DecisionPackageContract,
    candidate: ReplayCandidateReadModel,
    replay_agent_id: uuid.UUID,
) -> ReplayResult:
    reconstructed_action = _reconstructed_action(package=package)
    reconstructed_confidence = package.decision_record.confidence
    replay_id = uuid.uuid5(uuid.UUID("00000000-0000-0000-0000-000000000000"), f"{replay_agent_id}:{candidate.decision_package_id}")

    supporting_evidence = [
        {
            "type": "decision_record",
            "decision_id": str(package.decision_id),
            "generated_signals": package.decision_record.generated_signals,
            "supporting_strategies": package.decision_record.supporting_strategies,
            "opposing_strategies": package.decision_record.opposing_strategies,
            "confidence": _decimal_to_str(package.decision_record.confidence),
        },
        {
            "type": "decision_snapshot",
            "available": package.decision_snapshot is not None,
            "strategy_inputs": package.decision_snapshot.strategy_inputs if package.decision_snapshot else {},
        },
        {
            "type": "availability_state",
            "value": {
                field.name: getattr(package.availability_state, field.name)
                for field in package.availability_state.__dataclass_fields__.values()
            },
        },
    ]

    explanation = (
        f"Replayed immutable decision package {candidate.decision_package_id} from decision {package.decision_id}. "
        f"Reconstructed action {reconstructed_action} from the original generated signal without mutation."
    )

    metadata = {
        "decision_id": str(package.decision_id),
        "decision_package_id": candidate.decision_package_id,
        "package_hash": candidate.package_hash,
        "package_version": candidate.package_version,
        "replay_ready": candidate.replay_ready,
        "replay_agent_name": "Default Replay Agent",
        "package_built_at": package.built_at.isoformat(),
    }

    return ReplayResult(
        replay_id=replay_id,
        replay_agent_id=replay_agent_id,
        decision_package_id=candidate.decision_package_id,
        replay_timestamp=datetime.now(timezone.utc),
        reconstructed_action=reconstructed_action,
        confidence=reconstructed_confidence,
        supporting_evidence=tuple(supporting_evidence),
        explanation=explanation,
        metadata=metadata,
    )


def _reconstructed_action(*, package: DecisionPackageContract) -> str:
    signals = package.decision_record.generated_signals
    if not signals:
        return "HOLD"

    first = signals[0]
    if not isinstance(first, dict):
        return "HOLD"

    action = first.get("action")
    if not isinstance(action, str) or not action.strip():
        return "HOLD"
    normalized = action.strip().upper()
    if normalized not in {"BUY", "SELL", "HOLD"}:
        return "HOLD"
    return normalized


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
