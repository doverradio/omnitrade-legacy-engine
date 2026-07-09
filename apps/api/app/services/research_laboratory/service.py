from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import uuid

from app.services.candidate_evaluation.deterministic import build_candidate_evaluations_batch_v1
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_agents.registry import (
    ResearchAgentRegistration,
    list_generated_strategy_candidates,
    list_registered_research_agents,
)
from app.services.research_laboratory.interface import ResearchLaboratoryRun, ResearchLaboratoryStatus


class ResearchLaboratory:
    def __init__(
        self,
        *,
        registration_provider: Callable[[], tuple[ResearchAgentRegistration, ...]] = list_registered_research_agents,
        candidates_provider: Callable[[], tuple[StrategyCandidate, ...]] = list_generated_strategy_candidates,
        evaluation_builder: Callable[..., list[CandidateEvaluation]] = build_candidate_evaluations_batch_v1,
    ) -> None:
        self._registration_provider = registration_provider
        self._candidates_provider = candidates_provider
        self._evaluation_builder = evaluation_builder
        self._last_run: ResearchLaboratoryRun | None = None
        self._batch_metadata: dict[str, dict[str, list[str]]] = {}

    def get_status(self) -> ResearchLaboratoryStatus:
        registrations = self._registration_provider()
        registered_agents = tuple(item.agent_name for item in registrations)

        if self._last_run is None:
            status = "EMPTY" if not registrations else "IDLE"
            return ResearchLaboratoryStatus(
                status=status,
                registered_agents=registered_agents,
                last_run=None,
                candidates_generated=0,
                candidates_evaluated=0,
                success_rate="0.00%",
            )

        success_rate = _compute_success_rate(
            generated_candidates=self._last_run.generated_candidates,
            evaluated_candidates=self._last_run.evaluated_candidates,
        )
        return ResearchLaboratoryStatus(
            status=self._last_run.status,
            registered_agents=registered_agents,
            last_run=self._last_run,
            candidates_generated=self._last_run.generated_candidates,
            candidates_evaluated=self._last_run.evaluated_candidates,
            success_rate=success_rate,
        )

    def run(self) -> ResearchLaboratoryRun:
        started_at = datetime.now(timezone.utc)
        registrations = self._registration_provider()
        participating_agents = tuple(item.agent_name for item in registrations)

        if not registrations:
            completed_at = datetime.now(timezone.utc)
            run = ResearchLaboratoryRun(
                laboratory_run_id=uuid.uuid4(),
                started_at=started_at,
                completed_at=completed_at,
                participating_agents=tuple(),
                generated_candidates=0,
                evaluated_candidates=0,
                status="EMPTY",
            )
            self._last_run = run
            self._batch_metadata[str(run.laboratory_run_id)] = {
                "candidate_ids": [],
                "evaluation_ids": [],
            }
            return run

        candidates = list(self._candidates_provider())
        evaluations = self._evaluation_builder(
            candidates=candidates,
            selected_candidate_ids=None,
            limit=None,
        )
        completed_at = datetime.now(timezone.utc)

        run = ResearchLaboratoryRun(
            laboratory_run_id=uuid.uuid4(),
            started_at=started_at,
            completed_at=completed_at,
            participating_agents=participating_agents,
            generated_candidates=len(candidates),
            evaluated_candidates=len(evaluations),
            status="COMPLETED",
        )

        self._last_run = run
        self._batch_metadata[str(run.laboratory_run_id)] = {
            "candidate_ids": [str(item.candidate_id) for item in candidates],
            "evaluation_ids": [str(item.evaluation_id) for item in evaluations],
        }
        return run


def _compute_success_rate(*, generated_candidates: int, evaluated_candidates: int) -> str:
    if generated_candidates <= 0:
        return "0.00%"
    return f"{(evaluated_candidates / generated_candidates) * 100:.2f}%"
