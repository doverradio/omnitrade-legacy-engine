from __future__ import annotations

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_memory.interface import (
    ResearchMemoryAgentParticipationRecord,
    ResearchMemoryCandidateRecord,
    ResearchMemoryLaboratoryRunRecord,
    ResearchMemorySummary,
    ResearchMemoryTournamentOutcomeRecord,
)


class ResearchMemory:
    def __init__(self) -> None:
        self._laboratory_runs: list[ResearchMemoryLaboratoryRunRecord] = []
        self._candidate_history: list[ResearchMemoryCandidateRecord] = []
        self._tournament_outcomes: list[ResearchMemoryTournamentOutcomeRecord] = []
        self._agent_participation: list[ResearchMemoryAgentParticipationRecord] = []

    def clear(self) -> None:
        self._laboratory_runs.clear()
        self._candidate_history.clear()
        self._tournament_outcomes.clear()
        self._agent_participation.clear()

    def record_laboratory_run(
        self,
        *,
        run: ResearchLaboratoryRun,
        candidates: list[StrategyCandidate],
        evaluations: list[CandidateEvaluation],
    ) -> None:
        run_record = ResearchMemoryLaboratoryRunRecord(
            laboratory_run_id=run.laboratory_run_id,
            started_at=run.started_at,
            completed_at=run.completed_at,
            participating_agents=run.participating_agents,
            candidates_generated=run.generated_candidates,
            candidates_evaluated=run.evaluated_candidates,
        )
        self._laboratory_runs.append(run_record)

        for agent_name in run.participating_agents:
            self._agent_participation.append(
                ResearchMemoryAgentParticipationRecord(
                    laboratory_run_id=run.laboratory_run_id,
                    agent_name=agent_name,
                )
            )

        evaluation_by_candidate_id = {
            item.candidate_id: item
            for item in evaluations
        }

        for candidate in candidates:
            evaluation = evaluation_by_candidate_id.get(candidate.candidate_id)
            candidate_record = ResearchMemoryCandidateRecord(
                laboratory_run_id=run.laboratory_run_id,
                candidate_id=candidate.candidate_id,
                originating_agent=candidate.originating_agent,
                parameter_set=dict(candidate.parameter_set),
                evaluation_summary=None if evaluation is None else evaluation.ai_coach_summary,
                quality_score=None if evaluation is None else evaluation.decision_quality_score,
                tournament_rank=None if evaluation is None else evaluation.tournament_rank,
                status="EVALUATED" if evaluation is not None else candidate.status,
            )
            self._candidate_history.append(candidate_record)

            if evaluation is not None and evaluation.tournament_rank is not None:
                self._tournament_outcomes.append(
                    ResearchMemoryTournamentOutcomeRecord(
                        laboratory_run_id=run.laboratory_run_id,
                        candidate_id=candidate.candidate_id,
                        tournament_rank=evaluation.tournament_rank,
                    )
                )

    def get_summary(self) -> ResearchMemorySummary:
        highest_quality_candidate = self._resolve_highest_quality_candidate()
        quality_scores = [
            item.quality_score
            for item in self._candidate_history
            if item.quality_score is not None
        ]
        average_quality_score = (
            None
            if not quality_scores
            else round(sum(quality_scores) / len(quality_scores), 2)
        )

        latest_laboratory_run = self._laboratory_runs[-1] if self._laboratory_runs else None
        return ResearchMemorySummary(
            total_laboratory_runs=len(self._laboratory_runs),
            total_candidates=len(self._candidate_history),
            highest_quality_candidate=highest_quality_candidate,
            average_quality_score=average_quality_score,
            latest_laboratory_run=latest_laboratory_run,
        )

    def list_runs(self) -> tuple[ResearchMemoryLaboratoryRunRecord, ...]:
        return tuple(reversed(self._laboratory_runs))

    def list_candidates(self) -> tuple[ResearchMemoryCandidateRecord, ...]:
        return tuple(reversed(self._candidate_history))

    def list_tournament_outcomes(self) -> tuple[ResearchMemoryTournamentOutcomeRecord, ...]:
        return tuple(reversed(self._tournament_outcomes))

    def list_agent_participation(self) -> tuple[ResearchMemoryAgentParticipationRecord, ...]:
        return tuple(reversed(self._agent_participation))

    def _resolve_highest_quality_candidate(self) -> ResearchMemoryCandidateRecord | None:
        scored_candidates = [
            item
            for item in self._candidate_history
            if item.quality_score is not None
        ]
        if not scored_candidates:
            return None

        return max(
            scored_candidates,
            key=lambda item: (
                int(item.quality_score or 0),
                -(item.tournament_rank or 999999),
            ),
        )
