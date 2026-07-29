from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ControlledProofCreateRequest(BaseModel):
    """Thin live-start contract; every execution scope remains server-owned."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(validation_alias=AliasChoices("product", "product_id"))
    notional_usd: Decimal = Decimal("5.00")
    idempotency_key: str
    expires_in_minutes: int = Field(default=60, ge=1, le=180)


class ControlledProofStartResponse(BaseModel):
    proof_id: uuid.UUID
    status: str
    product: str
    notional_usd: Decimal
    live_execution: bool
    new_proof_created: bool
    idempotent_replay: bool
    reused_historical_execution: bool
    audit_correlation_id: uuid.UUID


class ControlledProofCancelRequest(BaseModel):
    reason: str | None = None


class ControlledProofMandateReadinessResponse(BaseModel):
    configured: bool
    mandate_id: uuid.UUID | None
    mandate_found: bool
    purpose: str | None
    status: str | None
    autonomy_level: str | None
    provider: str | None
    environment: str | None
    exchange_connection_id: uuid.UUID | None
    live_trading_profile_id: uuid.UUID | None
    paper_account_id: uuid.UUID | None
    capital_campaign_id: int | None
    governing_version_id: uuid.UUID | None
    governing_version_found: bool
    max_order_notional_usd: Decimal | None
    max_open_exposure_usd: Decimal | None
    position_limit: int | None
    allowed_products: list[str] | None
    allowed_order_sides: list[str] | None
    ready: bool
    blockers: list[str]


class ControlledProofExitRecoveryCreateRequest(BaseModel):
    idempotency_key: str
    expires_in_minutes: int = Field(default=60, ge=1, le=180)


class ControlledProofRecoveredOutcome(BaseModel):
    status: str
    original_recovery_id: uuid.UUID
    proof_id: uuid.UUID
    sell_package_id: uuid.UUID
    sell_live_crypto_order_id: uuid.UUID
    provider_order_id: str
    execution_claim_id: uuid.UUID
    reconciliation_event_id: uuid.UUID
    recovered_terminal_verdict: str
    recovered_net_pnl_usd: Decimal
    completed_at: datetime
    audit_correlation_id: uuid.UUID


class ControlledProofExitRecoveryResponse(BaseModel):
    recovery_id: uuid.UUID
    proof_id: uuid.UUID
    status: str
    idempotency_key: str
    authorized_by: str
    authorized_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    blocked_reason: str | None
    failure_reason: str | None
    audit_correlation_id: uuid.UUID
    recovered_outcome: ControlledProofRecoveredOutcome | None = None


class ControlledProofResponse(BaseModel):
    proof_id: uuid.UUID
    status: str
    provider: str
    environment: str
    campaign_id: uuid.UUID
    campaign_version: int
    product_id: str
    max_notional_usd: Decimal
    requested_by: str
    requested_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    blocked_reason: str | None
    failure_reason: str | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    audit_correlation_id: uuid.UUID

    decision: dict[str, Any] | None
    mandate: dict[str, Any] | None
    package: dict[str, Any] | None
    buy_order: dict[str, Any] | None
    position: dict[str, Any] | None
    sell_order: dict[str, Any] | None
    reconciliation: dict[str, Any] | None
    fees_usd: Decimal | None
    net_pnl_usd: Decimal | None
    terminal_verdict: str | None
