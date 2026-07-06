from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.arena_competition import (
    ArenaCompetition,
    _prevent_arena_competition_delete,
    _prevent_arena_competition_update,
)
from app.models.arena_cycle import ArenaCycle, _prevent_arena_cycle_delete, _prevent_arena_cycle_update
from app.models.arena_participating_agent import (
    ArenaParticipatingAgent,
    _prevent_arena_participating_agent_delete,
    _prevent_arena_participating_agent_update,
)
from app.models.arena_tournament import (
    ArenaTournament,
    _prevent_arena_tournament_delete,
    _prevent_arena_tournament_update,
)


def test_arena_competition_append_only_guards() -> None:
    competition = ArenaCompetition(
        id=uuid.uuid4(),
        idempotency_key="comp-key",
        competition_identity="competition-1",
        master_account_id=uuid.uuid4(),
        paper_portfolio_id=uuid.uuid4(),
        name="Arena Competition",
        status="planned",
        config={"mode": "paper"},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_competition_update(None, None, competition)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_competition_delete(None, None, competition)


def test_arena_tournament_append_only_guards() -> None:
    tournament = ArenaTournament(
        id=uuid.uuid4(),
        idempotency_key="tournament-key",
        tournament_identity="tournament-1",
        competition_id=uuid.uuid4(),
        sequence_number=1,
        status="planned",
        config={"rounds": 3},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_tournament_update(None, None, tournament)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_tournament_delete(None, None, tournament)


def test_arena_cycle_append_only_guards() -> None:
    cycle = ArenaCycle(
        id=uuid.uuid4(),
        idempotency_key="cycle-key",
        cycle_identity="cycle-1",
        tournament_id=uuid.uuid4(),
        cycle_number=1,
        status="planned",
        config={"window": "15m"},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_cycle_update(None, None, cycle)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_cycle_delete(None, None, cycle)


def test_participating_agent_append_only_guards() -> None:
    agent = ArenaParticipatingAgent(
        id=uuid.uuid4(),
        idempotency_key="agent-key",
        agent_identity="agent-1",
        competition_id=uuid.uuid4(),
        strategy_id="mean_reversion",
        strategy_version="v1",
        agent_role="participant",
        config={"risk_profile": "standard"},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_participating_agent_update(None, None, agent)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_participating_agent_delete(None, None, agent)