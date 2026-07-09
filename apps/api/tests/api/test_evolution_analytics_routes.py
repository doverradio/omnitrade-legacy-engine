from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.registry import get_evolution_engine
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_memory.registry import get_research_memory


def _seed_memory() -> None:
    memory = get_research_memory()
    memory.clear()
    get_evolution_engine().clear()

    memory.record_laboratory_run(
        run=ResearchLaboratoryRun(
            laboratory_run_id=uuid.UUID("95000000-0000-0000-0000-000000000001"),
            started_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
            participating_agents=("Baseline Research Agent",),
            generated_candidates=1,
            evaluated_candidates=1,
            status="COMPLETED",
        ),
        candidates=[
            StrategyCandidate(
                candidate_id=uuid.UUID("95000000-0000-0000-0000-000000000011"),
                generated_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                originating_agent="Baseline Research Agent",
                strategy_name="MA Crossover 20/100",
                description="deterministic",
                parameter_set={"rsi_period": 14, "fast_period": 20, "slow_period": 100},
                rationale="deterministic",
                status="PROPOSED",
            )
        ],
        evaluations=[
            CandidateEvaluation(
                evaluation_id=uuid.UUID("95000000-0000-0000-0000-000000000021"),
                candidate_id=uuid.UUID("95000000-0000-0000-0000-000000000011"),
                replay_status="COMPLETED",
                decision_quality_score=100,
                ai_coach_summary="deterministic",
                decision_intelligence_summary="deterministic",
                tournament_rank=1,
                promotion_eligible=False,
            )
        ],
    )


def test_evolution_analytics_route_returns_summary() -> None:
    app = create_app()
    _seed_memory()

    with TestClient(app) as client:
        response = client.get("/research/evolution-analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_laboratory_runs"] == 1
    assert payload["total_candidates_generated"] == 1
    assert payload["best_quality_score"] == 100
    assert payload["best_candidate"]["candidate_id"] == "95000000-0000-0000-0000-000000000011"


def test_evolution_analytics_route_returns_empty_state() -> None:
    app = create_app()
    get_research_memory().clear()
    get_evolution_engine().clear()

    with TestClient(app) as client:
        response = client.get("/research/evolution-analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_laboratory_runs"] == 0
    assert payload["total_candidates_generated"] == 0
    assert payload["best_candidate"] is None
    assert payload["largest_lineage_tree"]["root_candidate_id"] is None
