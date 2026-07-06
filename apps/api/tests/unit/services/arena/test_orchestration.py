from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_cycle import ArenaCycle
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.arena_tournament import ArenaTournament
from app.services.arena.contracts import ArenaAgentProposalContract, ArenaCycleSnapshotContract
from app.services.arena.orchestration import (
    build_deterministic_snapshot_hash,
    orchestrate_arena_cycle,
)


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
        self.tournaments: list[ArenaTournament] = []
        self.participants: list[ArenaParticipatingAgent] = []
        self.registrations: list[ArenaAgentRegistration] = []
        self.cycles: list[ArenaCycle] = []
        self.proposals: list[ArenaCycleProposal] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_tournaments" in sql:
            tournament_id = params.get("id_1")
            competition_id = params.get("competition_id_1")
            for item in self.tournaments:
                if item.id == tournament_id and item.competition_id == competition_id:
                    return item
            return None

        if "FROM arena_cycles" in sql:
            key = params.get("idempotency_key_1")
            for item in self.cycles:
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
            rows.sort(key=lambda item: item.agent_identity)
            return _ExecuteResult(rows)

        if "FROM arena_agent_registrations" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.registrations if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM arena_cycle_proposals" in sql:
            cycle_id = params.get("cycle_id_1")
            rows = [item for item in self.proposals if item.cycle_id == cycle_id]
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaCycle):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.cycles.append(obj)
            return

        if isinstance(obj, ArenaCycleProposal):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.proposals.append(obj)

    async def flush(self) -> None:
        return None


def _snapshot() -> ArenaCycleSnapshotContract:
    return ArenaCycleSnapshotContract(
        market_data={"symbol": "BTCUSDT", "close": "50000"},
        portfolio_state={"cash": "10000", "positions": []},
        risk_constraints={"max_position_pct": "0.1"},
        cycle_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _proposal(agent_id: uuid.UUID, action: str) -> ArenaAgentProposalContract:
    return ArenaAgentProposalContract(agent_id=agent_id, action=action, payload={"intent": action})


def _registration(competition_id: uuid.UUID, agent_id: uuid.UUID, status: str) -> ArenaAgentRegistration:
    return ArenaAgentRegistration(
        id=uuid.uuid4(),
        idempotency_key=f"reg-{agent_id}",
        competition_id=competition_id,
        agent_id=agent_id,
        version_id=uuid.uuid4(),
        semantic_version="1.0.0",
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        provenance_metadata={"ticket": "ARENA-83"},
        registration_source="human_api",
        registration_hash=f"hash-{agent_id}",
        strategy_id="ma_crossover",
        strategy_version="v1",
        paper_only_eligible=True,
        live_capital_eligible=False,
        human_governed=True,
        autonomous_self_modifying=False,
        eligibility_status=status,
        rejection_reason=None if status == "accepted" else "registered_strategy_version_required",
    )


def test_snapshot_hash_deterministic_and_order_invariant() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    s = _snapshot()

    hash_1 = build_deterministic_snapshot_hash(
        market_data=s.market_data,
        portfolio_state=s.portfolio_state,
        risk_constraints=s.risk_constraints,
        cycle_timestamp=s.cycle_timestamp,
        participating_agent_ids=[a, b],
    )
    hash_2 = build_deterministic_snapshot_hash(
        market_data=s.market_data,
        portfolio_state=s.portfolio_state,
        risk_constraints=s.risk_constraints,
        cycle_timestamp=s.cycle_timestamp,
        participating_agent_ids=[b, a],
    )

    assert hash_1 == hash_2


@pytest.mark.asyncio
async def test_orchestration_idempotent_and_consistent_agent_ordering() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="tkey",
            tournament_identity="t1",
            competition_id=competition_id,
            sequence_number=1,
            status="active",
            config={},
            provenance={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    session.participants.extend(
        [
            ArenaParticipatingAgent(
                id=uuid.uuid4(),
                idempotency_key="p-b",
                agent_identity=str(agent_b),
                competition_id=competition_id,
                strategy_id="s",
                strategy_version="v1",
                agent_role="participant",
                config={},
                provenance={},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
            ArenaParticipatingAgent(
                id=uuid.uuid4(),
                idempotency_key="p-a",
                agent_identity=str(agent_a),
                competition_id=competition_id,
                strategy_id="s",
                strategy_version="v1",
                agent_role="participant",
                config={},
                provenance={},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
        ]
    )
    session.registrations.extend(
        [
            _registration(competition_id, agent_a, "accepted"),
            _registration(competition_id, agent_b, "accepted"),
        ]
    )

    proposals = [_proposal(agent_b, "sell"), _proposal(agent_a, "buy")]

    result_1 = await orchestrate_arena_cycle(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_number=1,
        snapshot=_snapshot(),
        proposals=proposals,
    )
    result_2 = await orchestrate_arena_cycle(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_number=1,
        snapshot=_snapshot(),
        proposals=proposals,
    )

    assert result_1.cycle_id == result_2.cycle_id
    assert result_1.participating_agent_ids == sorted([agent_a, agent_b], key=str)
    assert result_1.deterministic_snapshot_hash == result_2.deterministic_snapshot_hash
    assert len(session.cycles) == 1
    assert len(session.proposals) == 2


@pytest.mark.asyncio
async def test_orchestration_fails_closed_for_missing_or_rejected_registration() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="tkey",
            tournament_identity="t1",
            competition_id=competition_id,
            sequence_number=1,
            status="active",
            config={},
            provenance={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )
    agent_id = uuid.uuid4()
    session.participants.append(
        ArenaParticipatingAgent(
            id=uuid.uuid4(),
            idempotency_key="p",
            agent_identity=str(agent_id),
            competition_id=competition_id,
            strategy_id="s",
            strategy_version="v1",
            agent_role="participant",
            config={},
            provenance={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    with pytest.raises(InvalidRequestError, match="missing registration"):
        await orchestrate_arena_cycle(
            db=session,
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_number=1,
            snapshot=_snapshot(),
            proposals=[_proposal(agent_id, "wait")],
        )

    session.registrations.append(_registration(competition_id, agent_id, "rejected"))
    with pytest.raises(InvalidRequestError, match="registration rejected"):
        await orchestrate_arena_cycle(
            db=session,
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_number=1,
            snapshot=_snapshot(),
            proposals=[_proposal(agent_id, "wait")],
        )