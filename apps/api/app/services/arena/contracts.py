from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class ArenaProvenanceContract:
    source_lineage: dict[str, Any]
    field_provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaCompetitionIdentityContract:
    competition_identity: str
    idempotency_key: str
    master_account_id: uuid.UUID
    paper_portfolio_id: uuid.UUID


@dataclass(frozen=True)
class ArenaTournamentIdentityContract:
    tournament_identity: str
    idempotency_key: str
    competition_identity: str
    sequence_number: int


@dataclass(frozen=True)
class ArenaCycleIdentityContract:
    cycle_identity: str
    idempotency_key: str
    tournament_identity: str
    cycle_number: int


@dataclass(frozen=True)
class ArenaAgentIdentityContract:
    agent_identity: str
    idempotency_key: str
    competition_identity: str
    strategy_id: str
    strategy_version: str


@dataclass(frozen=True)
class ArenaLifecycleWriteRequest:
    status: str
    config: dict[str, Any]
    provenance: ArenaProvenanceContract
    requested_at: datetime


class ArenaLifecycleServiceContract(Protocol):
    async def ensure_competition(
        self,
        identity: ArenaCompetitionIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_tournament(
        self,
        identity: ArenaTournamentIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_cycle(
        self,
        identity: ArenaCycleIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_participating_agent(
        self,
        identity: ArenaAgentIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...