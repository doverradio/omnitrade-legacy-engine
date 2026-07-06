from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.services.arena.contracts import (
    ArenaAgentRegistrationRequest,
    ArenaAgentRegistrationResult,
    ArenaAgentVersionIdentityContract,
    ArenaEligibilityResult,
)
from app.services.arena.identity import build_arena_participating_agent_idempotency_key


def build_registration_hash(
    *,
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    semantic_version: str,
    created_at: datetime,
    provenance_metadata: dict[str, object],
    registration_source: str,
) -> str:
    payload = json.dumps(
        {
            "agent_id": str(agent_id),
            "version_id": str(version_id),
            "semantic_version": semantic_version,
            "created_at": created_at.isoformat(),
            "provenance_metadata": provenance_metadata,
            "registration_source": registration_source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_registration_idempotency_key(
    *,
    competition_id: uuid.UUID,
    strategy_id: str,
    strategy_version: str,
    semantic_version: str,
    registration_source: str,
) -> str:
    payload = json.dumps(
        {
            "competition_id": str(competition_id),
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "semantic_version": semantic_version,
            "registration_source": registration_source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_arena_eligibility(
    *,
    paper_only_eligible: bool,
    strategy_version_registered: bool,
    immutable_version_identity: bool,
    live_capital_eligible: bool,
    autonomous_self_modifying: bool,
    human_governed: bool,
    registration_source: str,
) -> ArenaEligibilityResult:
    if not paper_only_eligible:
        return ArenaEligibilityResult(eligible=False, rejection_reason="paper_only_competitions_required")
    if not strategy_version_registered:
        return ArenaEligibilityResult(eligible=False, rejection_reason="registered_strategy_version_required")
    if not immutable_version_identity:
        return ArenaEligibilityResult(eligible=False, rejection_reason="immutable_version_identity_required")
    if live_capital_eligible:
        return ArenaEligibilityResult(eligible=False, rejection_reason="live_capital_eligibility_not_allowed")
    if autonomous_self_modifying:
        return ArenaEligibilityResult(eligible=False, rejection_reason="autonomous_agents_not_allowed")
    if not human_governed:
        return ArenaEligibilityResult(eligible=False, rejection_reason="human_governed_registration_required")
    if not registration_source.startswith("human_"):
        return ArenaEligibilityResult(eligible=False, rejection_reason="human_registration_source_required")
    return ArenaEligibilityResult(eligible=True, rejection_reason=None)


async def register_arena_agent(
    *,
    db: AsyncSession,
    request: ArenaAgentRegistrationRequest,
) -> ArenaAgentRegistrationResult:
    idempotency_key = request.idempotency_key or build_registration_idempotency_key(
        competition_id=request.competition_id,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
        semantic_version=request.semantic_version,
        registration_source=request.registration_source,
    )

    existing = await db.scalar(
        select(ArenaAgentRegistration)
        .where(ArenaAgentRegistration.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        identity = ArenaAgentVersionIdentityContract(
            agent_id=existing.agent_id,
            version_id=existing.version_id,
            semantic_version=existing.semantic_version,
            created_at=existing.created_at,
            provenance_metadata=existing.provenance_metadata,
            registration_source=existing.registration_source,
            registration_hash=existing.registration_hash,
        )
        participating_agent = await db.scalar(
            select(ArenaParticipatingAgent.id)
            .where(
                ArenaParticipatingAgent.agent_identity == str(existing.agent_id),
                ArenaParticipatingAgent.competition_id == existing.competition_id,
            )
            .limit(1)
        )
        return ArenaAgentRegistrationResult(
            accepted=existing.eligibility_status == "accepted",
            identity=identity,
            rejection_reason=existing.rejection_reason,
            registration_record_id=existing.id,
            participating_agent_id=participating_agent,
        )

    strategy_exists = await _strategy_version_exists(
        db=db,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
    )

    agent_id = uuid.uuid4()
    version_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    registration_hash = build_registration_hash(
        agent_id=agent_id,
        version_id=version_id,
        semantic_version=request.semantic_version,
        created_at=created_at,
        provenance_metadata=request.provenance_metadata,
        registration_source=request.registration_source,
    )
    identity = ArenaAgentVersionIdentityContract(
        agent_id=agent_id,
        version_id=version_id,
        semantic_version=request.semantic_version,
        created_at=created_at,
        provenance_metadata=request.provenance_metadata,
        registration_source=request.registration_source,
        registration_hash=registration_hash,
    )
    eligibility = validate_arena_eligibility(
        paper_only_eligible=request.paper_only_eligible,
        strategy_version_registered=strategy_exists,
        immutable_version_identity=True,
        live_capital_eligible=request.live_capital_eligible,
        autonomous_self_modifying=request.autonomous_self_modifying,
        human_governed=request.human_governed,
        registration_source=request.registration_source,
    )

    registration_record = ArenaAgentRegistration(
        idempotency_key=idempotency_key,
        competition_id=request.competition_id,
        agent_id=identity.agent_id,
        version_id=identity.version_id,
        semantic_version=identity.semantic_version,
        created_at=identity.created_at,
        provenance_metadata=identity.provenance_metadata,
        registration_source=identity.registration_source,
        registration_hash=identity.registration_hash,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
        paper_only_eligible=request.paper_only_eligible,
        live_capital_eligible=request.live_capital_eligible,
        human_governed=request.human_governed,
        autonomous_self_modifying=request.autonomous_self_modifying,
        eligibility_status="accepted" if eligibility.eligible else "rejected",
        rejection_reason=eligibility.rejection_reason,
    )

    participating_agent_id: uuid.UUID | None = None
    async with db.begin():
        db.add(registration_record)
        await db.flush()

        if eligibility.eligible:
            participant = ArenaParticipatingAgent(
                idempotency_key=build_arena_participating_agent_idempotency_key(
                    agent_identity=str(identity.agent_id),
                    competition_identity=str(request.competition_id),
                    strategy_id=request.strategy_id,
                    strategy_version=request.strategy_version,
                ),
                agent_identity=str(identity.agent_id),
                competition_id=request.competition_id,
                strategy_id=request.strategy_id,
                strategy_version=request.strategy_version,
                agent_role="participant",
                config={
                    "version_id": str(identity.version_id),
                    "semantic_version": identity.semantic_version,
                    "registration_hash": identity.registration_hash,
                },
                provenance={
                    "registration_source": identity.registration_source,
                    "provenance_metadata": identity.provenance_metadata,
                },
            )
            db.add(participant)
            await db.flush()
            participating_agent_id = participant.id

        db.add(
            AuditLog(
                actor=request.requested_by,
                action=(
                    "arena.agent_registration.accepted"
                    if eligibility.eligible
                    else "arena.agent_registration.rejected"
                ),
                entity_type="arena_agent_registration",
                entity_id=registration_record.id,
                before_state=None,
                after_state={
                    "competition_id": str(request.competition_id),
                    "agent_id": str(identity.agent_id),
                    "version_id": str(identity.version_id),
                    "semantic_version": identity.semantic_version,
                    "registration_source": identity.registration_source,
                    "registration_hash": identity.registration_hash,
                    "eligibility_status": registration_record.eligibility_status,
                    "rejection_reason": registration_record.rejection_reason,
                },
            )
        )

    return ArenaAgentRegistrationResult(
        accepted=eligibility.eligible,
        identity=identity,
        rejection_reason=eligibility.rejection_reason,
        registration_record_id=registration_record.id,
        participating_agent_id=participating_agent_id,
    )


async def _strategy_version_exists(*, db: AsyncSession, strategy_id: str, strategy_version: str) -> bool:
    strategy_pk = await db.scalar(
        select(Strategy.id)
        .where(Strategy.slug == strategy_id, Strategy.module_version == strategy_version)
        .limit(1)
    )
    return strategy_pk is not None