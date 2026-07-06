from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_cycle import ArenaCycle
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.arena_tournament import ArenaTournament
from app.services.arena.contracts import (
    ArenaAgentProposalContract,
    ArenaCycleOrchestrationResult,
    ArenaCycleSnapshotContract,
)
from app.services.arena.identity import (
    build_arena_cycle_idempotency_key,
    build_arena_lifecycle_identity,
)


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def build_deterministic_snapshot_hash(
    *,
    market_data: dict[str, Any],
    portfolio_state: dict[str, Any],
    risk_constraints: dict[str, Any],
    cycle_timestamp: datetime,
    participating_agent_ids: list[uuid.UUID],
) -> str:
    payload = {
        "market_data": market_data,
        "portfolio_state": portfolio_state,
        "risk_constraints": risk_constraints,
        "cycle_timestamp": cycle_timestamp.isoformat(),
        "participating_agent_ids": [str(item) for item in sorted(participating_agent_ids, key=str)],
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _build_cycle_idempotency_key(
    *,
    tournament_id: uuid.UUID,
    cycle_number: int,
    deterministic_snapshot_hash: str,
) -> str:
    payload = {
        "kind": "arena_cycle_orchestration",
        "tournament_id": str(tournament_id),
        "cycle_number": cycle_number,
        "deterministic_snapshot_hash": deterministic_snapshot_hash,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _proposal_idempotency_key(*, cycle_id: uuid.UUID, proposal: ArenaAgentProposalContract) -> str:
    payload = {
        "kind": "arena_cycle_proposal",
        "cycle_id": str(cycle_id),
        "agent_id": str(proposal.agent_id),
        "action": proposal.action,
        "payload": proposal.payload,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


async def orchestrate_arena_cycle(
    *,
    db: AsyncSession,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_number: int,
    snapshot: ArenaCycleSnapshotContract,
    proposals: list[ArenaAgentProposalContract],
) -> ArenaCycleOrchestrationResult:
    tournament = await db.scalar(
        select(ArenaTournament)
        .where(ArenaTournament.id == tournament_id, ArenaTournament.competition_id == competition_id)
        .limit(1)
    )
    if tournament is None:
        raise InvalidRequestError("Tournament does not belong to competition")

    participants_result = await db.execute(
        select(ArenaParticipatingAgent)
        .where(ArenaParticipatingAgent.competition_id == competition_id)
        .order_by(ArenaParticipatingAgent.agent_identity.asc())
    )
    participants = list(participants_result.scalars().all())
    if not participants:
        raise InvalidRequestError("No participating agents registered for competition")

    participant_agent_ids = [uuid.UUID(item.agent_identity) for item in participants]
    registration_states_result = await db.execute(
        select(ArenaAgentRegistration)
        .where(
            ArenaAgentRegistration.competition_id == competition_id,
            ArenaAgentRegistration.agent_id.in_(participant_agent_ids),
        )
    )
    registrations = list(registration_states_result.scalars().all())
    registration_by_agent: dict[uuid.UUID, ArenaAgentRegistration] = {
        item.agent_id: item for item in registrations
    }

    for agent_id in sorted(participant_agent_ids, key=str):
        registration = registration_by_agent.get(agent_id)
        if registration is None:
            raise InvalidRequestError(f"Agent {agent_id} missing registration record")
        if registration.eligibility_status != "accepted":
            reason = registration.rejection_reason or "registration_not_accepted"
            raise InvalidRequestError(f"Agent {agent_id} registration rejected: {reason}")

    ordered_agent_ids = sorted(participant_agent_ids, key=str)

    provided_proposals = {proposal.agent_id: proposal for proposal in proposals}
    if sorted(provided_proposals.keys(), key=str) != ordered_agent_ids:
        raise InvalidRequestError("Proposals must exist for every accepted participating agent")

    for proposal in proposals:
        if proposal.action not in {"buy", "sell", "wait"}:
            raise InvalidRequestError("Proposal action must be one of buy, sell, wait")

    deterministic_snapshot_hash = build_deterministic_snapshot_hash(
        market_data=snapshot.market_data,
        portfolio_state=snapshot.portfolio_state,
        risk_constraints=snapshot.risk_constraints,
        cycle_timestamp=snapshot.cycle_timestamp,
        participating_agent_ids=ordered_agent_ids,
    )
    cycle_idempotency_key = _build_cycle_idempotency_key(
        tournament_id=tournament_id,
        cycle_number=cycle_number,
        deterministic_snapshot_hash=deterministic_snapshot_hash,
    )

    existing_cycle = await db.scalar(
        select(ArenaCycle)
        .where(ArenaCycle.idempotency_key == cycle_idempotency_key)
        .limit(1)
    )
    if existing_cycle is not None:
        existing_proposals_result = await db.execute(
            select(ArenaCycleProposal).where(ArenaCycleProposal.cycle_id == existing_cycle.id)
        )
        existing_proposals = list(existing_proposals_result.scalars().all())
        return ArenaCycleOrchestrationResult(
            cycle_id=existing_cycle.id,
            competition_id=competition_id,
            tournament_id=tournament_id,
            deterministic_snapshot_hash=deterministic_snapshot_hash,
            participating_agent_ids=ordered_agent_ids,
            provenance_metadata=existing_cycle.provenance,
            proposals_captured=len(existing_proposals),
        )

    cycle_identity = build_arena_lifecycle_identity(
        namespace="cycle",
        competition_identity=str(competition_id),
        ordinal=cycle_number,
        as_of=snapshot.cycle_timestamp,
    )
    cycle_model_identity_key = build_arena_cycle_idempotency_key(
        cycle_identity=cycle_identity,
        tournament_identity=str(tournament_id),
        cycle_number=cycle_number,
    )
    provenance_metadata: dict[str, Any] = {
        "deterministic_snapshot_hash": deterministic_snapshot_hash,
        "cycle_timestamp": snapshot.cycle_timestamp.isoformat(),
        "participating_agent_ids": [str(item) for item in ordered_agent_ids],
        "snapshot_distribution": {
            "market_data": snapshot.market_data,
            "portfolio_state": snapshot.portfolio_state,
            "risk_constraints": snapshot.risk_constraints,
            "uniform_for_all_agents": True,
        },
    }

    proposals_captured = 0
    async with db.begin():
        cycle = ArenaCycle(
            idempotency_key=cycle_idempotency_key,
            cycle_identity=cycle_identity,
            tournament_id=tournament_id,
            cycle_number=cycle_number,
            status="planned",
            config={
                "cycle_orchestration_idempotency_key": cycle_idempotency_key,
                "cycle_model_identity_key": cycle_model_identity_key,
                "deterministic_snapshot_hash": deterministic_snapshot_hash,
            },
            provenance=provenance_metadata,
            created_at=snapshot.cycle_timestamp,
        )
        db.add(cycle)
        await db.flush()

        for agent_id in ordered_agent_ids:
            proposal = provided_proposals[agent_id]
            db.add(
                ArenaCycleProposal(
                    idempotency_key=_proposal_idempotency_key(cycle_id=cycle.id, proposal=proposal),
                    cycle_id=cycle.id,
                    competition_id=competition_id,
                    tournament_id=tournament_id,
                    agent_id=agent_id,
                    proposal_action=proposal.action,
                    proposal_payload=proposal.payload,
                    provenance={
                        "cycle_timestamp": snapshot.cycle_timestamp.isoformat(),
                        "deterministic_snapshot_hash": deterministic_snapshot_hash,
                    },
                    created_at=snapshot.cycle_timestamp,
                )
            )
            proposals_captured += 1

    return ArenaCycleOrchestrationResult(
        cycle_id=cycle.id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        deterministic_snapshot_hash=deterministic_snapshot_hash,
        participating_agent_ids=ordered_agent_ids,
        provenance_metadata=provenance_metadata,
        proposals_captured=proposals_captured,
    )