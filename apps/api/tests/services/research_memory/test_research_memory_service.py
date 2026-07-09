from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_memory.service import ResearchMemory


def _build_run() -> ResearchLaboratoryRun:
    return ResearchLaboratoryRun(
        laboratory_run_id=uuid.UUID("90000000-0000-0000-0000-000000000001"),
        started_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent", "RSI Variant Agent"),
        generated_candidates=2,
        evaluated_candidates=2,
        status="COMPLETED",
    )


def _build_candidates() -> list[StrategyCandidate]:
    return [
        StrategyCandidate(
            candidate_id=uuid.UUID("90000000-0000-0000-0000-000000000011"),
            generated_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
            originating_agent="Baseline Research Agent",
            strategy_name="MA Crossover 9/30",
            description="deterministic",
            parameter_set={"fast_period": 9, "slow_period": 30},
            rationale="deterministic",
            status="PROPOSED",
        ),
        StrategyCandidate(
            candidate_id=uuid.UUID("90000000-0000-0000-0000-000000000012"),
            generated_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
            originating_agent="RSI Variant Agent",
            strategy_name="MA-RSI Blend rsi10",
            description="deterministic",
            parameter_set={"rsi_period": 10},
            rationale="deterministic",
            status="PROPOSED",
        ),
    ]


def _build_evaluations() -> list[CandidateEvaluation]:
    return [
        CandidateEvaluation(
            evaluation_id=uuid.UUID("90000000-0000-0000-0000-000000000021"),
            candidate_id=uuid.UUID("90000000-0000-0000-0000-000000000011"),
            replay_status="COMPLETED",
            decision_quality_score=100,
            ai_coach_summary="Replay successfully reproduced the production decision.",
            decision_intelligence_summary="Top deterministic recommendation.",
            tournament_rank=1,
            promotion_eligible=False,
        ),
        CandidateEvaluation(
            evaluation_id=uuid.UUID("90000000-0000-0000-0000-000000000022"),
            candidate_id=uuid.UUID("90000000-0000-0000-0000-000000000012"),
            replay_status="COMPLETED",
            decision_quality_score=50,
            ai_coach_summary="Replay action differed from production.",
            decision_intelligence_summary="Secondary deterministic recommendation.",
            tournament_rank=2,
            promotion_eligible=False,
        ),
    ]


def test_research_memory_records_laboratory_runs() -> None:
    memory = ResearchMemory()
    memory.record_laboratory_run(
        run=_build_run(),
        candidates=_build_candidates(),
        evaluations=_build_evaluations(),
    )

    runs = memory.list_runs()
    summary = memory.get_summary()
    participations = memory.list_agent_participation()

    assert len(runs) == 1
    assert runs[0].laboratory_run_id == uuid.UUID("90000000-0000-0000-0000-000000000001")
    assert runs[0].participating_agents == ("Baseline Research Agent", "RSI Variant Agent")
    assert summary.total_laboratory_runs == 1
    assert len(participations) == 2


def test_research_memory_records_candidates() -> None:
    memory = ResearchMemory()
    memory.record_laboratory_run(
        run=_build_run(),
        candidates=_build_candidates(),
        evaluations=_build_evaluations(),
    )

    candidates = memory.list_candidates()
    outcomes = memory.list_tournament_outcomes()
    summary = memory.get_summary()

    assert len(candidates) == 2
    assert candidates[0].status == "EVALUATED"
    assert candidates[0].quality_score in {50, 100}
    assert len(outcomes) == 2
    assert summary.total_candidates == 2
    assert summary.highest_quality_candidate is not None
    assert summary.highest_quality_candidate.quality_score == 100
    assert summary.average_quality_score == 75.0


def test_research_memory_is_empty_by_default() -> None:
    memory = ResearchMemory()
    summary = memory.get_summary()

    assert summary.total_laboratory_runs == 0
    assert summary.total_candidates == 0
    assert summary.highest_quality_candidate is None
    assert summary.average_quality_score is None
    assert summary.latest_laboratory_run is None
    assert memory.list_runs() == tuple()
    assert memory.list_candidates() == tuple()
