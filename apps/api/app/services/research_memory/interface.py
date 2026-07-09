from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import uuid


@dataclass(frozen=True, slots=True)
class ResearchMemoryLaboratoryRunRecord:
    laboratory_run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    participating_agents: tuple[str, ...]
    candidates_generated: int
    candidates_evaluated: int


@dataclass(frozen=True, slots=True)
class ResearchMemoryCandidateRecord:
    laboratory_run_id: uuid.UUID
    candidate_id: uuid.UUID
    originating_agent: str
    parameter_set: dict[str, Any]
    evaluation_summary: str | None
    quality_score: int | None
    tournament_rank: int | None
    status: str


@dataclass(frozen=True, slots=True)
class ResearchMemoryTournamentOutcomeRecord:
    laboratory_run_id: uuid.UUID
    candidate_id: uuid.UUID
    tournament_rank: int


@dataclass(frozen=True, slots=True)
class ResearchMemoryAgentParticipationRecord:
    laboratory_run_id: uuid.UUID
    agent_name: str


@dataclass(frozen=True, slots=True)
class ResearchMemorySummary:
    total_laboratory_runs: int
    total_candidates: int
    highest_quality_candidate: ResearchMemoryCandidateRecord | None
    average_quality_score: float | None
    latest_laboratory_run: ResearchMemoryLaboratoryRunRecord | None
