from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel


class CapitalAllocationEntryResponse(BaseModel):
    strategy_name: str
    allocation_percent: str
    allocation_amount: str
    rationale: str


class CapitalAllocationRecommendationResponse(BaseModel):
    recommendation_id: uuid.UUID
    generated_at: datetime
    total_paper_capital: str
    allocations: list[CapitalAllocationEntryResponse]
