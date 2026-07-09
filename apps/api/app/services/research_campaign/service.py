from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from app.services.capital_allocation.deterministic import build_capital_allocation_recommendation_v1
from app.services.capital_allocation.interface import CapitalAllocationInput
from app.services.evolution.registry import get_evolution_engine
from app.services.research_agents.registry import list_generated_strategy_candidates
from app.services.research_campaign.interface import ResearchCampaign
from app.services.research_laboratory.registry import get_research_laboratory
from app.services.research_memory.registry import get_research_memory
from app.services.tournament.deterministic import build_tournament_snapshot_v1
from app.services.tournament.interface import TournamentStrategyEvidence


class CampaignNotFoundError(LookupError):
    pass


class ResearchCampaignEngine:
    def __init__(self) -> None:
        self._campaigns: list[ResearchCampaign] = []

    def list_campaigns(self) -> tuple[ResearchCampaign, ...]:
        return tuple(reversed(self._campaigns))

    def get_campaign(self, *, campaign_id: uuid.UUID) -> ResearchCampaign:
        for campaign in self._campaigns:
            if campaign.campaign_id == campaign_id:
                return campaign
        raise CampaignNotFoundError(str(campaign_id))

    def create_campaign(self, *, name: str, objective: str) -> ResearchCampaign:
        agents = get_research_laboratory().get_status().registered_agents
        campaign = ResearchCampaign(
            campaign_id=uuid.uuid4(),
            name=name,
            objective=objective,
            status="IDLE",
            started_at=None,
            completed_at=None,
            participating_agents=tuple(agents),
            laboratory_runs=0,
            candidates_generated=0,
            candidates_evaluated=0,
            best_candidate=None,
            best_quality_score=None,
            current_champion=None,
        )
        self._campaigns.append(campaign)
        return campaign

    def run_campaign(self, *, campaign_id: uuid.UUID) -> ResearchCampaign:
        campaign = self.get_campaign(campaign_id=campaign_id)
        started_at = campaign.started_at or datetime.now(timezone.utc)
        running = replace(
            campaign,
            status="RUNNING",
            started_at=started_at,
        )
        self._replace_campaign(running)

        laboratory = get_research_laboratory()
        memory = get_research_memory()

        run = laboratory.run()
        run_candidates = [
            item
            for item in memory.list_candidates()
            if item.laboratory_run_id == run.laboratory_run_id and item.parent_candidate_id is None
        ]

        evolved_descendants = []
        if run_candidates:
            parent = max(
                run_candidates,
                key=lambda item: (
                    int(item.quality_score or 0),
                    -(item.tournament_rank or 999999),
                    str(item.candidate_id),
                ),
            )
            evolution_run = get_evolution_engine().evolve(
                memory_candidates=memory.list_candidates(),
                parent_candidate_id=parent.candidate_id,
                generation_limit=None,
            )
            evolved_descendants = list(evolution_run.descendants)
            memory.record_evolved_candidates(descendants=evolved_descendants)

        generated_candidates = run.generated_candidates + len(evolved_descendants)
        evaluated_candidates = run.evaluated_candidates + len(
            [item for item in evolved_descendants if item.quality_score is not None]
        )

        strategy_name_by_candidate_id = {
            item.candidate_id: item.strategy_name
            for item in list_generated_strategy_candidates()
        }
        strategy_name_by_candidate_id.update(
            {item.candidate_id: item.strategy_name for item in evolved_descendants}
        )

        tournament_input: list[TournamentStrategyEvidence] = []
        quality_scores_by_strategy: dict[str, int] = {}
        best_run_candidate: str | None = None
        best_run_quality: int | None = None

        for item in memory.list_candidates():
            if item.laboratory_run_id != run.laboratory_run_id:
                continue
            if item.quality_score is None:
                continue
            strategy_name = strategy_name_by_candidate_id.get(item.candidate_id, str(item.candidate_id))
            quality_scores_by_strategy[strategy_name] = item.quality_score
            if best_run_quality is None or item.quality_score > best_run_quality:
                best_run_quality = item.quality_score
                best_run_candidate = strategy_name
            tournament_input.append(
                TournamentStrategyEvidence(
                    strategy_name=strategy_name,
                    quality_score=item.quality_score,
                    replay_variance=Decimal("0"),
                    replay_count=1,
                    paper_trades=0,
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    win_rate=None,
                )
            )

        snapshot = build_tournament_snapshot_v1(strategies=tournament_input)
        champion = snapshot.ranking[0].strategy_name if snapshot.ranking else None

        build_capital_allocation_recommendation_v1(
            tournament_ranking=[
                CapitalAllocationInput(strategy_name=item.strategy_name, overall_rank=item.overall_rank)
                for item in snapshot.ranking
            ],
            highest_quality_strategy=best_run_candidate,
            quality_scores_by_strategy=quality_scores_by_strategy,
            total_paper_capital=Decimal("100000"),
        )

        best_quality_score = campaign.best_quality_score
        best_candidate = campaign.best_candidate
        if best_run_quality is not None and (best_quality_score is None or best_run_quality > best_quality_score):
            best_quality_score = best_run_quality
            best_candidate = best_run_candidate

        completed = replace(
            running,
            status="COMPLETED",
            completed_at=datetime.now(timezone.utc),
            participating_agents=tuple(sorted(set(running.participating_agents).union(run.participating_agents))),
            laboratory_runs=running.laboratory_runs + 1,
            candidates_generated=running.candidates_generated + generated_candidates,
            candidates_evaluated=running.candidates_evaluated + evaluated_candidates,
            best_candidate=best_candidate,
            best_quality_score=best_quality_score,
            current_champion=champion,
        )
        self._replace_campaign(completed)
        return completed

    def clear(self) -> None:
        self._campaigns.clear()

    def _replace_campaign(self, campaign: ResearchCampaign) -> None:
        self._campaigns = [
            campaign if item.campaign_id == campaign.campaign_id else item
            for item in self._campaigns
        ]
