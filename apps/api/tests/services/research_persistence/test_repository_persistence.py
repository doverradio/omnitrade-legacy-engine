from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolvedCandidate, EvolutionMutation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_persistence.repository import ResearchPersistenceRepository


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.candidates: dict[uuid.UUID, object] = {}
        self.lineage: dict[uuid.UUID, object] = {}
        self.campaigns: dict[uuid.UUID, object] = {}
        self.stats: dict[uuid.UUID, object] = {}

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            campaign_id = getattr(obj, "campaign_id", None)
            if campaign_id is None and obj.__class__.__name__ == "ResearchCampaign":
                setattr(obj, "campaign_id", uuid.uuid4())

            run_id = getattr(obj, "run_id", None)
            if run_id is None and obj.__class__.__name__ == "ResearchLaboratoryRun":
                setattr(obj, "run_id", uuid.uuid4())

            candidate_id = getattr(obj, "candidate_id", None)
            if candidate_id is not None and obj.__class__.__name__ == "ResearchCandidate":
                self.candidates[candidate_id] = obj

            if candidate_id is not None and obj.__class__.__name__ == "ResearchCandidateLineage":
                self.lineage[candidate_id] = obj

            if obj.__class__.__name__ == "ResearchCampaign":
                self.campaigns[obj.campaign_id] = obj

            if obj.__class__.__name__ == "ResearchCampaignStatistic":
                self.stats[obj.campaign_id] = obj

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM research_candidates" in sql:
            for candidate in self.candidates.values():
                if str(candidate.candidate_id) in sql:
                    return candidate
            return None
        if "FROM research_candidate_lineage" in sql:
            for lineage in self.lineage.values():
                if str(lineage.candidate_id) in sql:
                    return lineage
            return None
        if "FROM research_campaigns" in sql:
            for campaign in self.campaigns.values():
                if str(campaign.campaign_id) in sql:
                    return campaign
            return None
        if "FROM research_campaign_statistics" in sql:
            for stats in self.stats.values():
                if str(stats.campaign_id) in sql:
                    return stats
            return None
        return None


def _run() -> ResearchLaboratoryRun:
    return ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )


def _candidate(candidate_id: uuid.UUID) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=candidate_id,
        generated_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        originating_agent="Baseline Research Agent",
        strategy_name="Deterministic Candidate",
        description="deterministic",
        parameter_set={"fast_period": 9, "slow_period": 30},
        rationale="deterministic",
        status="PROPOSED",
    )


def _evaluation(candidate_id: uuid.UUID) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=uuid.uuid4(),
        candidate_id=candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=87,
        ai_coach_summary="summary",
        decision_intelligence_summary="intelligence",
        tournament_rank=1,
        promotion_eligible=False,
    )


@pytest.mark.asyncio
async def test_create_campaign_persists_research_campaign_and_statistics() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    campaign = await repository.create_campaign(
        db=db,
        name="Deterministic Research",
        objective="Bounded paper-only cycle",
        participating_agents=("Baseline Research Agent",),
    )

    tables = {getattr(item, "__tablename__", "") for item in db.added}
    assert "research_campaigns" in tables
    assert "research_campaign_statistics" in tables
    assert campaign.campaign_id is not None


@pytest.mark.asyncio
async def test_record_laboratory_run_persists_candidates_runs_and_memory_entries() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    candidate_id = uuid.uuid4()
    await repository.record_laboratory_run(
        db=db,
        run=_run(),
        candidates=[_candidate(candidate_id)],
        evaluations=[_evaluation(candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    tables = [getattr(item, "__tablename__", "") for item in db.added]
    assert "research_laboratory_runs" in tables
    assert "research_candidates" in tables
    assert "research_memory_entries" in tables


@pytest.mark.asyncio
async def test_record_evolved_candidates_persists_lineage_and_memory_growth() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    parent_id = uuid.uuid4()
    descendant_id = uuid.uuid4()

    db.candidates[parent_id] = type(
        "CandidateRow",
        (),
        {
            "candidate_id": parent_id,
            "laboratory_run_id": uuid.uuid4(),
            "campaign_id": uuid.uuid4(),
            "parent_candidate_id": None,
            "originating_agent": "Baseline Research Agent",
            "strategy_name": "Parent",
            "description": "parent",
            "parameter_set": {"fast_period": 9},
            "rationale": "parent",
            "status": "EVALUATED",
            "generation": 1,
            "mutation_reason": None,
            "parameter_diff": [],
            "generated_at": datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        },
    )()

    descendant = EvolvedCandidate(
        candidate_id=descendant_id,
        parent_candidate_id=parent_id,
        generation=2,
        mutation_reason="rsi_period 14->12",
        parameter_diff=(
            EvolutionMutation(
                parameter_name="rsi_period",
                previous_value=14,
                new_value=12,
            ),
        ),
        parameter_set={"rsi_period": 12},
        strategy_name="Descendant",
        originating_agent="Baseline Research Agent",
        generated_at=datetime(2026, 7, 10, 10, 2, tzinfo=timezone.utc),
        quality_score=92,
        tournament_rank=1,
        status="EVALUATED",
    )

    await repository.record_evolved_candidates(
        db=db,
        descendants=[descendant],
        campaign_id=uuid.uuid4(),
    )

    tables = [getattr(item, "__tablename__", "") for item in db.added]
    assert "research_candidates" in tables
    assert "research_candidate_lineage" in tables
    assert "research_memory_entries" in tables
