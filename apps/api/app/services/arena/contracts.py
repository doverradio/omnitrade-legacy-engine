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


@dataclass(frozen=True)
class ArenaAgentVersionIdentityContract:
    agent_id: uuid.UUID
    version_id: uuid.UUID
    semantic_version: str
    created_at: datetime
    provenance_metadata: dict[str, Any]
    registration_source: str
    registration_hash: str


@dataclass(frozen=True)
class ArenaAgentRegistrationRequest:
    competition_id: uuid.UUID
    strategy_id: str
    strategy_version: str
    semantic_version: str
    registration_source: str
    requested_by: str
    provenance_metadata: dict[str, Any]
    paper_only_eligible: bool
    live_capital_eligible: bool
    human_governed: bool
    autonomous_self_modifying: bool
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ArenaEligibilityResult:
    eligible: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class ArenaAgentRegistrationResult:
    accepted: bool
    identity: ArenaAgentVersionIdentityContract
    rejection_reason: str | None
    registration_record_id: uuid.UUID
    participating_agent_id: uuid.UUID | None


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


class ArenaRegistrationServiceContract(Protocol):
    async def register_agent(
        self,
        request: ArenaAgentRegistrationRequest,
    ) -> ArenaAgentRegistrationResult: ...