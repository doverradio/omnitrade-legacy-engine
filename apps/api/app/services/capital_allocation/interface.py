from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid


@dataclass(frozen=True, slots=True)
class CapitalAllocationInput:
    strategy_name: str
    overall_rank: int


@dataclass(frozen=True, slots=True)
class CapitalAllocationEntry:
    strategy_name: str
    allocation_percent: Decimal
    allocation_amount: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class CapitalAllocationRecommendation:
    recommendation_id: uuid.UUID
    generated_at: datetime
    total_paper_capital: Decimal
    allocations: tuple[CapitalAllocationEntry, ...]
