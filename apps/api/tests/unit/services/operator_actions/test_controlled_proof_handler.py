from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.operator_actions import controlled_proof_handler as handler


def _action(*, status: str = "ACCEPTED", linked_resource_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, result=None, linked_resource_id=linked_resource_id or uuid.uuid4())


def _view(**overrides) -> dict:
    base = {
        "status": "REQUESTED", "terminal_verdict": None, "net_pnl_usd": None, "fees_usd": None,
        "blocked_reason": None, "failure_reason": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_proof_requested_projects_to_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler, "get_controlled_proof_view", lambda **_: _async(_view(status="REQUESTED")))
    projection = await handler._project(object(), _action())
    assert projection.status == "ACCEPTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof_status",
    ["CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN", "WAITING_FOR_PROFITABLE_EXIT", "EXITED"],
)
async def test_active_proof_states_project_to_in_progress(monkeypatch: pytest.MonkeyPatch, proof_status: str) -> None:
    """Requirement 8."""
    monkeypatch.setattr(handler, "get_controlled_proof_view", lambda **_: _async(_view(status=proof_status)))
    projection = await handler._project(object(), _action())
    assert projection.status == "IN_PROGRESS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict,net_pnl",
    [("LIFECYCLE_PROVEN_PROFIT", "1.23"), ("LIFECYCLE_PROVEN_LOSS", "-0.04"), ("LIFECYCLE_PROVEN_FLAT", "0")],
)
async def test_all_three_lifecycle_proven_verdicts_project_to_succeeded(
    monkeypatch: pytest.MonkeyPatch, verdict: str, net_pnl: str,
) -> None:
    """Requirement 9: SUCCEEDED regardless of profit/loss/flat, but the
    actual verdict and net P&L must be preserved verbatim in result -- never
    relabeled as profit."""
    for proof_status in ("RECONCILED", "PROFIT_CONFIRMED"):
        monkeypatch.setattr(
            handler, "get_controlled_proof_view",
            lambda **_: _async(_view(status=proof_status, terminal_verdict=verdict, net_pnl_usd=net_pnl, fees_usd="0.02")),
        )
        projection = await handler._project(object(), _action())
        assert projection.status == "SUCCEEDED"
        assert projection.result["terminal_verdict"] == verdict
        assert projection.result["net_pnl_usd"] == net_pnl


@pytest.mark.asyncio
async def test_reconciled_without_a_computed_verdict_yet_stays_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never SUCCEEDED ahead of a real, computed verdict."""
    monkeypatch.setattr(handler, "get_controlled_proof_view", lambda **_: _async(_view(status="RECONCILED", terminal_verdict=None)))
    projection = await handler._project(object(), _action())
    assert projection.status == "IN_PROGRESS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof_status,expected_action_status",
    [("BLOCKED", "BLOCKED"), ("FAILED", "FAILED"), ("CANCELLED", "CANCELLED"), ("EXPIRED", "EXPIRED")],
)
async def test_terminal_non_success_states_project_correctly(
    monkeypatch: pytest.MonkeyPatch, proof_status: str, expected_action_status: str,
) -> None:
    """Requirement 10."""
    monkeypatch.setattr(
        handler, "get_controlled_proof_view",
        lambda **_: _async(_view(status=proof_status, blocked_reason="reason-b", failure_reason="reason-f")),
    )
    projection = await handler._project(object(), _action())
    assert projection.status == expected_action_status
    if expected_action_status == "BLOCKED":
        assert projection.blocked_reason == "reason-b"
        assert projection.failure_reason is None
    elif expected_action_status == "FAILED":
        assert projection.failure_reason == "reason-f"
        assert projection.blocked_reason is None
    else:
        assert projection.blocked_reason is None
        assert projection.failure_reason is None


async def _async(value):
    return value
