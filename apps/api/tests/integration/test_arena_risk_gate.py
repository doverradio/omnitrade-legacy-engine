from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_risk_gate_decision import ArenaRiskGateDecision
from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent
from app.services.arena.contracts import ArenaRiskContextContract, ArenaRiskEvaluationRequest
from app.services.arena.risk_gate import evaluate_arena_candidate_action


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.proposals: list[ArenaCycleProposal] = []
        self.decisions: list[ArenaRiskGateDecision] = []
        self.risk_events: list[RiskEvent] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_cycle_proposals" in sql:
            values = {value for value in params.values() if isinstance(value, uuid.UUID)}
            for item in self.proposals:
                if {
                    item.id,
                    item.cycle_id,
                    item.competition_id,
                    item.tournament_id,
                    item.agent_id,
                } <= values:
                    return item
            return None

        if "FROM arena_risk_gate_decisions" in sql:
            key = next((value for value in params.values() if isinstance(value, str)), None)
            for item in self.decisions:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaRiskGateDecision):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.decisions.append(obj)
            return
        if isinstance(obj, RiskEvent):
            self.risk_events.append(obj)
            return
        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


def _risk_context() -> ArenaRiskContextContract:
    return ArenaRiskContextContract(
        account_equity=Decimal("1000"),
        start_of_day_equity=Decimal("1000"),
        current_equity=Decimal("1000"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.10"),
        high_water_mark_equity=Decimal("1000"),
        max_drawdown_pct=Decimal("0.20"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("60"),
        evaluation_time=datetime(2026, 7, 6, tzinfo=timezone.utc),
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
    )


@pytest.mark.asyncio
async def test_every_arena_proposal_is_risk_evaluated_and_recorded() -> None:
    session = _FakeSession()
    cycle_id = uuid.uuid4()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()

    proposals = [
        ArenaCycleProposal(
            id=uuid.uuid4(),
            idempotency_key=f"proposal-{i}",
            cycle_id=cycle_id,
            competition_id=competition_id,
            tournament_id=tournament_id,
            agent_id=uuid.uuid4(),
            proposal_action="buy",
            proposal_payload={"quantity": "2"},
            provenance={"source": "integration-test"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    session.proposals.extend(proposals)

    outcomes = []
    for proposal in proposals:
        outcomes.append(
            await evaluate_arena_candidate_action(
                db=session,
                request=ArenaRiskEvaluationRequest(
                    cycle_id=proposal.cycle_id,
                    proposal_id=proposal.id,
                    competition_id=proposal.competition_id,
                    tournament_id=proposal.tournament_id,
                    agent_id=proposal.agent_id,
                    action=proposal.proposal_action,
                    symbol="BTCUSDT",
                    requested_quantity=Decimal("2"),
                    reference_price=Decimal("100"),
                    min_order_notional=Decimal("1"),
                    qty_step_size=Decimal("0.01"),
                    supports_fractional=True,
                    stop_loss_computable=True,
                    provenance={"ticket": "ARENA-85"},
                    actor="arena.integration",
                    risk_context=_risk_context(),
                ),
            )
        )

    assert len(outcomes) == 3
    assert len(session.decisions) == 3
    assert len(session.risk_events) == 3
    assert len(session.audit_logs) == 3
    assert all(item.action == "resize" for item in outcomes)
    assert all(item.approved_quantity == Decimal("1.00") for item in outcomes)


@pytest.mark.asyncio
async def test_rejected_proposals_are_captured_without_trade_execution() -> None:
    session = _FakeSession()
    proposal = ArenaCycleProposal(
        id=uuid.uuid4(),
        idempotency_key="proposal-reject",
        cycle_id=uuid.uuid4(),
        competition_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        proposal_action="buy",
        proposal_payload={"quantity": "1"},
        provenance={"source": "integration-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )
    session.proposals.append(proposal)

    context = _risk_context()
    reject_context = ArenaRiskContextContract(
        **{**context.__dict__, "global_kill_switch_engaged_state": True}
    )

    outcome = await evaluate_arena_candidate_action(
        db=session,
        request=ArenaRiskEvaluationRequest(
            cycle_id=proposal.cycle_id,
            proposal_id=proposal.id,
            competition_id=proposal.competition_id,
            tournament_id=proposal.tournament_id,
            agent_id=proposal.agent_id,
            action=proposal.proposal_action,
            symbol="BTCUSDT",
            requested_quantity=Decimal("1"),
            reference_price=Decimal("100"),
            min_order_notional=Decimal("1"),
            qty_step_size=Decimal("0.01"),
            supports_fractional=True,
            stop_loss_computable=True,
            provenance={"ticket": "ARENA-85"},
            actor="arena.integration",
            risk_context=reject_context,
        ),
    )

    assert outcome.action == "reject"
    assert outcome.approved_quantity == Decimal("0")
    assert outcome.reason_code == "global_kill_switch_engaged"
    assert outcome.persisted_risk_event_reason_code == "global_kill_switch_engaged"
    assert len(session.decisions) == 1
    assert len(session.risk_events) == 1
