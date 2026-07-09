from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.services.evolution_analytics.service import EvolutionAnalyticsService
from app.services.research_memory.interface import ResearchMemoryCandidateRecord, ResearchMemoryLaboratoryRunRecord


def _run(run_id: str, generated: int) -> ResearchMemoryLaboratoryRunRecord:
    return ResearchMemoryLaboratoryRunRecord(
        laboratory_run_id=uuid.UUID(run_id),
        started_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent",),
        candidates_generated=generated,
        candidates_evaluated=generated,
    )


def _candidate(
    *,
    candidate_id: str,
    run_id: str,
    agent: str,
    quality: int | None,
    rank: int | None,
    generation: int,
    parent_candidate_id: str | None,
) -> ResearchMemoryCandidateRecord:
    return ResearchMemoryCandidateRecord(
        laboratory_run_id=uuid.UUID(run_id),
        candidate_id=uuid.UUID(candidate_id),
        originating_agent=agent,
        parameter_set={"rsi_period": 14},
        evaluation_summary="deterministic",
        quality_score=quality,
        tournament_rank=rank,
        status="EVALUATED",
        generation=generation,
        parent_candidate_id=None if parent_candidate_id is None else uuid.UUID(parent_candidate_id),
        mutation_reason=None if parent_candidate_id is None else "rsi_period 14->12",
        parameter_diff=tuple(),
    )


def test_analytics_summary() -> None:
    runs = (
        _run("94000000-0000-0000-0000-000000000001", 2),
        _run("94000000-0000-0000-0000-000000000002", 3),
    )
    candidates = (
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000011",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=100,
            rank=1,
            generation=1,
            parent_candidate_id=None,
        ),
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000012",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=50,
            rank=2,
            generation=2,
            parent_candidate_id="94000000-0000-0000-0000-000000000011",
        ),
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000013",
            run_id="94000000-0000-0000-0000-000000000002",
            agent="RSI Variant Agent",
            quality=0,
            rank=3,
            generation=3,
            parent_candidate_id="94000000-0000-0000-0000-000000000012",
        ),
    )

    service = EvolutionAnalyticsService(
        runs_provider=lambda: runs,
        candidates_provider=lambda: candidates,
    )

    summary = service.build_summary()

    assert summary.total_laboratory_runs == 2
    assert summary.total_candidates_generated == 5
    assert summary.total_evolved_candidates == 2
    assert summary.average_quality_score == 50.0
    assert summary.best_quality_score == 100
    assert summary.best_candidate is not None
    assert summary.best_candidate.candidate_id == uuid.UUID("94000000-0000-0000-0000-000000000011")
    assert summary.successful_mutations == 0
    assert summary.unsuccessful_mutations == 2
    assert summary.lineage_depth == 3
    assert summary.top_research_agent == "Baseline Research Agent"
    assert summary.mutation_success_rate == 0.0


def test_analytics_empty_history() -> None:
    service = EvolutionAnalyticsService(
        runs_provider=lambda: tuple(),
        candidates_provider=lambda: tuple(),
    )

    summary = service.build_summary()

    assert summary.total_laboratory_runs == 0
    assert summary.total_candidates_generated == 0
    assert summary.total_evolved_candidates == 0
    assert summary.average_quality_score is None
    assert summary.best_quality_score is None
    assert summary.best_candidate is None
    assert summary.successful_mutations == 0
    assert summary.unsuccessful_mutations == 0
    assert summary.generation_distribution == tuple()
    assert summary.lineage_depth == 0
    assert summary.top_research_agent is None
    assert summary.largest_lineage_tree.root_candidate_id is None


def test_analytics_multiple_generations() -> None:
    runs = (_run("94000000-0000-0000-0000-000000000001", 4),)
    candidates = (
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000011",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=100,
            rank=1,
            generation=1,
            parent_candidate_id=None,
        ),
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000012",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=100,
            rank=1,
            generation=2,
            parent_candidate_id="94000000-0000-0000-0000-000000000011",
        ),
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000013",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=100,
            rank=1,
            generation=3,
            parent_candidate_id="94000000-0000-0000-0000-000000000012",
        ),
        _candidate(
            candidate_id="94000000-0000-0000-0000-000000000014",
            run_id="94000000-0000-0000-0000-000000000001",
            agent="Baseline Research Agent",
            quality=100,
            rank=1,
            generation=4,
            parent_candidate_id="94000000-0000-0000-0000-000000000013",
        ),
    )

    service = EvolutionAnalyticsService(
        runs_provider=lambda: runs,
        candidates_provider=lambda: candidates,
    )

    summary = service.build_summary()

    assert summary.lineage_depth == 4
    assert len(summary.generation_distribution) == 4
    assert summary.generation_distribution[-1].generation == 4
    assert summary.largest_lineage_tree.root_candidate_id == uuid.UUID("94000000-0000-0000-0000-000000000011")
    assert summary.largest_lineage_tree.lineage_depth == 4
    assert summary.largest_lineage_tree.descendant_count == 3
