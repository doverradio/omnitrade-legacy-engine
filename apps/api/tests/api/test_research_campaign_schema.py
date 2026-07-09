from __future__ import annotations

from app.schemas.research_campaign import ResearchCampaignCreateRequest


def test_research_campaign_create_request_serialization() -> None:
    payload = ResearchCampaignCreateRequest(
        name="Long Horizon Campaign",
        objective="Coordinate deterministic research campaigns.",
    )

    data = payload.model_dump(mode="json")
    assert data["name"] == "Long Horizon Campaign"
    assert data["objective"] == "Coordinate deterministic research campaigns."
