from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class ArenaProvenanceContract:
    source_lineage: dict[str, Any]
    field_provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaCompetitionIdentityContract:
    competition_identity: str
    idempotency_key: str
    master_account_id: uuid.UUID
    paper_portfolio_id: uuid.UUID


@dataclass(frozen=True)
class ArenaTournamentIdentityContract:
    tournament_identity: str
    idempotency_key: str
    competition_identity: str
    sequence_number: int


@dataclass(frozen=True)
class ArenaCycleIdentityContract:
    cycle_identity: str
    idempotency_key: str
    tournament_identity: str
    cycle_number: int


@dataclass(frozen=True)
class ArenaAgentIdentityContract:
    agent_identity: str
    idempotency_key: str
    competition_identity: str
    strategy_id: str
    strategy_version: str


@dataclass(frozen=True)
class ArenaLifecycleWriteRequest:
    status: str
    config: dict[str, Any]
    provenance: ArenaProvenanceContract
    requested_at: datetime


@dataclass(frozen=True)
class ArenaAgentVersionIdentityContract:
    agent_id: uuid.UUID
    version_id: uuid.UUID
    semantic_version: str
    created_at: datetime
    provenance_metadata: dict[str, Any]
    registration_source: str
    registration_hash: str


@dataclass(frozen=True)
class ArenaAgentRegistrationRequest:
    competition_id: uuid.UUID
    strategy_id: str
    strategy_version: str
    semantic_version: str
    registration_source: str
    requested_by: str
    provenance_metadata: dict[str, Any]
    paper_only_eligible: bool
    live_capital_eligible: bool
    human_governed: bool
    autonomous_self_modifying: bool
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ArenaEligibilityResult:
    eligible: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class ArenaAgentRegistrationResult:
    accepted: bool
    identity: ArenaAgentVersionIdentityContract
    rejection_reason: str | None
    registration_record_id: uuid.UUID
    participating_agent_id: uuid.UUID | None


@dataclass(frozen=True)
class ArenaCycleSnapshotContract:
    market_data: dict[str, Any]
    portfolio_state: dict[str, Any]
    risk_constraints: dict[str, Any]
    cycle_timestamp: datetime


@dataclass(frozen=True)
class ArenaAgentProposalContract:
    agent_id: uuid.UUID
    action: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArenaCycleOrchestrationResult:
    cycle_id: uuid.UUID
    competition_id: uuid.UUID
    tournament_id: uuid.UUID
    deterministic_snapshot_hash: str
    participating_agent_ids: list[uuid.UUID]
    provenance_metadata: dict[str, Any]
    proposals_captured: int


@dataclass(frozen=True)
class ArenaAgentBudgetAssignmentContract:
    agent_id: uuid.UUID
    assigned_budget: Decimal


@dataclass(frozen=True)
class ArenaCompetitionAllocationRequest:
    competition_id: uuid.UUID
    idempotency_key: str
    competition_budget: Decimal
    assignments: list[ArenaAgentBudgetAssignmentContract]
    provenance: dict[str, Any]
    requested_by: str


@dataclass(frozen=True)
class ArenaCompetitionAllocationResult:
    competition_budget_allocation_id: uuid.UUID
    competition_id: uuid.UUID
    paper_portfolio_id: uuid.UUID
    master_account_id: uuid.UUID
    competition_budget: Decimal
    total_assigned_budget: Decimal
    assignment_count: int
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaRiskContextContract:
    account_equity: Decimal
    start_of_day_equity: Decimal
    current_equity: Decimal
    max_position_size_pct: Decimal
    max_daily_loss_pct: Decimal
    high_water_mark_equity: Decimal
    max_drawdown_pct: Decimal
    consecutive_losses_on_pair: int
    cooldown_after_losses: int
    last_loss_at: datetime | None
    cooldown_duration_minutes: Decimal
    evaluation_time: datetime
    data_is_stale: bool
    data_has_gaps: bool
    global_kill_switch_engaged_state: bool | None
    global_kill_switch_rearm_required: bool | None
    account_kill_switch_engaged_state: bool | None
    account_kill_switch_rearm_required: bool | None
    global_kill_switch_state_observed: bool
    account_kill_switch_state_observed: bool


@dataclass(frozen=True)
class ArenaRiskEvaluationRequest:
    cycle_id: uuid.UUID
    proposal_id: uuid.UUID
    competition_id: uuid.UUID
    tournament_id: uuid.UUID
    agent_id: uuid.UUID
    action: str
    symbol: str
    requested_quantity: Decimal
    reference_price: Decimal
    min_order_notional: Decimal
    qty_step_size: Decimal
    supports_fractional: bool
    stop_loss_computable: bool
    provenance: dict[str, Any]
    actor: str
    risk_context: ArenaRiskContextContract


@dataclass(frozen=True)
class ArenaRiskEvaluationResult:
    risk_gate_decision_id: uuid.UUID
    cycle_id: uuid.UUID
    proposal_id: uuid.UUID
    competition_id: uuid.UUID
    tournament_id: uuid.UUID
    agent_id: uuid.UUID
    action: str
    approved_quantity: Decimal
    reason_code: str | None
    persisted_risk_event_type: str
    persisted_risk_event_action: str
    persisted_risk_event_reason_code: str | None
    provenance: dict[str, Any]
    decision_steps: list[dict[str, Any]]


@dataclass(frozen=True)
class ArenaMetricValueContract:
    value: Decimal | None
    status: str
    reason: str | None


@dataclass(frozen=True)
class ArenaAgentPerformanceSummaryContract:
    agent_id: uuid.UUID
    profit: ArenaMetricValueContract
    drawdown: ArenaMetricValueContract
    fee_drag: ArenaMetricValueContract
    consistency: ArenaMetricValueContract
    risk_discipline: ArenaMetricValueContract
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaPortfolioPerformanceContract:
    competition_id: uuid.UUID
    tournament_id: uuid.UUID | None
    cycle_id: uuid.UUID | None
    profit: ArenaMetricValueContract
    drawdown: ArenaMetricValueContract
    fee_drag: ArenaMetricValueContract
    consistency: ArenaMetricValueContract
    risk_discipline: ArenaMetricValueContract
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaPerformanceSnapshotRequest:
    competition_id: uuid.UUID
    tournament_id: uuid.UUID | None
    cycle_id: uuid.UUID | None
    as_of: datetime
    actor: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ArenaPerformanceSnapshotResult:
    snapshot_id: uuid.UUID
    competition_id: uuid.UUID
    tournament_id: uuid.UUID | None
    cycle_id: uuid.UUID | None
    snapshot_scope: str
    snapshot_input_hash: str
    agent_summaries: list[ArenaAgentPerformanceSummaryContract]
    portfolio: ArenaPortfolioPerformanceContract
    provenance: dict[str, Any]


class ArenaLifecycleServiceContract(Protocol):
    async def ensure_competition(
        self,
        identity: ArenaCompetitionIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_tournament(
        self,
        identity: ArenaTournamentIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_cycle(
        self,
        identity: ArenaCycleIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...

    async def ensure_participating_agent(
        self,
        identity: ArenaAgentIdentityContract,
        request: ArenaLifecycleWriteRequest,
    ) -> uuid.UUID: ...


class ArenaRegistrationServiceContract(Protocol):
    async def register_agent(
        self,
        request: ArenaAgentRegistrationRequest,
    ) -> ArenaAgentRegistrationResult: ...


class ArenaOrchestrationServiceContract(Protocol):
    async def orchestrate_cycle(
        self,
        *,
        competition_id: uuid.UUID,
        tournament_id: uuid.UUID,
        cycle_number: int,
        snapshot: ArenaCycleSnapshotContract,
        proposals: list[ArenaAgentProposalContract],
    ) -> ArenaCycleOrchestrationResult: ...


class ArenaPaperAllocationServiceContract(Protocol):
    async def allocate_competition_budget(
        self,
        request: ArenaCompetitionAllocationRequest,
    ) -> ArenaCompetitionAllocationResult: ...


class ArenaRiskGateServiceContract(Protocol):
    async def evaluate_candidate_action(
        self,
        request: ArenaRiskEvaluationRequest,
    ) -> ArenaRiskEvaluationResult: ...


class ArenaPerformanceTrackingServiceContract(Protocol):
    async def build_performance_snapshot(
        self,
        request: ArenaPerformanceSnapshotRequest,
    ) -> ArenaPerformanceSnapshotResult: ...