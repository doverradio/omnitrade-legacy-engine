from __future__ import annotations

from app.services.evolution.registry import get_evolution_engine
from app.services.research_campaign.service import ResearchCampaignEngine
from app.services.research_memory.registry import get_research_memory


def test_create_campaign() -> None:
    engine = ResearchCampaignEngine()

    campaign = engine.create_campaign(
        name="Momentum Exploration",
        objective="Run repeated deterministic laboratory cycles.",
    )

    assert campaign.name == "Momentum Exploration"
    assert campaign.objective == "Run repeated deterministic laboratory cycles."
    assert campaign.status == "IDLE"
    assert campaign.laboratory_runs == 0
    assert campaign.best_candidate is None


def test_run_campaign_updates_statistics() -> None:
    memory = get_research_memory()
    evolution = get_evolution_engine()
    memory.clear()
    evolution.clear()

    engine = ResearchCampaignEngine()
    campaign = engine.create_campaign(
        name="Campaign A",
        objective="Deterministic campaign orchestration.",
    )

    updated = engine.run_campaign(campaign_id=campaign.campaign_id)

    assert updated.status == "COMPLETED"
    assert updated.started_at is not None
    assert updated.completed_at is not None
    assert updated.laboratory_runs == 1
    assert updated.candidates_generated > 0
    assert updated.candidates_evaluated > 0
    assert updated.best_candidate is not None
    assert updated.best_quality_score is not None
    assert updated.current_champion is not None

    memory.clear()
    evolution.clear()


def test_run_campaign_multiple_times_accumulates() -> None:
    memory = get_research_memory()
    evolution = get_evolution_engine()
    memory.clear()
    evolution.clear()

    engine = ResearchCampaignEngine()
    campaign = engine.create_campaign(
        name="Campaign B",
        objective="Repeat campaign runs.",
    )

    first = engine.run_campaign(campaign_id=campaign.campaign_id)
    second = engine.run_campaign(campaign_id=campaign.campaign_id)

    assert first.laboratory_runs == 1
    assert second.laboratory_runs == 2
    assert second.candidates_generated >= first.candidates_generated
    assert second.candidates_evaluated >= first.candidates_evaluated

    memory.clear()
    evolution.clear()


def test_campaign_statistics_include_best_quality_score() -> None:
    memory = get_research_memory()
    evolution = get_evolution_engine()
    memory.clear()
    evolution.clear()

    engine = ResearchCampaignEngine()
    campaign = engine.create_campaign(
        name="Campaign C",
        objective="Track best quality score.",
    )

    updated = engine.run_campaign(campaign_id=campaign.campaign_id)
    assert updated.best_quality_score is not None
    assert updated.best_quality_score >= 0

    memory.clear()
    evolution.clear()
