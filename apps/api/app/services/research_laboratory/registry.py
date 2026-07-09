from __future__ import annotations

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_laboratory.service import ResearchLaboratory
from app.services.research_memory.registry import get_research_memory


def _record_laboratory_run_in_memory(
    run: ResearchLaboratoryRun,
    candidates: list[StrategyCandidate],
    evaluations: list[CandidateEvaluation],
) -> None:
    memory = get_research_memory()
    memory.record_laboratory_run(
        run=run,
        candidates=candidates,
        evaluations=evaluations,
    )


_LABORATORY = ResearchLaboratory(memory_recorder=_record_laboratory_run_in_memory)


def get_research_laboratory() -> ResearchLaboratory:
    return _LABORATORY
