from __future__ import annotations

import uuid

import pytest

from app.services.evolution.service import EvolutionEngine, ParentCandidateNotFoundError
from app.services.research_memory.interface import ResearchMemoryCandidateRecord


def _memory_candidate(*, candidate_id: str, quality_score: int, tournament_rank: int, parameter_set: dict[str, int]) -> ResearchMemoryCandidateRecord:
    return ResearchMemoryCandidateRecord(
        laboratory_run_id=uuid.UUID("92000000-0000-0000-0000-000000000001"),
        candidate_id=uuid.UUID(candidate_id),
        originating_agent="Baseline Research Agent",
        parameter_set=parameter_set,
        evaluation_summary="deterministic summary",
        quality_score=quality_score,
        tournament_rank=tournament_rank,
        status="EVALUATED",
    )


def test_evolution_engine_mutations_are_deterministic() -> None:
    engine = EvolutionEngine()
    memory_candidates = (
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000011",
            quality_score=100,
            tournament_rank=1,
            parameter_set={"rsi_period": 14, "fast_period": 20, "slow_period": 100},
        ),
    )

    first = engine.evolve(memory_candidates=memory_candidates, parent_candidate_id=None, generation_limit=None)
    engine.clear()
    second = engine.evolve(memory_candidates=memory_candidates, parent_candidate_id=None, generation_limit=None)

    assert first.generated_count == 6
    assert second.generated_count == 6
    assert [item.candidate_id for item in first.descendants] == [item.candidate_id for item in second.descendants]
    assert [item.mutation_reason for item in first.descendants] == [item.mutation_reason for item in second.descendants]


def test_evolution_engine_tracks_lineage() -> None:
    engine = EvolutionEngine()
    memory_candidates = (
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000011",
            quality_score=100,
            tournament_rank=1,
            parameter_set={"rsi_period": 14, "fast_period": 20, "slow_period": 100},
        ),
    )

    result = engine.evolve(memory_candidates=memory_candidates, parent_candidate_id=None, generation_limit=2)
    descendants = result.descendants

    assert len(descendants) == 2
    assert descendants[0].parent_candidate_id == uuid.UUID("92000000-0000-0000-0000-000000000011")
    assert descendants[0].generation == 2
    assert descendants[0].parameter_diff
    assert len(engine.list_descendants()) == 2


def test_evolution_engine_without_parent_selects_top_candidates() -> None:
    engine = EvolutionEngine()
    memory_candidates = (
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000011",
            quality_score=100,
            tournament_rank=1,
            parameter_set={"rsi_period": 14},
        ),
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000012",
            quality_score=50,
            tournament_rank=2,
            parameter_set={"fast_period": 20, "slow_period": 100},
        ),
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000013",
            quality_score=0,
            tournament_rank=3,
            parameter_set={"rsi_period": 10},
        ),
    )

    result = engine.evolve(memory_candidates=memory_candidates, parent_candidate_id=None, generation_limit=4)

    assert result.generated_count == 4
    parent_ids = {item.parent_candidate_id for item in result.descendants}
    assert uuid.UUID("92000000-0000-0000-0000-000000000011") in parent_ids
    assert uuid.UUID("92000000-0000-0000-0000-000000000012") in parent_ids


def test_evolution_engine_raises_for_invalid_parent() -> None:
    engine = EvolutionEngine()
    memory_candidates = (
        _memory_candidate(
            candidate_id="92000000-0000-0000-0000-000000000011",
            quality_score=100,
            tournament_rank=1,
            parameter_set={"rsi_period": 14},
        ),
    )

    with pytest.raises(ParentCandidateNotFoundError):
        engine.evolve(
            memory_candidates=memory_candidates,
            parent_candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
            generation_limit=None,
        )
