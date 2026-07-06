from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_cycle import ArenaCycle
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.arena_tournament import ArenaTournament
from app.services.arena.contracts import ArenaAgentProposalContract, ArenaCycleSnapshotContract
from app.services.arena.orchestration import orchestrate_arena_cycle


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
            for item in self.tournaments:
                if item.id == params.get("id_1") and item.competition_id == params.get("competition_id_1"):
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
            rows = [item for item in self.participants if item.competition_id == params.get("competition_id_1")]
            rows.sort(key=lambda item: item.agent_identity)
            return _ExecuteResult(rows)

        if "FROM arena_agent_registrations" in sql:
            rows = [item for item in self.registrations if item.competition_id == params.get("competition_id_1")]
            return _ExecuteResult(rows)

        if "FROM arena_cycle_proposals" in sql:
            rows = [item for item in self.proposals if item.cycle_id == params.get("cycle_id_1")]
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


def _registration(competition_id: uuid.UUID, agent_id: uuid.UUID) -> ArenaAgentRegistration:
    return ArenaAgentRegistration(
        id=uuid.uuid4(),
        idempotency_key=f"reg-{agent_id}",
        competition_id=competition_id,
        agent_id=agent_id,
        version_id=uuid.uuid4(),
        semantic_version="1.0.0",
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        provenance_metadata={"source": "integration"},
        registration_source="human_api",
        registration_hash=f"hash-{agent_id}",
        strategy_id="ma_crossover",
        strategy_version="v1",
        paper_only_eligible=True,
        live_capital_eligible=False,
        human_governed=True,
        autonomous_self_modifying=False,
        eligibility_status="accepted",
        rejection_reason=None,
    )


@pytest.mark.asyncio
async def test_orchestration_cycle_persists_identical_snapshot_metadata_and_proposals() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="t",
            tournament_identity="tour-1",
            competition_id=competition_id,
            sequence_number=1,
            status="active",
            config={},
            provenance={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )
    agent_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    for idx, agent_id in enumerate(agent_ids):
        session.participants.append(
            ArenaParticipatingAgent(
                id=uuid.uuid4(),
                idempotency_key=f"p-{idx}",
                agent_identity=str(agent_id),
                competition_id=competition_id,
                strategy_id="ma_crossover",
                strategy_version="v1",
                agent_role="participant",
                config={},
                provenance={},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            )
        )
        session.registrations.append(_registration(competition_id, agent_id))

    snapshot = ArenaCycleSnapshotContract(
        market_data={"asset": "BTCUSDT", "close": "65000"},
        portfolio_state={"cash": "25000", "positions": [{"asset": "BTCUSDT", "qty": "0"}]},
        risk_constraints={"max_position_pct": "0.15", "kill_switch": False},
        cycle_timestamp=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
    )
    proposals = [
        ArenaAgentProposalContract(agent_id=agent_ids[0], action="buy", payload={"size": "0.01"}),
        ArenaAgentProposalContract(agent_id=agent_ids[1], action="sell", payload={"size": "0.01"}),
        ArenaAgentProposalContract(agent_id=agent_ids[2], action="wait", payload={"reason": "neutral"}),
    ]

    result = await orchestrate_arena_cycle(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_number=1,
        snapshot=snapshot,
        proposals=proposals,
    )

    assert result.competition_id == competition_id
    assert result.tournament_id == tournament_id
    assert result.proposals_captured == 3
    assert result.participating_agent_ids == sorted(agent_ids, key=str)

    assert len(session.cycles) == 1
    persisted_cycle = session.cycles[0]
    assert persisted_cycle.provenance["deterministic_snapshot_hash"] == result.deterministic_snapshot_hash
    assert persisted_cycle.provenance["snapshot_distribution"]["uniform_for_all_agents"] is True
    assert persisted_cycle.provenance["snapshot_distribution"]["market_data"] == snapshot.market_data
    assert persisted_cycle.provenance["snapshot_distribution"]["portfolio_state"] == snapshot.portfolio_state
    assert persisted_cycle.provenance["snapshot_distribution"]["risk_constraints"] == snapshot.risk_constraints

    assert len(session.proposals) == 3
    assert sorted(item.proposal_action for item in session.proposals) == ["buy", "sell", "wait"]
    for item in session.proposals:
        assert item.provenance["deterministic_snapshot_hash"] == result.deterministic_snapshot_hash