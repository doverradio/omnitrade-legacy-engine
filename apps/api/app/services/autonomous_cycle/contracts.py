from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict
import uuid


CYCLE_STATES = (
    "NOT_STARTED",
    "LOADING",
    "READY",
    "EVALUATING",
    "HOLD",
    "PREVIEW_READY",
    "FAILED",
    "COMPLETE",
)

ACTIONS = ("BUY", "SELL", "HOLD")


@dataclass(frozen=True)
class AutonomousCycleRequest:
    mandate_id: uuid.UUID
    actor: str
    product_id: str = "BTC-USD"
    strategy_interval: str = "15m"
    trigger: str = "manual"
    idempotency_seed: str | None = None
    software_build_version: str | None = None
    forced_action: str | None = None


@dataclass(frozen=True)
class ReconciliationStatus:
    provider_ready: bool
    balances_loaded: bool
    unresolved_order_count: int
    open_position_count: int
    stale_evidence: bool
    explanation: tuple[str, ...] = ()


class StrategySignalPayload(TypedDict, total=False):
    action: str
    strength: str | float | Decimal
    reason: str
    indicators: dict[str, Any]
    timestamp: str
    timeline: dict[str, Any]
    market_window: dict[str, Any]


@dataclass(frozen=True)
class StrategyProposal:
    action: str
    strategy_name: str
    strategy_version: str
    deterministic_explanation: tuple[str, ...]
    signal_payload: StrategySignalPayload | None = None


@dataclass(frozen=True)
class RiskEvaluationSummary:
    risk_verdict: str
    risk_event_id: uuid.UUID | None
    reason_code: str | None
    approved_quantity: Decimal | None


@dataclass(frozen=True)
class CycleDiagnostics:
    duration_ms: int
    evaluation_stage: str | None
    termination_stage: str
    failure_reason: str | None
    deterministic_explanation: tuple[str, ...]


@dataclass(frozen=True)
class AutonomousCycleResult:
    cycle_id: uuid.UUID
    state: str
    idempotency_key: str
    mandate_id: uuid.UUID
    mandate_version_id: uuid.UUID | None
    proposed_action: str
    mandate_verdict: str
    risk_verdict: str
    decision_record_id: uuid.UUID | None
    preview_id: uuid.UUID | None
    mandate_evaluation_id: uuid.UUID | None
    risk_event_id: uuid.UUID | None
    audit_correlation_id: uuid.UUID
    diagnostics: CycleDiagnostics
    replayed: bool
    cycle_context: dict[str, object] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
