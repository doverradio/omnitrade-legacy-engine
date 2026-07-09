from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolvedCandidate, EvolutionMutation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_agents.registry import list_generated_strategy_candidates
from app.services.research_campaign.registry import get_research_campaign_engine
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_memory.interface import ResearchMemoryCandidateRecord
from app.services.research_memory.registry import get_research_memory
from app.services.research_persistence.repository import ResearchPersistenceRepository


async def flush_legacy_research_state(
    *,
    db: AsyncSession,
    repository: ResearchPersistenceRepository,
) -> bool:
    summary = await repository.get_summary(db=db)
    if summary.total_laboratory_runs > 0 or summary.total_candidates > 0:
        return False

    memory = get_research_memory()
    runs = list(reversed(memory.list_runs()))
    candidates = list(memory.list_candidates())

    strategy_by_id = {item.candidate_id: item for item in list_generated_strategy_candidates()}

    for run in runs:
        run_candidates = [
            item
            for item in candidates
            if item.laboratory_run_id == run.laboratory_run_id and item.parent_candidate_id is None
        ]
        await repository.record_laboratory_run(
            db=db,
            run=ResearchLaboratoryRun(
                laboratory_run_id=run.laboratory_run_id,
                started_at=run.started_at,
                completed_at=run.completed_at,
                participating_agents=run.participating_agents,
                generated_candidates=run.candidates_generated,
                evaluated_candidates=run.candidates_evaluated,
                status="COMPLETED",
            ),
            candidates=[_to_strategy_candidate(item, strategy_by_id=strategy_by_id) for item in run_candidates],
            evaluations=[_to_evaluation(item) for item in run_candidates if item.quality_score is not None],
            campaign_id=None,
        )

    evolved = [item for item in candidates if item.parent_candidate_id is not None]
    await repository.record_evolved_candidates(
        db=db,
        descendants=[_to_evolved_candidate(item, strategy_by_id=strategy_by_id) for item in evolved],
        campaign_id=None,
    )

    campaigns = list(reversed(get_research_campaign_engine().list_campaigns()))
    for campaign in campaigns:
        persisted = await repository.create_campaign(
            db=db,
            name=campaign.name,
            objective=campaign.objective,
            participating_agents=campaign.participating_agents,
        )
        await repository.upsert_campaign_statistics(
            db=db,
            campaign_id=persisted.campaign_id,
            laboratory_runs_increment=campaign.laboratory_runs,
            candidates_generated_increment=campaign.candidates_generated,
            candidates_evaluated_increment=campaign.candidates_evaluated,
            best_candidate_id=None,
            best_quality_score=campaign.best_quality_score,
            current_champion=campaign.current_champion,
            status=campaign.status,
            participating_agents=campaign.participating_agents,
        )

    memory.clear()
    get_research_campaign_engine().clear()
    return True


def _to_strategy_candidate(
    item: ResearchMemoryCandidateRecord,
    *,
    strategy_by_id: dict[uuid.UUID, StrategyCandidate],
) -> StrategyCandidate:
    existing = strategy_by_id.get(item.candidate_id)
    if existing is not None:
        return existing

    return StrategyCandidate(
        candidate_id=item.candidate_id,
        generated_at=datetime.now(timezone.utc),
        originating_agent=item.originating_agent,
        strategy_name=f"Recovered {item.candidate_id}",
        description="Recovered from legacy in-memory state",
        parameter_set=dict(item.parameter_set),
        rationale=item.evaluation_summary or "Recovered from legacy in-memory state",
        status=item.status,
    )


def _to_evaluation(item: ResearchMemoryCandidateRecord) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=uuid.uuid5(
            uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
            f"legacy-eval:{item.candidate_id}:{item.quality_score}:{item.tournament_rank}",
        ),
        candidate_id=item.candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=int(item.quality_score or 0),
        ai_coach_summary=item.evaluation_summary or "Recovered from legacy in-memory state.",
        decision_intelligence_summary="Recovered from legacy in-memory state.",
        tournament_rank=item.tournament_rank,
        promotion_eligible=False,
    )


def _to_evolved_candidate(
    item: ResearchMemoryCandidateRecord,
    *,
    strategy_by_id: dict[uuid.UUID, StrategyCandidate],
) -> EvolvedCandidate:
    strategy_name = strategy_by_id[item.candidate_id].strategy_name if item.candidate_id in strategy_by_id else f"Recovered {item.candidate_id}"
    mutation_reason = item.mutation_reason or "Recovered from legacy in-memory state"
    return EvolvedCandidate(
        candidate_id=item.candidate_id,
        parent_candidate_id=item.parent_candidate_id or uuid.UUID("00000000-0000-0000-0000-000000000000"),
        generation=item.generation,
        mutation_reason=mutation_reason,
        parameter_diff=tuple(
            EvolutionMutation(
                parameter_name=diff.parameter_name,
                previous_value=diff.previous_value,
                new_value=diff.new_value,
            )
            for diff in item.parameter_diff
        ),
        parameter_set=dict(item.parameter_set),
        strategy_name=strategy_name,
        originating_agent=item.originating_agent,
        generated_at=datetime.now(timezone.utc),
        quality_score=item.quality_score,
        tournament_rank=item.tournament_rank,
        status=item.status,
    )
