from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_memory.registry import get_research_memory


def _seed_memory_with_single_run() -> None:
    memory = get_research_memory()
    memory.clear()

    run = ResearchLaboratoryRun(
        laboratory_run_id=uuid.UUID("91000000-0000-0000-0000-000000000001"),
        started_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )
    candidates = [
        StrategyCandidate(
            candidate_id=uuid.UUID("91000000-0000-0000-0000-000000000011"),
            generated_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
            originating_agent="Baseline Research Agent",
            strategy_name="MA Crossover 20/100",
            description="deterministic",
            parameter_set={"fast_period": 20, "slow_period": 100},
            rationale="deterministic",
            status="PROPOSED",
        )
    ]
    evaluations = [
        CandidateEvaluation(
            evaluation_id=uuid.UUID("91000000-0000-0000-0000-000000000021"),
            candidate_id=uuid.UUID("91000000-0000-0000-0000-000000000011"),
            replay_status="COMPLETED",
            decision_quality_score=100,
            ai_coach_summary="Replay successfully reproduced the production decision.",
            decision_intelligence_summary="Top deterministic recommendation.",
            tournament_rank=1,
            promotion_eligible=False,
        )
    ]
    memory.record_laboratory_run(
        run=run,
        candidates=candidates,
        evaluations=evaluations,
    )


def test_research_memory_endpoints_return_empty_history() -> None:
    app = create_app()
    get_research_memory().clear()

    with TestClient(app) as client:
        summary_response = client.get("/research/memory")
        runs_response = client.get("/research/memory/runs")
        candidates_response = client.get("/research/memory/candidates")

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_laboratory_runs"] == 0
    assert summary["total_candidates"] == 0
    assert summary["highest_quality_candidate"] is None
    assert summary["latest_laboratory_run"] is None

    assert runs_response.status_code == 200
    assert runs_response.json() == []

    assert candidates_response.status_code == 200
    assert candidates_response.json() == []


def test_research_memory_endpoints_return_recorded_history() -> None:
    app = create_app()
    _seed_memory_with_single_run()

    with TestClient(app) as client:
        summary_response = client.get("/research/memory")
        runs_response = client.get("/research/memory/runs")
        candidates_response = client.get("/research/memory/candidates")

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_laboratory_runs"] == 1
    assert summary["total_candidates"] == 1
    assert summary["average_quality_score"] == 100.0
    assert summary["highest_quality_candidate"]["candidate_id"] == "91000000-0000-0000-0000-000000000011"

    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["laboratory_run_id"] == "91000000-0000-0000-0000-000000000001"
    assert runs[0]["participating_agents"] == ["Baseline Research Agent"]

    assert candidates_response.status_code == 200
    candidates = candidates_response.json()
    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == "91000000-0000-0000-0000-000000000011"
    assert candidates[0]["quality_score"] == 100
    assert candidates[0]["tournament_rank"] == 1

    get_research_memory().clear()
