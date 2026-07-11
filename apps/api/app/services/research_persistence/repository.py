from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_agent_activity import ResearchAgentActivity as ResearchAgentActivityModel
from app.models.research_campaign import ResearchCampaign as ResearchCampaignModel
from app.models.research_campaign_statistic import ResearchCampaignStatistic as ResearchCampaignStatisticModel
from app.models.research_candidate import ResearchCandidate as ResearchCandidateModel
from app.models.research_candidate_evaluation import ResearchCandidateEvaluation as ResearchCandidateEvaluationModel
from app.models.research_candidate_lineage import ResearchCandidateLineage as ResearchCandidateLineageModel
from app.models.research_laboratory_run import ResearchLaboratoryRun as ResearchLaboratoryRunModel
from app.models.research_memory_entry import ResearchMemoryEntry as ResearchMemoryEntryModel
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolvedCandidate
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_campaign.interface import ResearchCampaign
from app.services.research_laboratory.interface import ResearchLaboratoryRun, ResearchLaboratoryStatus
from app.services.research_memory.interface import (
    ResearchMemoryCandidateRecord,
    ResearchMemoryLaboratoryRunRecord,
    ResearchMemoryParameterDiffRecord,
    ResearchMemorySummary,
    ResearchMemoryTournamentOutcomeRecord,
)


@asynccontextmanager
async def _transaction_scope(db: AsyncSession):
    if not hasattr(db, "begin") or not hasattr(db, "in_transaction"):
        yield
        return

    manager = db.begin_nested() if db.in_transaction() else db.begin()
    async with manager:
        yield


class ResearchPersistenceRepository:
    async def record_laboratory_run(
        self,
        *,
        db: AsyncSession,
        run: ResearchLaboratoryRun,
        candidates: list[StrategyCandidate],
        evaluations: list[CandidateEvaluation],
        campaign_id: uuid.UUID | None = None,
    ) -> None:
        async with _transaction_scope(db):
            run_row = ResearchLaboratoryRunModel(
                run_id=run.laboratory_run_id,
                started_at=run.started_at,
                completed_at=run.completed_at,
                participating_agents=list(run.participating_agents),
                status=run.status,
                generated_candidates=run.generated_candidates,
                evaluated_candidates=run.evaluated_candidates,
                metadata_json={},
            )
            db.add(run_row)
            # Persist the parent before any child row or query-triggered autoflush can reference it.
            await db.flush()

            no_autoflush = getattr(db, "no_autoflush", nullcontext())
            with no_autoflush:
                for agent_name in run.participating_agents:
                    db.add(
                        ResearchAgentActivityModel(
                            laboratory_run_id=run.laboratory_run_id,
                            campaign_id=campaign_id,
                            agent_name=agent_name,
                            activity_type="laboratory_participation",
                            metadata_json={"status": run.status},
                        )
                    )

                evaluation_map = {item.candidate_id: item for item in evaluations}
                for candidate in candidates:
                    evaluation = evaluation_map.get(candidate.candidate_id)
                    row = await self._upsert_candidate(
                        db=db,
                        candidate_id=candidate.candidate_id,
                        laboratory_run_id=run.laboratory_run_id,
                        campaign_id=campaign_id,
                        parent_candidate_id=None,
                        originating_agent=candidate.originating_agent,
                        strategy_name=candidate.strategy_name,
                        description=candidate.description,
                        parameter_set=dict(candidate.parameter_set),
                        rationale=candidate.rationale,
                        status="EVALUATED" if evaluation is not None else candidate.status,
                        generation=1,
                        mutation_reason=None,
                        parameter_diff=[],
                        generated_at=candidate.generated_at,
                    )
                    if evaluation is not None:
                        await self._record_evaluation(
                            db=db,
                            evaluation=evaluation,
                            candidate_id=row.candidate_id,
                            laboratory_run_id=run.laboratory_run_id,
                        )

                    db.add(
                        ResearchMemoryEntryModel(
                            entry_type="laboratory_candidate",
                            laboratory_run_id=run.laboratory_run_id,
                            candidate_id=row.candidate_id,
                            payload={
                                "originating_agent": row.originating_agent,
                                "strategy_name": row.strategy_name,
                                "status": row.status,
                            },
                        )
                    )

                db.add(
                    ResearchMemoryEntryModel(
                        entry_type="laboratory_run",
                        laboratory_run_id=run.laboratory_run_id,
                        candidate_id=None,
                        payload={
                            "status": run.status,
                            "generated_candidates": run.generated_candidates,
                            "evaluated_candidates": run.evaluated_candidates,
                        },
                    )
                )

    async def record_evolved_candidates(
        self,
        *,
        db: AsyncSession,
        descendants: list[EvolvedCandidate],
        campaign_id: uuid.UUID | None = None,
    ) -> None:
        if not descendants:
            return

        for descendant in descendants:
            row = await self._upsert_candidate(
                db=db,
                candidate_id=descendant.candidate_id,
                laboratory_run_id=None,
                campaign_id=campaign_id,
                parent_candidate_id=descendant.parent_candidate_id,
                originating_agent=descendant.originating_agent,
                strategy_name=descendant.strategy_name,
                description=f"Evolved descendant from {descendant.parent_candidate_id}",
                parameter_set=dict(descendant.parameter_set),
                rationale=descendant.mutation_reason,
                status=descendant.status,
                generation=descendant.generation,
                mutation_reason=descendant.mutation_reason,
                parameter_diff=[
                    {
                        "parameter_name": diff.parameter_name,
                        "previous_value": diff.previous_value,
                        "new_value": diff.new_value,
                    }
                    for diff in descendant.parameter_diff
                ],
                generated_at=descendant.generated_at,
            )
            if descendant.parent_candidate_id is not None:
                existing_lineage = await db.scalar(
                    select(ResearchCandidateLineageModel).where(
                        ResearchCandidateLineageModel.candidate_id == row.candidate_id
                    )
                )
                if existing_lineage is None:
                    db.add(
                        ResearchCandidateLineageModel(
                            candidate_id=row.candidate_id,
                            parent_candidate_id=descendant.parent_candidate_id,
                            mutation_reason=descendant.mutation_reason,
                            parameter_diff=[
                                {
                                    "parameter_name": diff.parameter_name,
                                    "previous_value": diff.previous_value,
                                    "new_value": diff.new_value,
                                }
                                for diff in descendant.parameter_diff
                            ],
                        )
                    )

            if descendant.quality_score is not None:
                evaluation = CandidateEvaluation(
                    evaluation_id=uuid.uuid5(
                        uuid.UUID("00000000-0000-0000-0000-000000000099"),
                        f"evolved:{descendant.candidate_id}:{descendant.quality_score}:{descendant.tournament_rank}",
                    ),
                    candidate_id=descendant.candidate_id,
                    replay_status="COMPLETED",
                    decision_quality_score=int(descendant.quality_score),
                    ai_coach_summary=descendant.mutation_reason,
                    decision_intelligence_summary="Evolved candidate deterministic evaluation.",
                    tournament_rank=descendant.tournament_rank,
                    promotion_eligible=False,
                )
                await self._record_evaluation(
                    db=db,
                    evaluation=evaluation,
                    candidate_id=row.candidate_id,
                    laboratory_run_id=row.laboratory_run_id,
                )

            db.add(
                ResearchMemoryEntryModel(
                    entry_type="evolved_candidate",
                    laboratory_run_id=row.laboratory_run_id,
                    candidate_id=row.candidate_id,
                    payload={
                        "generation": row.generation,
                        "parent_candidate_id": str(row.parent_candidate_id) if row.parent_candidate_id else None,
                        "status": row.status,
                    },
                )
            )

    async def list_runs(
        self,
        *,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> tuple[ResearchMemoryLaboratoryRunRecord, ...]:
        rows = (
            await db.execute(
                select(ResearchLaboratoryRunModel)
                .order_by(ResearchLaboratoryRunModel.started_at.desc(), ResearchLaboratoryRunModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return tuple(
            ResearchMemoryLaboratoryRunRecord(
                laboratory_run_id=item.run_id,
                started_at=item.started_at,
                completed_at=item.completed_at,
                participating_agents=tuple(item.participating_agents),
                candidates_generated=item.generated_candidates,
                candidates_evaluated=item.evaluated_candidates,
            )
            for item in rows
        )

    async def list_candidates(
        self,
        *,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> tuple[ResearchMemoryCandidateRecord, ...]:
        rows = (
            await db.execute(
                select(ResearchCandidateModel)
                .order_by(ResearchCandidateModel.generated_at.desc(), ResearchCandidateModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

        candidate_ids = [item.candidate_id for item in rows]
        evaluations = (
            await db.execute(
                select(ResearchCandidateEvaluationModel)
                .where(ResearchCandidateEvaluationModel.candidate_id.in_(candidate_ids) if candidate_ids else false())
                .order_by(
                    ResearchCandidateEvaluationModel.candidate_id.asc(),
                    ResearchCandidateEvaluationModel.created_at.desc(),
                )
            )
        ).scalars().all()

        evaluation_by_candidate: dict[uuid.UUID, ResearchCandidateEvaluationModel] = {}
        for evaluation in evaluations:
            if evaluation.candidate_id not in evaluation_by_candidate:
                evaluation_by_candidate[evaluation.candidate_id] = evaluation

        return tuple(
            ResearchMemoryCandidateRecord(
                laboratory_run_id=item.laboratory_run_id
                if item.laboratory_run_id is not None
                else uuid.UUID("00000000-0000-0000-0000-000000000000"),
                candidate_id=item.candidate_id,
                originating_agent=item.originating_agent,
                parameter_set=dict(item.parameter_set),
                evaluation_summary=(
                    evaluation_by_candidate[item.candidate_id].ai_coach_summary
                    if item.candidate_id in evaluation_by_candidate
                    else item.mutation_reason
                ),
                quality_score=(
                    evaluation_by_candidate[item.candidate_id].decision_quality_score
                    if item.candidate_id in evaluation_by_candidate
                    else None
                ),
                tournament_rank=(
                    evaluation_by_candidate[item.candidate_id].tournament_rank
                    if item.candidate_id in evaluation_by_candidate
                    else None
                ),
                status=item.status,
                parent_candidate_id=item.parent_candidate_id,
                generation=item.generation,
                mutation_reason=item.mutation_reason,
                parameter_diff=tuple(
                    ResearchMemoryParameterDiffRecord(
                        parameter_name=str(diff.get("parameter_name")),
                        previous_value=int(diff.get("previous_value", 0)),
                        new_value=int(diff.get("new_value", 0)),
                    )
                    for diff in item.parameter_diff
                ),
            )
            for item in rows
        )

    async def list_tournament_outcomes(
        self,
        *,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> tuple[ResearchMemoryTournamentOutcomeRecord, ...]:
        rows = (
            await db.execute(
                select(ResearchCandidateEvaluationModel)
                .where(ResearchCandidateEvaluationModel.tournament_rank.is_not(None))
                .order_by(
                    ResearchCandidateEvaluationModel.created_at.desc(),
                    ResearchCandidateEvaluationModel.tournament_rank.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return tuple(
            ResearchMemoryTournamentOutcomeRecord(
                laboratory_run_id=(
                    item.laboratory_run_id
                    if item.laboratory_run_id is not None
                    else uuid.UUID("00000000-0000-0000-0000-000000000000")
                ),
                candidate_id=item.candidate_id,
                tournament_rank=item.tournament_rank or 0,
            )
            for item in rows
        )

    async def get_summary(self, *, db: AsyncSession) -> ResearchMemorySummary:
        total_runs = await db.scalar(select(func.count()).select_from(ResearchLaboratoryRunModel))
        total_candidates = await db.scalar(select(func.count()).select_from(ResearchCandidateModel))
        average_quality = await db.scalar(select(func.avg(ResearchCandidateEvaluationModel.decision_quality_score)))

        latest_run = await db.scalar(
            select(ResearchLaboratoryRunModel)
            .order_by(ResearchLaboratoryRunModel.started_at.desc(), ResearchLaboratoryRunModel.created_at.desc())
            .limit(1)
        )

        top_evaluation = await db.scalar(
            select(ResearchCandidateEvaluationModel)
            .order_by(
                ResearchCandidateEvaluationModel.decision_quality_score.desc(),
                ResearchCandidateEvaluationModel.tournament_rank.asc().nullslast(),
                ResearchCandidateEvaluationModel.created_at.desc(),
            )
            .limit(1)
        )

        highest_candidate: ResearchMemoryCandidateRecord | None = None
        if top_evaluation is not None:
            candidate = await db.scalar(
                select(ResearchCandidateModel).where(ResearchCandidateModel.candidate_id == top_evaluation.candidate_id)
            )
            if candidate is not None:
                highest_candidate = ResearchMemoryCandidateRecord(
                    laboratory_run_id=(
                        candidate.laboratory_run_id
                        if candidate.laboratory_run_id is not None
                        else uuid.UUID("00000000-0000-0000-0000-000000000000")
                    ),
                    candidate_id=candidate.candidate_id,
                    originating_agent=candidate.originating_agent,
                    parameter_set=dict(candidate.parameter_set),
                    evaluation_summary=top_evaluation.ai_coach_summary,
                    quality_score=top_evaluation.decision_quality_score,
                    tournament_rank=top_evaluation.tournament_rank,
                    status=candidate.status,
                    parent_candidate_id=candidate.parent_candidate_id,
                    generation=candidate.generation,
                    mutation_reason=candidate.mutation_reason,
                    parameter_diff=tuple(
                        ResearchMemoryParameterDiffRecord(
                            parameter_name=str(diff.get("parameter_name")),
                            previous_value=int(diff.get("previous_value", 0)),
                            new_value=int(diff.get("new_value", 0)),
                        )
                        for diff in candidate.parameter_diff
                    ),
                )

        return ResearchMemorySummary(
            total_laboratory_runs=int(total_runs or 0),
            total_candidates=int(total_candidates or 0),
            highest_quality_candidate=highest_candidate,
            average_quality_score=None if average_quality is None else round(float(average_quality), 2),
            latest_laboratory_run=(
                None
                if latest_run is None
                else ResearchMemoryLaboratoryRunRecord(
                    laboratory_run_id=latest_run.run_id,
                    started_at=latest_run.started_at,
                    completed_at=latest_run.completed_at,
                    participating_agents=tuple(latest_run.participating_agents),
                    candidates_generated=latest_run.generated_candidates,
                    candidates_evaluated=latest_run.evaluated_candidates,
                )
            ),
        )

    async def get_laboratory_status(
        self,
        *,
        db: AsyncSession,
        registered_agents: tuple[str, ...],
    ) -> ResearchLaboratoryStatus:
        latest_run = await db.scalar(
            select(ResearchLaboratoryRunModel)
            .order_by(ResearchLaboratoryRunModel.started_at.desc(), ResearchLaboratoryRunModel.created_at.desc())
            .limit(1)
        )

        if latest_run is None:
            status = "EMPTY" if not registered_agents else "IDLE"
            return ResearchLaboratoryStatus(
                status=status,
                registered_agents=registered_agents,
                last_run=None,
                candidates_generated=0,
                candidates_evaluated=0,
                success_rate="0.00%",
            )

        success_rate = "0.00%"
        if latest_run.generated_candidates > 0:
            success_rate = f"{(latest_run.evaluated_candidates / latest_run.generated_candidates) * 100:.2f}%"

        run = ResearchLaboratoryRun(
            laboratory_run_id=latest_run.run_id,
            started_at=latest_run.started_at,
            completed_at=latest_run.completed_at,
            participating_agents=tuple(latest_run.participating_agents),
            generated_candidates=latest_run.generated_candidates,
            evaluated_candidates=latest_run.evaluated_candidates,
            status=latest_run.status,
        )
        return ResearchLaboratoryStatus(
            status=latest_run.status,
            registered_agents=registered_agents,
            last_run=run,
            candidates_generated=latest_run.generated_candidates,
            candidates_evaluated=latest_run.evaluated_candidates,
            success_rate=success_rate,
        )

    async def create_campaign(
        self,
        *,
        db: AsyncSession,
        name: str,
        objective: str,
        participating_agents: tuple[str, ...],
    ) -> ResearchCampaign:
        campaign = ResearchCampaignModel(
            name=name,
            objective=objective,
            status="IDLE",
            started_at=None,
            completed_at=None,
            participating_agents=list(participating_agents),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(campaign)
        await db.flush()

        db.add(
            ResearchCampaignStatisticModel(
                campaign_id=campaign.campaign_id,
                laboratory_runs=0,
                candidates_generated=0,
                candidates_evaluated=0,
                best_candidate_id=None,
                best_quality_score=None,
                current_champion=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()

        return ResearchCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            objective=campaign.objective,
            status=campaign.status,
            started_at=campaign.started_at,
            completed_at=campaign.completed_at,
            participating_agents=tuple(campaign.participating_agents),
            laboratory_runs=0,
            candidates_generated=0,
            candidates_evaluated=0,
            best_candidate=None,
            best_quality_score=None,
            current_champion=None,
        )

    async def get_campaign(self, *, db: AsyncSession, campaign_id: uuid.UUID) -> ResearchCampaign | None:
        campaign = await db.scalar(select(ResearchCampaignModel).where(ResearchCampaignModel.campaign_id == campaign_id))
        if campaign is None:
            return None

        stats = await db.scalar(
            select(ResearchCampaignStatisticModel).where(ResearchCampaignStatisticModel.campaign_id == campaign.campaign_id)
        )
        best_candidate = None
        if stats is not None and stats.best_candidate_id is not None:
            candidate = await db.scalar(
                select(ResearchCandidateModel).where(ResearchCandidateModel.candidate_id == stats.best_candidate_id)
            )
            best_candidate = None if candidate is None else candidate.strategy_name

        return ResearchCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            objective=campaign.objective,
            status=campaign.status,
            started_at=campaign.started_at,
            completed_at=campaign.completed_at,
            participating_agents=tuple(campaign.participating_agents),
            laboratory_runs=0 if stats is None else stats.laboratory_runs,
            candidates_generated=0 if stats is None else stats.candidates_generated,
            candidates_evaluated=0 if stats is None else stats.candidates_evaluated,
            best_candidate=best_candidate,
            best_quality_score=None if stats is None else stats.best_quality_score,
            current_champion=None if stats is None else stats.current_champion,
        )

    async def list_campaigns(
        self,
        *,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> tuple[ResearchCampaign, ...]:
        rows = (
            await db.execute(
                select(ResearchCampaignModel)
                .order_by(ResearchCampaignModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        campaigns: list[ResearchCampaign] = []
        for row in rows:
            loaded = await self.get_campaign(db=db, campaign_id=row.campaign_id)
            if loaded is not None:
                campaigns.append(loaded)
        return tuple(campaigns)

    async def upsert_campaign_statistics(
        self,
        *,
        db: AsyncSession,
        campaign_id: uuid.UUID,
        laboratory_runs_increment: int,
        candidates_generated_increment: int,
        candidates_evaluated_increment: int,
        best_candidate_id: uuid.UUID | None,
        best_quality_score: int | None,
        current_champion: str | None,
        status: str,
        participating_agents: tuple[str, ...],
    ) -> ResearchCampaign:
        campaign = await db.scalar(select(ResearchCampaignModel).where(ResearchCampaignModel.campaign_id == campaign_id))
        if campaign is None:
            raise LookupError(str(campaign_id))

        stats = await db.scalar(
            select(ResearchCampaignStatisticModel).where(ResearchCampaignStatisticModel.campaign_id == campaign_id)
        )
        if stats is None:
            stats = ResearchCampaignStatisticModel(campaign_id=campaign_id)
            db.add(stats)
            await db.flush()

        stats.laboratory_runs = int(stats.laboratory_runs) + laboratory_runs_increment
        stats.candidates_generated = int(stats.candidates_generated) + candidates_generated_increment
        stats.candidates_evaluated = int(stats.candidates_evaluated) + candidates_evaluated_increment
        if best_quality_score is not None and (stats.best_quality_score is None or best_quality_score > stats.best_quality_score):
            stats.best_quality_score = best_quality_score
            stats.best_candidate_id = best_candidate_id
        stats.current_champion = current_champion
        stats.updated_at = datetime.now(timezone.utc)

        campaign.status = status
        if campaign.started_at is None:
            campaign.started_at = datetime.now(timezone.utc)
        if status == "COMPLETED":
            campaign.completed_at = datetime.now(timezone.utc)
        campaign.participating_agents = list(sorted(set(campaign.participating_agents).union(participating_agents)))
        campaign.updated_at = datetime.now(timezone.utc)

        await db.flush()
        loaded = await self.get_campaign(db=db, campaign_id=campaign_id)
        if loaded is None:
            raise LookupError(str(campaign_id))
        return loaded

    async def list_strategy_candidates(
        self,
        *,
        db: AsyncSession,
        limit: int,
        offset: int,
    ) -> tuple[StrategyCandidate, ...]:
        rows = (
            await db.execute(
                select(ResearchCandidateModel)
                .order_by(ResearchCandidateModel.generated_at.desc(), ResearchCandidateModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return tuple(
            StrategyCandidate(
                candidate_id=item.candidate_id,
                generated_at=item.generated_at,
                originating_agent=item.originating_agent,
                strategy_name=item.strategy_name,
                description=item.description,
                parameter_set=dict(item.parameter_set),
                rationale=item.rationale,
                status=item.status,
            )
            for item in rows
        )

    async def _upsert_candidate(
        self,
        *,
        db: AsyncSession,
        candidate_id: uuid.UUID,
        laboratory_run_id: uuid.UUID | None,
        campaign_id: uuid.UUID | None,
        parent_candidate_id: uuid.UUID | None,
        originating_agent: str,
        strategy_name: str,
        description: str,
        parameter_set: dict[str, Any],
        rationale: str,
        status: str,
        generation: int,
        mutation_reason: str | None,
        parameter_diff: list[dict[str, Any]],
        generated_at: datetime,
    ) -> ResearchCandidateModel:
        row = await db.scalar(select(ResearchCandidateModel).where(ResearchCandidateModel.candidate_id == candidate_id))
        if row is None:
            row = ResearchCandidateModel(
                candidate_id=candidate_id,
                laboratory_run_id=laboratory_run_id,
                campaign_id=campaign_id,
                parent_candidate_id=parent_candidate_id,
                originating_agent=originating_agent,
                strategy_name=strategy_name,
                description=description,
                parameter_set=parameter_set,
                rationale=rationale,
                status=status,
                generation=generation,
                mutation_reason=mutation_reason,
                parameter_diff=parameter_diff,
                generated_at=generated_at,
            )
            db.add(row)
        else:
            row.laboratory_run_id = laboratory_run_id or row.laboratory_run_id
            row.campaign_id = campaign_id or row.campaign_id
            row.parent_candidate_id = parent_candidate_id or row.parent_candidate_id
            row.originating_agent = originating_agent
            row.strategy_name = strategy_name
            row.description = description
            row.parameter_set = parameter_set
            row.rationale = rationale
            row.status = status
            row.generation = generation
            row.mutation_reason = mutation_reason
            row.parameter_diff = parameter_diff
            row.generated_at = generated_at
        await db.flush()
        return row

    async def _record_evaluation(
        self,
        *,
        db: AsyncSession,
        evaluation: CandidateEvaluation,
        candidate_id: uuid.UUID,
        laboratory_run_id: uuid.UUID | None,
    ) -> None:
        existing = await db.scalar(
            select(ResearchCandidateEvaluationModel).where(
                ResearchCandidateEvaluationModel.evaluation_id == evaluation.evaluation_id
            )
        )
        if existing is not None:
            return

        db.add(
            ResearchCandidateEvaluationModel(
                evaluation_id=evaluation.evaluation_id,
                candidate_id=candidate_id,
                laboratory_run_id=laboratory_run_id,
                replay_status=evaluation.replay_status,
                decision_quality_score=evaluation.decision_quality_score,
                ai_coach_summary=evaluation.ai_coach_summary,
                decision_intelligence_summary=evaluation.decision_intelligence_summary,
                tournament_rank=evaluation.tournament_rank,
                promotion_eligible=evaluation.promotion_eligible,
            )
        )
        db.add(
            ResearchMemoryEntryModel(
                entry_type="candidate_evaluation",
                laboratory_run_id=laboratory_run_id,
                candidate_id=candidate_id,
                payload={
                    "evaluation_id": str(evaluation.evaluation_id),
                    "replay_status": evaluation.replay_status,
                    "decision_quality_score": evaluation.decision_quality_score,
                    "tournament_rank": evaluation.tournament_rank,
                },
            )
        )
        await db.flush()
