from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import uuid


@dataclass(frozen=True, slots=True)
class EvolutionMutation:
    parameter_name: str
    previous_value: int
    new_value: int


@dataclass(frozen=True, slots=True)
class EvolvedCandidate:
    candidate_id: uuid.UUID
    parent_candidate_id: uuid.UUID
    generation: int
    mutation_reason: str
    parameter_diff: tuple[EvolutionMutation, ...]
    parameter_set: dict[str, Any]
    strategy_name: str
    originating_agent: str
    generated_at: datetime
    quality_score: int | None
    tournament_rank: int | None
    status: str


@dataclass(frozen=True, slots=True)
class EvolutionRunResult:
    generated_count: int
    descendants: tuple[EvolvedCandidate, ...]
