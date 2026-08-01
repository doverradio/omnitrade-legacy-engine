from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.orchestration import autonomous_proof_sell_worker as subject


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


class FakeDb:
    def __init__(self, custody):
        self.custody = custody
        self.flushes = 0

    async def get(self, model, identity):
        return self.custody

    async def flush(self):
        self.flushes += 1

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def refresh(self, _row):
        return None

    async def rollback(self):
        return None


def attempt(stage: str):
    return SimpleNamespace(
        attempt_id=uuid4(), custody_id=uuid4(), stage=stage, hard_stopped=False,
        authority_id=None, package_id=None, activation_id=None, claim_id=None,
        order_id=None, reconciliation_id=None, blocker=None, retry_count=0,
        next_attempt_at=None, terminal_reason=None, proof_sell_verified=False,
        updated_at=NOW,
    )


@pytest.fixture
def enabled_scope(monkeypatch):
    scope = (uuid4(), 1, uuid4())
    monkeypatch.setattr(subject, "_scope", lambda: scope)
    return scope


def test_all_autonomous_proof_sell_gates_default_false():
    settings = Settings(_env_file=None)
    assert settings.autonomous_proof_sell_worker_enabled is False
    assert settings.autonomous_position_exit_submission_enabled is False
    assert settings.autonomous_proof_sell_campaign_id is None
    assert settings.autonomous_proof_sell_campaign_version is None
    assert settings.autonomous_proof_sell_runtime_campaign_id is None


@pytest.mark.asyncio
async def test_disabled_or_ambiguous_configuration_touches_no_database(monkeypatch):
    monkeypatch.setattr(subject, "_scope", lambda: None)
    result = await subject.advance_one_autonomous_proof_sell_stage(db=object(), now=NOW, cadence_seconds=30)
    assert result.action == "disabled_or_ambiguous"
    assert result.provider_call_made is False


@pytest.mark.asyncio
async def test_default_worker_cadence_fails_closed_before_database_access(monkeypatch, enabled_scope):
    result = await subject.advance_one_autonomous_proof_sell_stage(
        db=object(), now=NOW, cadence_seconds=300,
    )
    assert result.action == "unsafe_worker_cadence"
    assert result.attempt_id is None


@pytest.mark.asyncio
async def test_worker_advances_exactly_one_existing_service_per_cycle(monkeypatch, enabled_scope):
    custody = SimpleNamespace(custody_id=uuid4(), proof_eligible=True, disqualification_reason=None,
                              audit_metadata={"latest_exit_evaluation": {"disposition": "EXIT_RECOMMENDED", "price_fresh": True}})
    db = FakeDb(custody)
    row = attempt("SELECTED")
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))

    calls = []
    async def authority(**kwargs):
        calls.append("authority"); return SimpleNamespace(authority_id=uuid4(), authority_state="ARMED"), (), False
    async def paperwork(**kwargs):
        calls.append("package"); return SimpleNamespace(package_id=uuid4())
    async def activation(**kwargs):
        calls.append("activation_claim"); return SimpleNamespace(activation_id=uuid4(), claim_id=uuid4())
    async def order(**kwargs):
        calls.append("order"); return SimpleNamespace(order_id=uuid4())
    async def submission(**kwargs):
        calls.append("submission"); return SimpleNamespace(status="ACKNOWLEDGED", provider_call_made=True)
    monkeypatch.setattr(subject, "issue_exit_authority", authority)
    monkeypatch.setattr(subject, "construct_exit_paperwork", paperwork)
    monkeypatch.setattr(subject, "activate_exit_package_and_claim", activation)
    monkeypatch.setattr(subject, "construct_autonomous_exit_order", order)
    monkeypatch.setattr(subject, "submit_autonomous_exit_order", submission)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(autonomous_position_exit_submission_enabled=True))

    expected = [
        ("SELECTED", "EVALUATED", []),
        ("EVALUATED", "AUTHORIZED", ["authority"]),
        ("AUTHORIZED", "PACKAGED", ["authority", "package"]),
        ("PACKAGED", "CLAIMED", ["authority", "package", "activation_claim"]),
        ("CLAIMED", "ORDERED", ["authority", "package", "activation_claim", "order"]),
        ("ORDERED", "RECONCILING", ["authority", "package", "activation_claim", "order", "submission"]),
    ]
    for start, end, cumulative_calls in expected:
        row.stage = start
        result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
        assert row.stage == end
        assert calls == cumulative_calls
        assert result.stage == end


@pytest.mark.asyncio
async def test_submission_requires_second_gate_without_calling_provider(monkeypatch, enabled_scope):
    custody = SimpleNamespace(proof_eligible=True, disqualification_reason=None)
    db = FakeDb(custody); row = attempt("ORDERED"); row.order_id = uuid4()
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(autonomous_position_exit_submission_enabled=False))
    called = False
    async def submission(**kwargs):
        nonlocal called; called = True
    monkeypatch.setattr(subject, "submit_autonomous_exit_order", submission)
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.action == "submission_disabled"
    assert row.stage == "ORDERED"
    assert row.blocker == "submission_gate_disabled"
    assert called is False


@pytest.mark.asyncio
async def test_partial_reconciliation_retries_same_order_only(monkeypatch, enabled_scope):
    custody = SimpleNamespace(proof_eligible=True, disqualification_reason=None,
                              custody_state="EXIT_PENDING", exit_reconciliation_event_id=None)
    db = FakeDb(custody); row = attempt("RECONCILING"); row.order_id = uuid4()
    original_order = row.order_id
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))
    async def reconcile(**kwargs):
        assert kwargs["order_id"] == original_order
        return SimpleNamespace(terminal=False, status="PARTIALLY_FILLED", proof_sell_verified=False)
    monkeypatch.setattr(subject, "reconcile_autonomous_exit_order", reconcile)
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "RECONCILING"
    assert row.order_id == original_order
    assert row.blocker == "reconciliation_incomplete"
    assert row.next_attempt_at > NOW


@pytest.mark.asyncio
async def test_uncertain_submission_replays_recovery_on_original_order(monkeypatch, enabled_scope):
    custody = SimpleNamespace(proof_eligible=True, disqualification_reason=None)
    db = FakeDb(custody); row = attempt("ORDERED"); row.order_id = uuid4()
    original_order = row.order_id
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(autonomous_position_exit_submission_enabled=True))
    seen = []
    async def submission(**kwargs):
        seen.append(kwargs["order_id"])
        return SimpleNamespace(status="RECONCILIATION_REQUIRED", provider_call_made=True)
    monkeypatch.setattr(subject, "submit_autonomous_exit_order", submission)
    await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert row.stage == "ORDERED"
    assert row.blocker == "provider_outcome_uncertain"
    row.next_attempt_at = NOW
    await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert seen == [original_order, original_order]
    assert row.order_id == original_order


@pytest.mark.asyncio
async def test_terminal_result_is_permanent_hard_stop(monkeypatch, enabled_scope):
    custody = SimpleNamespace(proof_eligible=True, disqualification_reason=None,
                              custody_state="CLOSED", exit_reconciliation_event_id=uuid4())
    db = FakeDb(custody); row = attempt("RECONCILING"); row.order_id = uuid4()
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))
    monkeypatch.setattr(subject, "reconcile_autonomous_exit_order", lambda **kwargs: async_value(
        SimpleNamespace(terminal=True, status="FILLED", proof_sell_verified=True)))
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "TERMINAL"
    assert row.hard_stopped is True
    assert row.proof_sell_verified is True
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.action == "hard_stopped"


@pytest.mark.asyncio
async def test_single_coordinator_lifecycle_uncertainty_partial_fill_then_profitable_terminal(monkeypatch, enabled_scope):
    custody = SimpleNamespace(
        custody_id=uuid4(), proof_eligible=True, disqualification_reason=None,
        custody_state="ACTIVE", exit_reconciliation_event_id=None,
        audit_metadata={"latest_exit_evaluation": {"disposition": "EXIT_RECOMMENDED", "price_fresh": True}},
    )
    db = FakeDb(custody); row = attempt("SELECTED")
    monkeypatch.setattr(subject, "_locked_attempt", lambda *args: async_value(row))
    ids = {name: uuid4() for name in ("authority", "package", "activation", "claim", "order", "reconciliation")}
    calls = []; submission_results = iter(("RECONCILIATION_REQUIRED", "ACKNOWLEDGED"))
    reconciliation_results = iter((False, True))

    async def authority(**kwargs): calls.append("authority"); return SimpleNamespace(authority_id=ids["authority"], authority_state="ARMED"), (), False
    async def package(**kwargs): calls.append("package"); return SimpleNamespace(package_id=ids["package"])
    async def activation(**kwargs): calls.append("activation"); return SimpleNamespace(activation_id=ids["activation"], claim_id=ids["claim"])
    async def order(**kwargs): calls.append("order"); return SimpleNamespace(order_id=ids["order"])
    async def submission(**kwargs):
        assert kwargs["order_id"] == ids["order"]
        calls.append("submission_or_recovery")
        return SimpleNamespace(status=next(submission_results), provider_call_made=True)
    async def reconciliation(**kwargs):
        assert kwargs["order_id"] == ids["order"]
        terminal = next(reconciliation_results); calls.append("reconciliation")
        if terminal:
            custody.custody_state = "CLOSED"; custody.exit_reconciliation_event_id = ids["reconciliation"]
        return SimpleNamespace(terminal=terminal, status="FILLED" if terminal else "PARTIALLY_FILLED",
                               proof_sell_verified=terminal)

    monkeypatch.setattr(subject, "issue_exit_authority", authority)
    monkeypatch.setattr(subject, "construct_exit_paperwork", package)
    monkeypatch.setattr(subject, "activate_exit_package_and_claim", activation)
    monkeypatch.setattr(subject, "construct_autonomous_exit_order", order)
    monkeypatch.setattr(subject, "submit_autonomous_exit_order", submission)
    monkeypatch.setattr(subject, "reconcile_autonomous_exit_order", reconciliation)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(autonomous_position_exit_submission_enabled=True))

    expected_stages = ("EVALUATED", "AUTHORIZED", "PACKAGED", "CLAIMED", "ORDERED")
    for stage in expected_stages:
        result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
        assert result.stage == stage
    # Unknown outcome: remain on this order and use recovery on replay.
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "ORDERED" and row.order_id == ids["order"]
    row.next_attempt_at = NOW
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "RECONCILING"
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "RECONCILING" and row.order_id == ids["order"]
    row.next_attempt_at = NOW
    result = await subject.advance_one_autonomous_proof_sell_stage(db=db, now=NOW, cadence_seconds=30)
    assert result.stage == "TERMINAL" and row.proof_sell_verified is True
    assert row.reconciliation_id == ids["reconciliation"]
    assert calls == ["authority", "package", "activation", "order", "submission_or_recovery",
                     "submission_or_recovery", "reconciliation", "reconciliation"]


async def async_value(value):
    return value
