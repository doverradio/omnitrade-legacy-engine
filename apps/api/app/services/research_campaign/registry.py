from __future__ import annotations

from app.services.research_campaign.service import ResearchCampaignEngine


_RESEARCH_CAMPAIGN_ENGINE = ResearchCampaignEngine()


def get_research_campaign_engine() -> ResearchCampaignEngine:
    return _RESEARCH_CAMPAIGN_ENGINE
