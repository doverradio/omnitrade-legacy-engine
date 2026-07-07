from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

LIVE_TRADING_LIFECYCLE_STATES = {
    "paper_default",
    "live_pending_governance",
    "live_governance_approved",
    "live_enabled",
    "live_suspended",
}

LIVE_TRADING_OPERATING_MODES = {"paper", "live"}

LIVE_TRADING_APPROVAL_STATES = {
    "not_requested",
    "pending",
    "approved",
    "rejected",
    "revoked",
}

# Prompt 9.1 boundary: paper is the default mode, live is opt-in only.
LIVE_TRADING_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "paper_default": {"live_pending_governance"},
    "live_pending_governance": {"paper_default", "live_governance_approved"},
    "live_governance_approved": {"paper_default", "live_enabled"},
    "live_enabled": {"paper_default", "live_suspended"},
    "live_suspended": {"paper_default", "live_enabled"},
}


@dataclass(frozen=True)
class LiveTradingProvenanceContract:
    actor_id: str
    actor_type: str
    source: str
    request_id: str
    observed_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LiveTradingProfileContract:
    live_trading_profile_id: uuid.UUID
    paper_account_id: uuid.UUID
    operating_mode: str
    lifecycle_state: str
    approval_state: str
    live_opt_in: bool
    paper_default_mode: bool
    governance_approved: bool
    risk_authority_model: str
    autonomous_capital_allocation: bool
    autonomous_strategy_evolution: bool
    automatic_promotion_enabled: bool
    provenance_metadata: dict[str, Any]


@dataclass(frozen=True)
class LiveTradingStateTransitionContract:
    live_trading_profile_id: uuid.UUID
    from_state: str
    to_state: str
    transition_reason: str
    requested_at: datetime


@dataclass(frozen=True)
class LiveTradingImmutableEventContract:
    idempotency_key: str
    event_hash: str
    immutable_contract_version: str
    live_trading_profile_id: uuid.UUID
    sequence_number: int
    event_type: str
    from_state: str | None
    to_state: str
    operating_mode: str
    paper_default_mode: bool
    live_opt_in: bool
    governance_approved: bool
    risk_authority_model: str
    event_payload: dict[str, Any]
    provenance: LiveTradingProvenanceContract
    recorded_at: datetime


def is_valid_live_trading_transition(from_state: str, to_state: str) -> bool:
    allowed_targets = LIVE_TRADING_ALLOWED_TRANSITIONS.get(from_state)
    if not allowed_targets:
        return False
    return to_state in allowed_targets
