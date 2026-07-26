from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class AssetCommissioningPreviewRequest(BaseModel):
    provider: str
    product_id: str
    campaign_id: uuid.UUID
    environment: str


class AssetCommissioningPreviewResponse(BaseModel):
    provider: str
    canonical_product_id: str
    provider_symbol: str
    provider_supported: bool
    asset_registered: bool
    asset_id: uuid.UUID | None
    candle_count: int
    candle_count_required: int
    market_data_current: bool
    campaign_mutation_required: bool
    mandate_successor_required: bool
    preserved_risk_constraints: dict[str, Any]
    runtime_discovery_mutation_required: bool
    expected_changes: list[str]
    blockers: list[str]
    plan: list[str]


class AssetCommissioningRequest(BaseModel):
    provider: str
    product_id: str
    campaign_id: uuid.UUID
    environment: str
    activate: bool
    idempotency_key: str


class AssetCommissioningStageEvidence(BaseModel):
    status: str
    evidence: dict[str, Any] = {}
    completed_at: datetime | None = None
    error: str | None = None


class AssetCommissioningResponse(BaseModel):
    commissioning_id: uuid.UUID
    provider: str
    product_id: str
    campaign_id: uuid.UUID
    environment: str
    status: str
    stages: dict[str, AssetCommissioningStageEvidence]
    asset_id: uuid.UUID | None
    mandate_version_id: uuid.UUID | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class AssetReadinessResponse(BaseModel):
    product_id: str
    provider_supported: bool
    asset_registered: bool
    market_data_current: bool
    candle_count: int
    campaign_authorized: bool
    mandate_authorized: bool
    runtime_selected: bool
    strategy_evaluation_observed: bool
    live_execution_eligible: bool
    blockers: list[str]
    warnings: list[str]
    overall_status: str
