from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_agents.registry import ResearchAgentRegistration
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_laboratory.service import ResearchLaboratory


def _candidate(name: str, seed: str) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=uuid.uuid5(uuid.UUID("00000000-0000-0000-0000-000000000010"), seed),
        generated_at=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        originating_agent="Baseline Research Agent",
        strategy_name=name,
        description="deterministic candidate",
        parameter_set={"rsi_period": 14},
        rationale="deterministic rationale",
        status="PROPOSED",
    )


def test_laboratory_status_with_one_registered_agent() -> None:
    laboratory = ResearchLaboratory(
        registration_provider=lambda: (
            ResearchAgentRegistration(
                agent_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
                agent_name="Baseline Research Agent",
                capabilities=("Generate deterministic candidate strategies",),
            ),
        ),
        candidates_provider=lambda: (_candidate("MA-RSI Blend rsi14", "c1"),),
    )

    status = laboratory.get_status()
    assert status.status == "IDLE"
    assert status.registered_agents == ("Baseline Research Agent",)
    assert status.last_run is None


def test_laboratory_status_with_multiple_registered_agents() -> None:
    laboratory = ResearchLaboratory(
        registration_provider=lambda: (
            ResearchAgentRegistration(
                agent_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
                agent_name="Baseline Research Agent",
                capabilities=("Generate deterministic candidate strategies",),
            ),
            ResearchAgentRegistration(
                agent_id=uuid.UUID("66666666-6666-6666-6666-666666666667"),
                agent_name="RSI Variant Agent",
                capabilities=("Generate deterministic candidate strategies",),
            ),
        ),
        candidates_provider=lambda: (
            _candidate("MA-RSI Blend rsi14", "c1"),
            _candidate("MA-RSI Blend rsi10", "c2"),
        ),
    )

    status = laboratory.get_status()
    assert status.status == "IDLE"
    assert status.registered_agents == ("Baseline Research Agent", "RSI Variant Agent")


def test_laboratory_run_completes_successfully() -> None:
    laboratory = ResearchLaboratory(
        registration_provider=lambda: (
            ResearchAgentRegistration(
                agent_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
                agent_name="Baseline Research Agent",
                capabilities=("Generate deterministic candidate strategies",),
            ),
        ),
        candidates_provider=lambda: (
            _candidate("MA-RSI Blend rsi14", "c1"),
            _candidate("MA-RSI Blend rsi10", "c2"),
            _candidate("MA Crossover 9/30", "c3"),
        ),
    )

    run = laboratory.run()
    assert run.status == "COMPLETED"
    assert run.participating_agents == ("Baseline Research Agent",)
    assert run.generated_candidates == 3
    assert run.evaluated_candidates == 3
    assert run.completed_at is not None

    status = laboratory.get_status()
    assert status.status == "COMPLETED"
    assert status.candidates_generated == 3
    assert status.candidates_evaluated == 3
    assert status.success_rate == "100.00%"


def test_laboratory_run_handles_empty_registry() -> None:
    laboratory = ResearchLaboratory(
        registration_provider=lambda: tuple(),
        candidates_provider=lambda: tuple(),
    )

    run = laboratory.run()
    assert run.status == "EMPTY"
    assert run.participating_agents == tuple()
    assert run.generated_candidates == 0
    assert run.evaluated_candidates == 0

    status = laboratory.get_status()
    assert status.status == "EMPTY"
    assert status.registered_agents == tuple()
    assert status.success_rate == "0.00%"


def test_laboratory_run_records_memory_when_recorder_is_configured() -> None:
    captured: list[tuple[str, int, int]] = []

    def _memory_recorder(run: ResearchLaboratoryRun, candidates: list[StrategyCandidate], evaluations: list[CandidateEvaluation]) -> None:
        captured.append((run.status, len(candidates), len(evaluations)))

    laboratory = ResearchLaboratory(
        registration_provider=lambda: (
            ResearchAgentRegistration(
                agent_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
                agent_name="Baseline Research Agent",
                capabilities=("Generate deterministic candidate strategies",),
            ),
        ),
        candidates_provider=lambda: (
            _candidate("MA-RSI Blend rsi14", "c1"),
            _candidate("MA-RSI Blend rsi10", "c2"),
        ),
        memory_recorder=_memory_recorder,
    )

    laboratory.run()

    assert captured == [("COMPLETED", 2, 2)]
