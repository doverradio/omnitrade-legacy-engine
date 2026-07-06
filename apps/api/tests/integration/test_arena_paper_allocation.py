from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_competition import ArenaCompetition
from app.models.arena_participating_agent import ArenaParticipatingAgent
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
        self.competition_allocations: list[Any] = []
        self.agent_allocations: list[Any] = []
        self.audit_logs: list[Any] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_competitions" in sql:
            target_id = params.get("id_1")
            for item in self.competitions:
                if item.id == target_id:
                    return item
            return None

        if "FROM paper_accounts" in sql:
            target_id = params.get("id_1")
            for item in self.paper_accounts:
                if item.id == target_id:
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
            rows = [item for item in self.participants if item.competition_id == params.get("competition_id_1")]
            return _ExecuteResult(rows)

        if "FROM arena_agent_registrations" in sql:
            rows = [
                item
                for item in self.registrations
                if item.competition_id == params.get("competition_id_1") and item.eligibility_status == "accepted"
            ]
            return _ExecuteResult(rows)

        if "FROM arena_agent_budget_assignments" in sql:
            rows = [
                item
                for item in self.agent_allocations
                if item.competition_budget_allocation_id == params.get("competition_budget_allocation_id_1")
            ]
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        name = obj.__class__.__name__
        if name == "ArenaCompetitionBudgetAllocation":
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.competition_allocations.append(obj)
            return
        if name == "ArenaAgentBudgetAssignment":
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.agent_allocations.append(obj)
            return
        if name == "AuditLog":
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_arena_paper_budget_allocation_is_record_only_and_paper_only() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    paper_portfolio_id = uuid.uuid4()
    master_account_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)

    session.competitions.append(
        ArenaCompetition(
            id=competition_id,
            idempotency_key="comp",
            competition_identity="comp-1",
            master_account_id=master_account_id,
            paper_portfolio_id=paper_portfolio_id,
            name="Comp",
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
            name="paper",
            asset_class="crypto",
            starting_balance=Decimal("2000"),
            current_cash_balance=Decimal("1800"),
            is_active=True,
            created_at=now,
        )
    )
    session.participants.append(
        ArenaParticipatingAgent(
            id=uuid.uuid4(),
            idempotency_key="p",
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
            idempotency_key="r",
            competition_id=competition_id,
            agent_id=agent_id,
            version_id=uuid.uuid4(),
            semantic_version="1.0.0",
            created_at=now,
            provenance_metadata={},
            registration_source="human_api",
            registration_hash="hash",
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

    starting_cash = session.paper_accounts[0].current_cash_balance
    result = await allocate_competition_paper_budget(
        db=session,
        request=ArenaCompetitionAllocationRequest(
            competition_id=competition_id,
            idempotency_key="alloc-itg",
            competition_budget=Decimal("500"),
            assignments=[
                ArenaAgentBudgetAssignmentContract(agent_id=agent_id, assigned_budget=Decimal("500")),
            ],
            provenance={"source": "integration"},
            requested_by="human.reviewer",
        ),
    )

    assert result.competition_id == competition_id
    assert result.paper_portfolio_id == paper_portfolio_id
    assert result.master_account_id == master_account_id
    assert result.assignment_count == 1
    assert result.total_assigned_budget == Decimal("500")

    assert len(session.competition_allocations) == 1
    assert session.competition_allocations[0].paper_only is True
    assert session.competition_allocations[0].live_capital_allocation is False
    assert len(session.agent_allocations) == 1
    assert session.agent_allocations[0].paper_only is True
    assert session.agent_allocations[0].live_capital_allocation is False

    # Integration guarantee: paper allocation records do not mutate portfolio cash balances.
    assert session.paper_accounts[0].current_cash_balance == starting_cash