from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.operator_cli.service as service


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _ExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarResult(self._values)


class _FakeDb:
    def __init__(self, *, cycles, packages, campaign_id, campaign_version):
        self.cycles = cycles
        self.packages = packages
        self.campaign_id = campaign_id
        self.campaign_version = campaign_version

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM canonical_proving_activations" in sql:
            return SimpleNamespace(campaign_id=self.campaign_id, campaign_version=self.campaign_version)
        if "FROM capital_campaign_definitions" in sql:
            return None
        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM autonomous_cycle_runs" in sql:
            return _ExecuteResult(self.cycles)
        if "FROM canonical_preview_packages" in sql:
            return _ExecuteResult(self.packages)
        return _ExecuteResult([])


class _SessionContext:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        return False


def _cycle(*, proposed_action: str, decision_id=None, reason: str | None = None):
    cycle_id = uuid4()
    started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    context = {
        "authoritative_composition": {
            "selected_decision": {
                "reason": reason,
            }
        }
    }
    return SimpleNamespace(
        cycle_id=cycle_id,
        proposed_action=proposed_action,
        decision_record_id=decision_id,
        started_at=started_at,
        cycle_context=context,
        failure_reason=None,
        deterministic_explanation=[],
    )


def _buy_package(*, campaign_id, campaign_version, decision_id, state: str, invalidated_reason: str | None = None):
    return SimpleNamespace(
        package_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        decision_record_id=decision_id,
        side="BUY",
        package_state=state,
        invalidated_reason=invalidated_reason,
        generated_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )


@pytest.mark.asyncio
async def test_buy_opportunity_diagnostic_counts_and_primary_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    decision_a = uuid4()
    decision_b = uuid4()

    cycles = [
        _cycle(proposed_action="OPEN_POSITION_PROPOSED", decision_id=decision_a, reason="risk_rejected"),
        _cycle(proposed_action="OPEN_POSITION_PROPOSED", decision_id=decision_b, reason=None),
        _cycle(proposed_action="CLOSE_POSITION_PROPOSED", decision_id=uuid4(), reason=None),
        _cycle(proposed_action="HOLD", decision_id=uuid4(), reason="non_positive_net_edge"),
    ]
    packages = [
        _buy_package(campaign_id=campaign_id, campaign_version=1, decision_id=decision_b, state="READY"),
    ]

    db = _FakeDb(cycles=cycles, packages=packages, campaign_id=campaign_id, campaign_version=1)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.buy_opportunity_diagnostic()

    assert payload["totals"]["strategy_evaluations"] == 4
    assert payload["totals"]["buy_opportunities"] == 2
    assert payload["totals"]["sell_opportunities"] == 1
    assert payload["totals"]["hold_decisions"] == 1
    assert payload["totals"]["ready_packages"] == 1
    assert len(payload["buy_blockers"]) == 2
    assert payload["summary"]["primary_blocker"] == "Risk"


@pytest.mark.asyncio
async def test_buy_opportunity_diagnostic_zero_buys(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    cycles = [
        _cycle(proposed_action="HOLD", decision_id=uuid4(), reason="non_positive_net_edge"),
        _cycle(proposed_action="CLOSE_POSITION_PROPOSED", decision_id=uuid4(), reason=None),
    ]
    db = _FakeDb(cycles=cycles, packages=[], campaign_id=campaign_id, campaign_version=1)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.buy_opportunity_diagnostic()

    assert payload["totals"]["buy_opportunities"] == 0
    assert payload["no_buy_opportunities"] is True
    assert payload["summary"]["primary_blocker"] == "none"


def test_buy_opportunity_diagnostic_is_read_only() -> None:
    source = service.buy_opportunity_diagnostic.__code__.co_names
    assert "commit" not in source
    assert "rollback" not in source
    assert "flush" not in source
    assert "create_canonical_preview_package" not in source
    assert "authorize_canonical_preview_package" not in source
