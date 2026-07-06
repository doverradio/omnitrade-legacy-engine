from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.arena_agent_budget_assignment import ArenaAgentBudgetAssignment
from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_competition import ArenaCompetition
from app.models.arena_competition_budget_allocation import ArenaCompetitionBudgetAllocation
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.services.arena.contracts import (
    ArenaCompetitionAllocationRequest,
    ArenaCompetitionAllocationResult,
)


def _build_assignment_idempotency_key(
    *,
    competition_budget_allocation_id: uuid.UUID,
    competition_id: uuid.UUID,
    agent_id: uuid.UUID,
    assigned_budget: Decimal,
) -> str:
    payload = json.dumps(
        {
            "competition_budget_allocation_id": str(competition_budget_allocation_id),
            "competition_id": str(competition_id),
            "agent_id": str(agent_id),
            "assigned_budget": format(assigned_budget, "f"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def allocate_competition_paper_budget(
    *,
    db: AsyncSession,
    request: ArenaCompetitionAllocationRequest,
) -> ArenaCompetitionAllocationResult:
    competition = await db.scalar(
        select(ArenaCompetition).where(ArenaCompetition.id == request.competition_id).limit(1)
    )
    if competition is None:
        raise InvalidRequestError("Arena competition not found")

    paper_account = await db.scalar(
        select(PaperAccount).where(PaperAccount.id == competition.paper_portfolio_id).limit(1)
    )
    if paper_account is None:
        raise InvalidRequestError("Paper portfolio not found for competition")

    existing = await db.scalar(
        select(ArenaCompetitionBudgetAllocation)
        .where(ArenaCompetitionBudgetAllocation.idempotency_key == request.idempotency_key)
        .limit(1)
    )
    if existing is not None:
        existing_assignments_result = await db.execute(
            select(ArenaAgentBudgetAssignment).where(
                ArenaAgentBudgetAssignment.competition_budget_allocation_id == existing.id
            )
        )
        existing_assignments = list(existing_assignments_result.scalars().all())
        total_assigned = sum((item.assigned_budget for item in existing_assignments), start=Decimal("0"))
        return ArenaCompetitionAllocationResult(
            competition_budget_allocation_id=existing.id,
            competition_id=existing.competition_id,
            paper_portfolio_id=existing.paper_portfolio_id,
            master_account_id=existing.master_account_id,
            competition_budget=existing.competition_budget,
            total_assigned_budget=total_assigned,
            assignment_count=len(existing_assignments),
            provenance=existing.provenance,
        )

    if request.competition_budget < Decimal("0"):
        raise InvalidRequestError("Competition budget must be non-negative")

    participant_rows_result = await db.execute(
        select(ArenaParticipatingAgent).where(ArenaParticipatingAgent.competition_id == request.competition_id)
    )
    participants = list(participant_rows_result.scalars().all())
    participants_by_id = {uuid.UUID(item.agent_identity): item for item in participants}

    accepted_registration_rows_result = await db.execute(
        select(ArenaAgentRegistration).where(
            ArenaAgentRegistration.competition_id == request.competition_id,
            ArenaAgentRegistration.eligibility_status == "accepted",
        )
    )
    accepted_registrations = list(accepted_registration_rows_result.scalars().all())
    accepted_registration_ids = {item.agent_id for item in accepted_registrations}

    assigned_total = Decimal("0")
    seen_agent_ids: set[uuid.UUID] = set()
    for assignment in request.assignments:
        if assignment.assigned_budget < Decimal("0"):
            raise InvalidRequestError("Assigned budget must be non-negative")
        if assignment.agent_id in seen_agent_ids:
            raise InvalidRequestError("Duplicate agent assignment is not allowed")
        seen_agent_ids.add(assignment.agent_id)

        participant = participants_by_id.get(assignment.agent_id)
        if participant is None:
            raise InvalidRequestError("All budget assignments must target participating agents in the competition")
        if assignment.agent_id not in accepted_registration_ids:
            raise InvalidRequestError("All budget assignments must target accepted registered agents")
        if participant.competition_id != request.competition_id:
            raise InvalidRequestError("Budget assignments must be scoped to the requested competition")

        assigned_total += assignment.assigned_budget

    if assigned_total > request.competition_budget:
        raise InvalidRequestError("Assigned budgets cannot exceed competition budget")

    allocation = ArenaCompetitionBudgetAllocation(
        idempotency_key=request.idempotency_key,
        competition_id=request.competition_id,
        paper_portfolio_id=competition.paper_portfolio_id,
        master_account_id=competition.master_account_id,
        competition_budget=request.competition_budget,
        paper_only=True,
        live_capital_allocation=False,
        provenance=request.provenance,
    )

    async with db.begin():
        db.add(allocation)
        await db.flush()

        for assignment in request.assignments:
            db.add(
                ArenaAgentBudgetAssignment(
                    idempotency_key=_build_assignment_idempotency_key(
                        competition_budget_allocation_id=allocation.id,
                        competition_id=request.competition_id,
                        agent_id=assignment.agent_id,
                        assigned_budget=assignment.assigned_budget,
                    ),
                    competition_budget_allocation_id=allocation.id,
                    competition_id=request.competition_id,
                    agent_id=assignment.agent_id,
                    assigned_budget=assignment.assigned_budget,
                    paper_only=True,
                    live_capital_allocation=False,
                    provenance=request.provenance,
                )
            )

        db.add(
            AuditLog(
                actor=request.requested_by,
                action="arena.paper_budget_allocated",
                entity_type="arena_competition_budget_allocation",
                entity_id=allocation.id,
                before_state=None,
                after_state={
                    "competition_id": str(request.competition_id),
                    "paper_portfolio_id": str(competition.paper_portfolio_id),
                    "master_account_id": str(competition.master_account_id),
                    "competition_budget": format(request.competition_budget, "f"),
                    "total_assigned_budget": format(assigned_total, "f"),
                    "assignment_count": len(request.assignments),
                    "paper_only": True,
                    "live_capital_allocation": False,
                },
            )
        )

    return ArenaCompetitionAllocationResult(
        competition_budget_allocation_id=allocation.id,
        competition_id=request.competition_id,
        paper_portfolio_id=competition.paper_portfolio_id,
        master_account_id=competition.master_account_id,
        competition_budget=request.competition_budget,
        total_assigned_budget=assigned_total,
        assignment_count=len(request.assignments),
        provenance=request.provenance,
    )