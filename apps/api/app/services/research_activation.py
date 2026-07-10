from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.decision_record import DecisionRecord
from app.models.research_candidate import ResearchCandidate
from app.models.research_campaign import ResearchCampaign as ResearchCampaignModel
from app.models.research_laboratory_run import ResearchLaboratoryRun as ResearchLaboratoryRunModel
from app.models.signal import Signal
from app.models.trade import Trade
from app.models.validation_run import ValidationRun
from app.models.validation_run_event import ValidationRunEvent
from app.services.candidate_evaluation.deterministic import build_candidate_evaluations_batch_v1
from app.services.evolution.registry import get_evolution_engine
from app.services.evolution_analytics.service import EvolutionAnalyticsService
from app.services.research_agents.registry import list_generated_strategy_candidates, list_registered_research_agents
from app.services.research_laboratory.registry import get_research_laboratory
from app.services.research_persistence.repository import ResearchPersistenceRepository


@dataclass(frozen=True, slots=True)
class ResearchCycleResult:
    started: bool
    campaign_id: uuid.UUID | None
    candidates_generated: int
    candidates_evaluated: int
    descendants_generated: int
    champion: str | None
    reason: str | None


async def run_deterministic_research_cycle_if_due(*, db: AsyncSession) -> ResearchCycleResult:
    settings = get_settings()
    if not settings.research_evolution_enabled:
        return ResearchCycleResult(False, None, 0, 0, 0, None, "research_disabled")
    if not hasattr(db, "scalar") or not hasattr(db, "execute"):
        return ResearchCycleResult(False, None, 0, 0, 0, None, "research_query_surface_unavailable")

    now = datetime.now(timezone.utc)
    latest_run_started_at = await db.scalar(
        select(ResearchLaboratoryRunModel.started_at)
        .order_by(ResearchLaboratoryRunModel.started_at.desc(), ResearchLaboratoryRunModel.created_at.desc())
        .limit(1)
    )
    if latest_run_started_at is not None and now - latest_run_started_at.astimezone(timezone.utc) < timedelta(minutes=settings.research_cycle_interval_minutes):
        return ResearchCycleResult(False, None, 0, 0, 0, None, "research_interval_not_elapsed")

    running_campaign = await db.scalar(
        select(ResearchCampaignModel.campaign_id)
        .where(ResearchCampaignModel.status == "RUNNING")
        .limit(1)
    )
    if running_campaign is not None:
        return ResearchCycleResult(False, running_campaign, 0, 0, 0, None, "research_cycle_already_running")

    repository = ResearchPersistenceRepository()
    laboratory = get_research_laboratory()
    campaign = await repository.create_campaign(
        db=db,
        name=f"Deterministic Research {now.strftime('%Y-%m-%d %H:%M')}",
        objective="Bounded deterministic paper-only research cycle.",
        participating_agents=tuple(item.agent_name for item in list_registered_research_agents()),
    )
    await repository.upsert_campaign_statistics(
        db=db,
        campaign_id=campaign.campaign_id,
        laboratory_runs_increment=0,
        candidates_generated_increment=0,
        candidates_evaluated_increment=0,
        best_candidate_id=None,
        best_quality_score=None,
        current_champion=None,
        status="RUNNING",
        participating_agents=campaign.participating_agents,
    )

    validation_run_ids = await _load_active_validation_run_ids(db=db)
    await _emit_validation_event(
        db=db,
        validation_run_ids=validation_run_ids,
        event_type="RESEARCH_CYCLE_STARTED",
        severity="purple",
        title="Research Cycle Started",
        description="Deterministic research cycle started.",
        metadata={"campaign_id": str(campaign.campaign_id)},
    )

    run = laboratory.run()
    candidates = list(list_generated_strategy_candidates())[: settings.research_max_candidates_per_cycle]
    evaluations = build_candidate_evaluations_batch_v1(
        candidates=list(candidates),
        selected_candidate_ids=[item.candidate_id for item in candidates],
        limit=settings.research_max_candidates_per_cycle,
    )
    await repository.record_laboratory_run(
        db=db,
        run=run,
        candidates=list(candidates),
        evaluations=list(evaluations),
        campaign_id=campaign.campaign_id,
    )

    for candidate in candidates:
        await _emit_validation_event(
            db=db,
            validation_run_ids=validation_run_ids,
            event_type="CANDIDATE_GENERATED",
            severity="purple",
            title="Candidate Generated",
            description=f"Generated research candidate {candidate.strategy_name}.",
            metadata={
                "campaign_id": str(campaign.campaign_id),
                "candidate_id": str(candidate.candidate_id),
                "strategy_name": candidate.strategy_name,
            },
        )

    for evaluation in evaluations:
        await _emit_validation_event(
            db=db,
            validation_run_ids=validation_run_ids,
            event_type="CANDIDATE_EVALUATED",
            severity="purple",
            title="Candidate Evaluated",
            description="Deterministic candidate evaluation completed.",
            metadata={
                "campaign_id": str(campaign.campaign_id),
                "candidate_id": str(evaluation.candidate_id),
                "decision_quality_score": evaluation.decision_quality_score,
                "tournament_rank": evaluation.tournament_rank,
            },
        )

    persisted_candidates = await repository.list_candidates(db=db, limit=5000, offset=0)
    evolution_run = get_evolution_engine().evolve(
        memory_candidates=persisted_candidates,
        parent_candidate_id=None,
        generation_limit=settings.research_max_descendants_per_candidate,
    )
    descendants = [
        item for item in evolution_run.descendants if item.generation <= settings.research_max_generation
    ]
    await repository.record_evolved_candidates(
        db=db,
        descendants=descendants,
        campaign_id=campaign.campaign_id,
    )

    for descendant in descendants:
        await _emit_validation_event(
            db=db,
            validation_run_ids=validation_run_ids,
            event_type="EVOLUTION_DESCENDANT_CREATED",
            severity="purple",
            title="Evolution Descendant Created",
            description=f"Created descendant {descendant.strategy_name}.",
            metadata={
                "campaign_id": str(campaign.campaign_id),
                "candidate_id": str(descendant.candidate_id),
                "parent_candidate_id": str(descendant.parent_candidate_id) if descendant.parent_candidate_id else None,
                "generation": descendant.generation,
            },
        )

    refreshed_candidates = await repository.list_candidates(db=db, limit=5000, offset=0)
    analytics_summary = EvolutionAnalyticsService(
        runs_provider=lambda: tuple(),
        candidates_provider=lambda: tuple(refreshed_candidates),
    ).build_summary()

    await _emit_validation_event(
        db=db,
        validation_run_ids=validation_run_ids,
        event_type="TOURNAMENT_COMPLETED",
        severity="purple",
        title="Tournament Completed",
        description="Deterministic tournament ranking computed for current research evidence.",
        metadata={
            "campaign_id": str(campaign.campaign_id),
            "best_quality_score": analytics_summary.best_quality_score,
            "top_research_agent": analytics_summary.top_research_agent,
        },
    )

    best_candidate_id = analytics_summary.best_candidate.candidate_id if analytics_summary.best_candidate is not None else None
    best_quality_score = analytics_summary.best_quality_score
    champion = await _resolve_champion(
        db=db,
        best_candidate_id=best_candidate_id,
    )
    updated_campaign = await repository.upsert_campaign_statistics(
        db=db,
        campaign_id=campaign.campaign_id,
        laboratory_runs_increment=1,
        candidates_generated_increment=run.generated_candidates + len(descendants),
        candidates_evaluated_increment=run.evaluated_candidates + len([item for item in descendants if item.quality_score is not None]),
        best_candidate_id=best_candidate_id,
        best_quality_score=best_quality_score,
        current_champion=champion,
        status="COMPLETED",
        participating_agents=run.participating_agents,
    )

    if champion is not None:
        await _emit_validation_event(
            db=db,
            validation_run_ids=validation_run_ids,
            event_type="CHAMPION_SELECTED",
            severity="purple",
            title="Champion Selected",
            description=f"Champion selected: {champion}.",
            metadata={"campaign_id": str(campaign.campaign_id), "champion": champion},
        )

    await _emit_validation_event(
        db=db,
        validation_run_ids=validation_run_ids,
        event_type="RESEARCH_MEMORY_UPDATED",
        severity="purple",
        title="Research Memory Updated",
        description="Research memory updated from deterministic cycle outputs.",
        metadata={
            "campaign_id": str(campaign.campaign_id),
            "candidates_generated": updated_campaign.candidates_generated,
            "candidates_evaluated": updated_campaign.candidates_evaluated,
            "descendants_generated": len(descendants),
            "champion": champion,
        },
    )

    return ResearchCycleResult(
        started=True,
        campaign_id=campaign.campaign_id,
        candidates_generated=run.generated_candidates,
        candidates_evaluated=run.evaluated_candidates,
        descendants_generated=len(descendants),
        champion=champion,
        reason=None,
    )


async def _load_active_validation_run_ids(*, db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.execute(
        select(ValidationRun.validation_run_id)
        .where(ValidationRun.status == "RUNNING")
        .order_by(ValidationRun.started_at.asc(), ValidationRun.validation_run_id.asc())
    )
    return list(rows.scalars().all())


async def _emit_validation_event(
    *,
    db: AsyncSession,
    validation_run_ids: list[uuid.UUID],
    event_type: str,
    severity: str,
    title: str,
    description: str,
    metadata: dict[str, object],
) -> None:
    for validation_run_id in validation_run_ids:
        db.add(
            ValidationRunEvent(
                validation_run_id=validation_run_id,
                event_type=event_type,
                message=description,
                payload={
                    "severity": severity,
                    "title": title,
                    "description": description,
                    "metadata": metadata,
                },
            )
        )


async def _resolve_champion(*, db: AsyncSession, best_candidate_id: uuid.UUID | None) -> str | None:
    settings = get_settings()
    decision_count = int(
        await db.scalar(select(func.count()).select_from(DecisionRecord)) or 0
    )
    actionable_signal_count = int(
        await db.scalar(
            select(func.count()).select_from(Signal).where(Signal.action.in_(["buy", "sell"]))
        )
        or 0
    )
    trade_count = int(
        await db.scalar(select(func.count()).select_from(Trade).where(Trade.is_paper.is_(True))) or 0
    )

    if decision_count < settings.research_min_decisions:
        return None
    if actionable_signal_count < settings.research_min_actionable_signals:
        return None
    if trade_count < settings.research_min_trades:
        return None

    if best_candidate_id is None:
        return None

    candidate = await db.scalar(
        select(ResearchCandidate).where(ResearchCandidate.candidate_id == best_candidate_id).limit(1)
    )
    if candidate is None:
        return None
    return candidate.strategy_name
