from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.schemas.capital_campaign_domain import CommissionedControlPlaneMutationRequest
from app.services.capital_campaign_domain import commissioned_control_plane as control_plane


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1


def _definition(campaign_id, *, version: int):
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        metadata_evidence={
            "commissioned_seed_campaign": {
                "state": "ACTIVE_POSITION",
                "commissioning": {
                    "commissioning_identity": "commission-1",
                    "commissioned_until": "2026-07-15T00:00:00+00:00",
                    "commissioned_by": "operator:human",
                    "authority_classification": "OPERATOR_COMMISSIONED",
                    "strategy_signal_classification": "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY",
                },
                "entry_execution": {
                    "decision_record_id": str(uuid4()),
                    "risk_event_id": str(uuid4()),
                },
                "ownership_reconciliation": {
                    "ownership_proven": True,
                    "position_identity": "btc-seed-1",
                    "provider_fill_ids": ["fill-1"],
                },
                "exit_recommendation": {
                    "last_recommendation": {
                        "recommendation_type": "HOLD_FOR_PROFIT",
                        "decision_record_id": str(uuid4()),
                        "risk_event_id": str(uuid4()),
                    },
                    "seen_idempotency_keys": {},
                },
                "transition_history": [],
            }
        },
        updated_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )


def _definition_with_state(campaign_id, *, version: int, state: str):
    definition = _definition(campaign_id, version=version)
    definition.metadata_evidence["commissioned_seed_campaign"]["state"] = state
    return definition


@pytest.mark.asyncio
async def test_control_plane_status_is_deterministic_and_no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, version=1)
    runtime = SimpleNamespace(uuid=campaign_id)
    fixed_now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    async def _load_def_runtime(**_kwargs):
        return definition, runtime

    async def _load_decision(**_kwargs):
        return {"decision_id": "dec-1", "outcome": "advisory_only"}

    async def _load_risk(**_kwargs):
        return {"risk_event_id": "risk-1", "action_taken": "allow"}

    async def _load_audit(**_kwargs):
        return []

    async def _submit_never(*_args, **_kwargs):
        raise AssertionError("submit must never be called by control plane")

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime", _load_def_runtime)
    monkeypatch.setattr(control_plane, "_load_decision_summary", _load_decision)
    monkeypatch.setattr(control_plane, "_load_risk_summary", _load_risk)
    monkeypatch.setattr(control_plane, "_load_audit_rows", _load_audit)
    monkeypatch.setattr(control_plane, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.services.live_crypto_orders.LiveCryptoOrderService.submit", _submit_never)

    first = await control_plane.get_commissioned_control_plane_status(db=_FakeDb(), campaign_id=campaign_id, version=1)
    second = await control_plane.get_commissioned_control_plane_status(db=_FakeDb(), campaign_id=campaign_id, version=1)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.no_execution is True
    assert first.read_only is True


@pytest.mark.asyncio
async def test_control_plane_mutation_idempotent_and_no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, version=1)
    runtime = SimpleNamespace(uuid=campaign_id)
    fixed_now = datetime(2026, 7, 14, 12, 1, tzinfo=timezone.utc)
    db = _FakeDb()

    async def _load_for_update(**_kwargs):
        return definition, runtime

    async def _submit_never(*_args, **_kwargs):
        raise AssertionError("submit must never be called by control plane")

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime_for_update", _load_for_update)
    monkeypatch.setattr(control_plane, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.services.live_crypto_orders.LiveCryptoOrderService.submit", _submit_never)

    request = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="operator:human",
        action="pause",
        idempotency_key="ctrl-idem-1",
        reason="manual stop",
    )

    first = await control_plane.mutate_commissioned_control_plane(db=db, request=request)
    second = await control_plane.mutate_commissioned_control_plane(db=db, request=request)

    assert first.accepted is True
    assert first.replayed is False
    assert first.no_execution is True
    assert second.replayed is True
    assert db.flush_calls == 1
    assert db.commit_calls == 1

    blob = definition.metadata_evidence["commissioned_seed_campaign"]["operator_control"]
    assert blob["paused"] is True
    assert blob["cancelled"] is False
    assert "ctrl-idem-1" in blob["seen_idempotency_keys"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "state"),
    [
        ("acknowledge", "READY"),
        ("pause", "COMPLETED"),
        ("resume", "RECONCILIATION_REQUIRED"),
        ("cancel", "FAILED_CLOSED"),
    ],
)
async def test_control_plane_mutation_invalid_source_states_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    state: str,
) -> None:
    campaign_id = uuid4()
    definition = _definition_with_state(campaign_id, version=1, state=state)
    runtime = SimpleNamespace(uuid=campaign_id)
    db = _FakeDb()

    async def _load_for_update(**_kwargs):
        return definition, runtime

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime_for_update", _load_for_update)

    request = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="operator:human",
        action=action,
        idempotency_key=f"idem-{action}",
        reason="state-check",
    )

    with pytest.raises(InvalidRequestError):
        await control_plane.mutate_commissioned_control_plane(db=db, request=request)

    assert db.flush_calls == 0
    assert db.commit_calls == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_control_plane_mutation_changed_intent_same_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition_with_state(campaign_id, version=1, state="ACTIVE_POSITION")
    runtime = SimpleNamespace(uuid=campaign_id)
    db = _FakeDb()

    async def _load_for_update(**_kwargs):
        return definition, runtime

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime_for_update", _load_for_update)

    first = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="operator:human",
        action="pause",
        idempotency_key="idem-shared",
        reason="one",
    )
    await control_plane.mutate_commissioned_control_plane(db=db, request=first)

    changed_intent = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="operator:human",
        action="resume",
        idempotency_key="idem-shared",
        reason="two",
    )
    with pytest.raises(InvalidRequestError):
        await control_plane.mutate_commissioned_control_plane(db=db, request=changed_intent)

    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_control_plane_mutation_concurrent_same_key_single_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition_with_state(campaign_id, version=1, state="ACTIVE_POSITION")
    runtime = SimpleNamespace(uuid=campaign_id)
    db = _FakeDb()
    lock = asyncio.Lock()

    async def _load_for_update(**_kwargs):
        async with lock:
            await asyncio.sleep(0)
            return definition, runtime

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime_for_update", _load_for_update)

    request = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="operator:human",
        action="pause",
        idempotency_key="idem-concurrent",
        reason="concurrency-check",
    )

    first, second = await asyncio.gather(
        control_plane.mutate_commissioned_control_plane(db=db, request=request),
        control_plane.mutate_commissioned_control_plane(db=db, request=request),
    )

    assert first.accepted is True
    assert second.replayed is True
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_control_plane_mutation_requires_non_empty_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition_with_state(campaign_id, version=1, state="ACTIVE_POSITION")
    runtime = SimpleNamespace(uuid=campaign_id)
    db = _FakeDb()

    async def _load_for_update(**_kwargs):
        return definition, runtime

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime_for_update", _load_for_update)

    request = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=1,
        actor="   ",
        action="pause",
        idempotency_key="idem-empty-actor",
        reason="security-check",
    )

    with pytest.raises(InvalidRequestError):
        await control_plane.mutate_commissioned_control_plane(db=db, request=request)

    assert db.flush_calls == 0
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_control_plane_status_renders_audit_rows_in_deterministic_order(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, version=1)
    runtime = SimpleNamespace(uuid=campaign_id)

    async def _load_def_runtime(**_kwargs):
        return definition, runtime

    async def _load_decision(**_kwargs):
        return None

    async def _load_risk(**_kwargs):
        return None

    older = AuditLog(
        id=1,
        actor="operator:older",
        action="older_action",
        entity_type="capital_campaign",
        entity_id=campaign_id,
        before_state=None,
        after_state=None,
        created_at=datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc),
    )
    newer = AuditLog(
        id=2,
        actor="operator:newer",
        action="newer_action",
        entity_type="capital_campaign",
        entity_id=campaign_id,
        before_state=None,
        after_state=None,
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )

    async def _load_audit(**_kwargs):
        return [newer, older]

    monkeypatch.setattr(control_plane, "_load_definition_and_runtime", _load_def_runtime)
    monkeypatch.setattr(control_plane, "_load_decision_summary", _load_decision)
    monkeypatch.setattr(control_plane, "_load_risk_summary", _load_risk)
    monkeypatch.setattr(control_plane, "_load_audit_rows", _load_audit)

    result = await control_plane.get_commissioned_control_plane_status(db=_FakeDb(), campaign_id=campaign_id, version=1)

    assert result.audit_summary["latest"][0]["action"] == "newer_action"
    audit_events = [item for item in result.campaign_timeline if item.get("kind") == "audit"]
    assert [item["action"] for item in audit_events] == ["older_action", "newer_action"]
