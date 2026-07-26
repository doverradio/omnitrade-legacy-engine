from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ControlledProofCreateRequest(BaseModel):
    """No scope, provider, environment, campaign, or notional fields here --
    those are server-enforced constants for v1 (see controlled_proof.service),
    never caller-supplied, so there is no arbitrary parameter surface for an
    operator (or a compromised/buggy caller) to widen."""

    product_id: str
    idempotency_key: str
    expires_in_minutes: int = Field(default=60, ge=1, le=180)


class ControlledProofCancelRequest(BaseModel):
    reason: str | None = None


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
