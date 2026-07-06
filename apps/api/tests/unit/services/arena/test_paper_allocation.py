from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.arena_agent_budget_assignment import ArenaAgentBudgetAssignment
from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_competition import ArenaCompetition
from app.models.arena_competition_budget_allocation import ArenaCompetitionBudgetAllocation
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.services.arena.contracts import ArenaAgentBudgetAssignmentContract, ArenaCompetitionAllocationRequest
from app.services.arena.paper_allocation import allocate_competition_paper_budget


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.competitions: list[ArenaCompetition] = []
        self.paper_accounts: list[PaperAccount] = []
        self.participants: list[ArenaParticipatingAgent] = []
        self.registrations: list[ArenaAgentRegistration] = []
        self.competition_allocations: list[ArenaCompetitionBudgetAllocation] = []
        self.agent_allocations: list[ArenaAgentBudgetAssignment] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_competitions" in sql:
            competition_id = params.get("id_1")
            for item in self.competitions:
                if item.id == competition_id:
                    return item
            return None

        if "FROM paper_accounts" in sql:
            portfolio_id = params.get("id_1")
            for item in self.paper_accounts:
                if item.id == portfolio_id:
                    return item
            return None

        if "FROM arena_competition_budget_allocations" in sql:
            key = params.get("idempotency_key_1")
            for item in self.competition_allocations:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_participating_agents" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.participants if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM arena_agent_registrations" in sql:
            competition_id = params.get("competition_id_1")
            rows = [
                item
                for item in self.registrations
                if item.competition_id == competition_id and item.eligibility_status == "accepted"
            ]
            return _ExecuteResult(rows)

        if "FROM arena_agent_budget_assignments" in sql:
            allocation_id = params.get("competition_budget_allocation_id_1")
            rows = [
                item for item in self.agent_allocations if item.competition_budget_allocation_id == allocation_id
            ]
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaCompetitionBudgetAllocation):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.competition_allocations.append(obj)
            return
        if isinstance(obj, ArenaAgentBudgetAssignment):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.agent_allocations.append(obj)
            return
        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


def _request(*, competition_id: uuid.UUID, idempotency_key: str, assignments: list[tuple[uuid.UUID, str]]) -> ArenaCompetitionAllocationRequest:
    return ArenaCompetitionAllocationRequest(
        competition_id=competition_id,
        idempotency_key=idempotency_key,
        competition_budget=Decimal("1000"),
        assignments=[
            ArenaAgentBudgetAssignmentContract(agent_id=agent_id, assigned_budget=Decimal(amount))
            for agent_id, amount in assignments
        ],
        provenance={"source": "unit-test", "ticket": "ARENA-84"},
        requested_by="human.reviewer",
    )


def _build_session() -> tuple[_FakeSession, uuid.UUID, uuid.UUID, uuid.UUID]:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    paper_portfolio_id = uuid.uuid4()
    master_account_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    session.competitions.append(
        ArenaCompetition(
            id=competition_id,
            idempotency_key="c-key",
            competition_identity="comp-1",
            master_account_id=master_account_id,
            paper_portfolio_id=paper_portfolio_id,
            name="Arena One",
            status="active",
            config={},
            provenance={},
            created_at=now,
        )
    )
    session.paper_accounts.append(
        PaperAccount(
            id=paper_portfolio_id,
            owner_user_id=uuid.uuid4(),
            name="paper-main",
            asset_class="crypto",
            starting_balance=Decimal("1000"),
            current_cash_balance=Decimal("1000"),
            is_active=True,
            created_at=now,
        )
    )

    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    for agent_id in (agent_a, agent_b):
        session.participants.append(
            ArenaParticipatingAgent(
                id=uuid.uuid4(),
                idempotency_key=f"p-{agent_id}",
                agent_identity=str(agent_id),
                competition_id=competition_id,
                strategy_id="ma_crossover",
                strategy_version="v1",
                agent_role="participant",
                config={},
                provenance={},
                created_at=now,
            )
        )
        session.registrations.append(
            ArenaAgentRegistration(
                id=uuid.uuid4(),
                idempotency_key=f"r-{agent_id}",
                competition_id=competition_id,
                agent_id=agent_id,
                version_id=uuid.uuid4(),
                semantic_version="1.0.0",
                created_at=now,
                provenance_metadata={},
                registration_source="human_api",
                registration_hash=f"h-{agent_id}",
                strategy_id="ma_crossover",
                strategy_version="v1",
                paper_only_eligible=True,
                live_capital_eligible=False,
                human_governed=True,
                autonomous_self_modifying=False,
                eligibility_status="accepted",
                rejection_reason=None,
            )
        )

    return session, competition_id, agent_a, agent_b


@pytest.mark.asyncio
async def test_paper_allocation_is_competition_scoped_and_preserves_provenance() -> None:
    session, competition_id, agent_a, agent_b = _build_session()
    starting_balance = session.paper_accounts[0].current_cash_balance

    result = await allocate_competition_paper_budget(
        db=session,
        request=_request(
            competition_id=competition_id,
            idempotency_key="alloc-1",
            assignments=[(agent_a, "500"), (agent_b, "250")],
        ),
    )

    assert result.competition_id == competition_id
    assert result.paper_portfolio_id == session.competitions[0].paper_portfolio_id
    assert result.assignment_count == 2
    assert result.total_assigned_budget == Decimal("750")
    assert result.provenance["ticket"] == "ARENA-84"
    assert len(session.competition_allocations) == 1
    assert session.competition_allocations[0].paper_only is True
    assert session.competition_allocations[0].live_capital_allocation is False
    assert len(session.agent_allocations) == 2
    assert all(item.competition_id == competition_id for item in session.agent_allocations)
    assert all(item.paper_only is True for item in session.agent_allocations)
    assert all(item.live_capital_allocation is False for item in session.agent_allocations)

    # Allocation is record-only and must not mutate portfolio accounting state.
    assert session.paper_accounts[0].current_cash_balance == starting_balance


@pytest.mark.asyncio
async def test_agent_budgets_cannot_exceed_competition_budget() -> None:
    session, competition_id, agent_a, agent_b = _build_session()

    with pytest.raises(InvalidRequestError, match="cannot exceed competition budget"):
        await allocate_competition_paper_budget(
            db=session,
            request=ArenaCompetitionAllocationRequest(
                competition_id=competition_id,
                idempotency_key="alloc-exceed",
                competition_budget=Decimal("100"),
                assignments=[
                    ArenaAgentBudgetAssignmentContract(agent_id=agent_a, assigned_budget=Decimal("70")),
                    ArenaAgentBudgetAssignmentContract(agent_id=agent_b, assigned_budget=Decimal("40")),
                ],
                provenance={"source": "unit-test"},
                requested_by="human.reviewer",
            ),
        )


@pytest.mark.asyncio
async def test_repeated_allocation_creation_is_idempotent() -> None:
    session, competition_id, agent_a, agent_b = _build_session()
    request = _request(
        competition_id=competition_id,
        idempotency_key="alloc-repeat",
        assignments=[(agent_a, "100"), (agent_b, "200")],
    )

    first = await allocate_competition_paper_budget(db=session, request=request)
    second = await allocate_competition_paper_budget(db=session, request=request)

    assert first.competition_budget_allocation_id == second.competition_budget_allocation_id
    assert len(session.competition_allocations) == 1
    assert len(session.agent_allocations) == 2