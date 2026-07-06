from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.core.errors import InvalidRequestError
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_risk_gate_decision import ArenaRiskGateDecision
from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent
from app.services.arena.contracts import (
    ArenaRiskContextContract,
    ArenaRiskEvaluationRequest,
)
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


def _risk_context(
    *,
    max_position_size_pct: str = "0.20",
    global_kill_switch_engaged_state: bool | None = False,
    account_kill_switch_engaged_state: bool | None = False,
    global_kill_switch_state_observed: bool = True,
    account_kill_switch_state_observed: bool = True,
) -> ArenaRiskContextContract:
    return ArenaRiskContextContract(
        account_equity=Decimal("1000"),
        start_of_day_equity=Decimal("1000"),
        current_equity=Decimal("1000"),
        max_position_size_pct=Decimal(max_position_size_pct),
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
        global_kill_switch_engaged_state=global_kill_switch_engaged_state,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=account_kill_switch_engaged_state,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=global_kill_switch_state_observed,
        account_kill_switch_state_observed=account_kill_switch_state_observed,
    )


def _proposal(
    *,
    cycle_id: uuid.UUID,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> ArenaCycleProposal:
    return ArenaCycleProposal(
        id=uuid.uuid4(),
        idempotency_key=f"proposal-{agent_id}",
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
        proposal_action="buy",
        proposal_payload={"quantity": "2"},
        provenance={"source": "service-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _request(
    *,
    proposal: ArenaCycleProposal,
    quantity: str,
    stop_loss_computable: bool = True,
    max_position_size_pct: str = "0.20",
    global_kill_switch_state_observed: bool = True,
) -> ArenaRiskEvaluationRequest:
    return ArenaRiskEvaluationRequest(
        cycle_id=proposal.cycle_id,
        proposal_id=proposal.id,
        competition_id=proposal.competition_id,
        tournament_id=proposal.tournament_id,
        agent_id=proposal.agent_id,
        action=proposal.proposal_action,
        symbol="BTCUSDT",
        requested_quantity=Decimal(quantity),
        reference_price=Decimal("100"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.01"),
        supports_fractional=True,
        stop_loss_computable=stop_loss_computable,
        provenance={"ticket": "ARENA-85"},
        actor="arena.system",
        risk_context=_risk_context(
            max_position_size_pct=max_position_size_pct,
            global_kill_switch_state_observed=global_kill_switch_state_observed,
        ),
    )


@pytest.mark.asyncio
async def test_approval_is_recorded_without_execution_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    cycle_id = uuid.uuid4()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    proposal = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
    )
    session.proposals.append(proposal)

    execution_calls = {"alpaca": 0, "internal": 0}

    def _alpaca_probe(*_args: Any, **_kwargs: Any) -> None:
        execution_calls["alpaca"] += 1

    def _internal_probe(*_args: Any, **_kwargs: Any) -> None:
        execution_calls["internal"] += 1

    monkeypatch.setattr("app.services.paper.alpaca_paper.submit_alpaca_paper_order", _alpaca_probe)
    monkeypatch.setattr("app.services.paper.internal_sim.execute_internal_crypto_fill", _internal_probe)

    result = await evaluate_arena_candidate_action(
        db=session,
        request=_request(proposal=proposal, quantity="1"),
    )

    assert result.action == "approve"
    assert result.approved_quantity == Decimal("1")
    assert result.reason_code is None
    assert result.persisted_risk_event_action == "approved"
    assert result.persisted_risk_event_type == "risk_approval"
    assert execution_calls == {"alpaca": 0, "internal": 0}
    assert len(session.decisions) == 1
    assert len(session.risk_events) == 1


@pytest.mark.asyncio
async def test_resize_preserves_adjusted_quantity_and_provenance() -> None:
    session = _FakeSession()
    proposal = _proposal(
        cycle_id=uuid.uuid4(),
        competition_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    session.proposals.append(proposal)

    result = await evaluate_arena_candidate_action(
        db=session,
        request=_request(
            proposal=proposal,
            quantity="2",
            max_position_size_pct="0.10",
        ),
    )

    assert result.action == "resize"
    assert result.approved_quantity == Decimal("1.00")
    assert result.reason_code == "position_resized_by_risk_engine"
    assert result.provenance["risk_engine"]["reason_code"] == "position_resized_by_risk_engine"
    assert result.persisted_risk_event_reason_code == "position_resized_by_risk_engine"


@pytest.mark.asyncio
async def test_rejection_is_recorded_and_reason_codes_are_preserved() -> None:
    session = _FakeSession()
    proposal = _proposal(
        cycle_id=uuid.uuid4(),
        competition_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    session.proposals.append(proposal)

    request = _request(proposal=proposal, quantity="1")
    request = ArenaRiskEvaluationRequest(
        **{**request.__dict__, "risk_context": _risk_context(global_kill_switch_engaged_state=True)}
    )

    result = await evaluate_arena_candidate_action(db=session, request=request)

    assert result.action == "reject"
    assert result.approved_quantity == Decimal("0")
    assert result.reason_code == "global_kill_switch_engaged"
    assert result.persisted_risk_event_reason_code == "global_kill_switch_engaged"
    assert len(session.audit_logs) == 1


@pytest.mark.asyncio
async def test_missing_risk_context_observation_fails_closed() -> None:
    session = _FakeSession()
    proposal = _proposal(
        cycle_id=uuid.uuid4(),
        competition_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    session.proposals.append(proposal)

    with pytest.raises(InvalidRequestError, match="requires observed global kill switch state"):
        await evaluate_arena_candidate_action(
            db=session,
            request=_request(
                proposal=proposal,
                quantity="1",
                global_kill_switch_state_observed=False,
            ),
        )


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_decision_record() -> None:
    session = _FakeSession()
    proposal = _proposal(
        cycle_id=uuid.uuid4(),
        competition_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )
    session.proposals.append(proposal)
    request = _request(proposal=proposal, quantity="1")

    first = await evaluate_arena_candidate_action(db=session, request=request)
    second = await evaluate_arena_candidate_action(db=session, request=request)

    assert first.risk_gate_decision_id == second.risk_gate_decision_id
    assert len(session.decisions) == 1
    assert len(session.risk_events) == 1
