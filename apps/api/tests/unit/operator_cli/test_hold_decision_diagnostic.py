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
    def __init__(self, *, cycles, campaign_id, campaign_version):
        self.cycles = cycles
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
        return _ExecuteResult([])


class _SessionContext:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        return False


def _cycle(*, proposed_action: str, reason: str, decision_kind: str, expected_net: str | None = None):
    started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    cycle_id = uuid4()
    instrument = "BTC-USD"
    rejected_row = {
        "instrument": instrument,
        "reason": reason,
        "expected_net_dollars": expected_net,
        "risk": {"verdict": "ALLOW"},
    }
    context = {
        "supported_trigger": {"product_id": instrument},
        "candle": {
            "close_time": (started_at - timedelta(minutes=15)).isoformat(),
        },
        "authoritative_composition": {
            "selected_decision": {
                "decision_kind": decision_kind,
                "reason": reason,
                "instrument": instrument,
                "strategy_identity": "ma_crossover@1.0.0",
                "strategy_version": "1.0.0",
                "risk_verdict": "ALLOW",
            },
            "rejected_candidates": [rejected_row],
            "authoritative_evidence": {
                "market": {
                    instrument: {
                        "freshness": "fresh",
                        "source_identity": {"candle_id": "candle-1"},
                    }
                },
                "position": {
                    instrument: {
                        "position": {"quantity": "0"},
                    }
                },
            },
        },
    }
    return SimpleNamespace(
        cycle_id=cycle_id,
        proposed_action=proposed_action,
        decision_record_id=uuid4(),
        started_at=started_at,
        cycle_context=context,
        failure_reason=None,
        deterministic_explanation=[],
    )


@pytest.mark.asyncio
async def test_hold_decision_diagnostic_reports_hold_details_and_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    cycles = [
        _cycle(
            proposed_action="HOLD",
            reason="non_positive_net_edge",
            decision_kind="HOLD",
            expected_net="-2.50",
        ),
        _cycle(
            proposed_action="OPEN_POSITION_PROPOSED",
            reason="ready",
            decision_kind="OPEN_POSITION_PROPOSED",
            expected_net="2.00",
        ),
    ]

    db = _FakeDb(cycles=cycles, campaign_id=campaign_id, campaign_version=1)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    payload = await service.hold_decision_diagnostic()

    assert payload["totals"]["strategy_evaluations"] == 2
    assert payload["totals"]["buy_opportunities"] == 1
    assert payload["totals"]["sell_opportunities"] == 0
    assert payload["totals"]["hold_decisions"] == 1
    assert len(payload["hold_decisions"]) == 1
    hold = payload["hold_decisions"][0]
    assert hold["hold_reason"] == "non_positive_net_edge"
    assert hold["candle_id"] == "candle-1"
    assert hold["first_unmet_buy_condition"] == "decision_kind_open_position"
    assert payload["summary"]["most_common_hold_reason"] == "non_positive_net_edge"


@pytest.mark.asyncio
async def test_hold_decision_diagnostic_missing_campaign_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MissingIdentityDb:
        async def scalar(self, _statement):
            return None

        async def execute(self, _statement):
            return _ExecuteResult([])

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(_MissingIdentityDb()))

    payload = await service.hold_decision_diagnostic()

    assert payload["canonical_proving_campaign"] is None
    assert payload["totals"]["hold_decisions"] == 0
    assert payload["summary"]["most_common_hold_reason"] == "none"


def test_hold_decision_diagnostic_is_read_only() -> None:
    source = service.hold_decision_diagnostic.__code__.co_names
    assert "commit" not in source
    assert "rollback" not in source
    assert "flush" not in source
    assert "create_canonical_preview_package" not in source
    assert "authorize_canonical_preview_package" not in source
