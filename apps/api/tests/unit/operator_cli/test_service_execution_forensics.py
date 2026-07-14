from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.operator_cli.service as service
from app.operator_cli.formatting import render_json


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _SessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FetchDB:
    def __init__(self, *, latest_cycle=None, cycles_for_since=None, cycles_by_id=None):
        self.latest_cycle = latest_cycle
        self.cycles_for_since = list(cycles_for_since or [])
        self.cycles_by_id = dict(cycles_by_id or {})

    async def scalar(self, _stmt):
        return self.latest_cycle

    async def execute(self, _stmt):
        return _ScalarListResult(self.cycles_for_since)

    async def get(self, _model, cycle_id):
        return self.cycles_by_id.get(cycle_id)

    async def commit(self):
        raise AssertionError("read-only command must not call commit")

    async def flush(self):
        raise AssertionError("read-only command must not call flush")


class _BuildDB:
    def __init__(self, *, decision=None, signals=None, risk_events=None, trades=None, signal_audits=None, trade_audit=None):
        self.decision = decision
        self.signals = list(signals or [])
        self.risk_events = list(risk_events or [])
        self.trades = list(trades or [])
        self.signal_audits = list(signal_audits or [])
        self.trade_audit = trade_audit

    async def get(self, model, key):
        if model is service.DecisionRecord:
            if self.decision is not None and self.decision.id == key:
                return self.decision
            return None
        if model is service.RiskEvent:
            for item in self.risk_events:
                if item.id == key:
                    return item
            return None
        return None

    async def scalar(self, stmt):
        first = stmt.column_descriptions[0].get("name")
        if first == "close_time":
            return None
        entity = stmt.column_descriptions[0].get("entity")
        if entity is service.AuditLog:
            return self.trade_audit
        return None

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is service.Signal:
            return _ScalarListResult(self.signals)
        if entity is service.Strategy:
            strategy_ids = {item.strategy_id for item in self.signals}
            rows = [SimpleNamespace(id=item, slug=f"strategy-{str(item)[:8]}") for item in strategy_ids]
            return _ScalarListResult(rows)
        if entity is service.Asset:
            asset_ids = {item.asset_id for item in self.signals}
            rows = [SimpleNamespace(id=item, symbol="BTC", exchange="kraken_spot") for item in asset_ids]
            return _ScalarListResult(rows)
        if entity is service.RiskEvent:
            return _ScalarListResult(self.risk_events)
        if entity is service.Trade:
            return _ScalarListResult(self.trades)
        if entity is service.AuditLog:
            return _ScalarListResult(self.signal_audits)
        if entity is service.StrategyRosterRun:
            return _ScalarListResult([])
        if entity is service.StrategyRosterProposalOutcome:
            return _ScalarListResult([])
        if entity is service.ValidationRunEvent:
            return _ScalarListResult([])
        return _ScalarListResult([])


@pytest.mark.asyncio
async def test_fetch_execution_forensics_latest_selector(monkeypatch) -> None:
    cycle = SimpleNamespace(cycle_id=uuid4(), started_at=datetime.now(timezone.utc))
    db = _FetchDB(latest_cycle=cycle)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_build_cycle_forensics(*, db, cycle):
        return {"cycle_id": cycle.cycle_id}

    monkeypatch.setattr(service, "_build_cycle_forensics", _fake_build_cycle_forensics)

    payload = await service.fetch_execution_forensics(since=None, cycle_id=None, latest=True)

    assert payload["mode"] == "read_only_forensics"
    assert payload["criteria"]["selector"] == "latest"
    assert payload["cycle_count"] == 1


@pytest.mark.asyncio
async def test_fetch_execution_forensics_since_dedupes_and_flags_truncation(monkeypatch) -> None:
    cycle_id = uuid4()
    rows = [
        SimpleNamespace(cycle_id=cycle_id, started_at=datetime.now(timezone.utc)),
        SimpleNamespace(cycle_id=cycle_id, started_at=datetime.now(timezone.utc)),
    ]
    db = _FetchDB(cycles_for_since=rows)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_build_cycle_forensics(*, db, cycle):
        return {"cycle_id": cycle.cycle_id}

    monkeypatch.setattr(service, "_build_cycle_forensics", _fake_build_cycle_forensics)

    payload = await service.fetch_execution_forensics(since="2 hours ago", cycle_id=None, latest=False)

    assert payload["cycle_count"] == 1
    assert payload["criteria"]["selector"] == "since"
    assert payload["criteria"]["max_cycles"] == service._EXECUTION_FORENSICS_MAX_SINCE_CYCLES
    assert payload["truncated"] is False


@pytest.mark.asyncio
async def test_fetch_execution_forensics_cycle_not_found_raises(monkeypatch) -> None:
    db = _FetchDB(cycles_by_id={})
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(ValueError, match="not found"):
        await service.fetch_execution_forensics(since=None, cycle_id=uuid4(), latest=False)


@pytest.mark.asyncio
async def test_fetch_execution_forensics_invalid_selector_raises() -> None:
    with pytest.raises(ValueError, match="exactly one selector"):
        await service.fetch_execution_forensics(since="2 hours ago", cycle_id=uuid4(), latest=False)


@pytest.mark.asyncio
async def test_fetch_execution_forensics_invalid_since_raises(monkeypatch) -> None:
    db = _FetchDB(cycles_for_since=[])
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    with pytest.raises(ValueError):
        await service.fetch_execution_forensics(since="not-a-time", cycle_id=None, latest=False)


@pytest.mark.asyncio
async def test_build_cycle_forensics_hold_only_reports_not_applicable_risk() -> None:
    signal_id = uuid4()
    decision_id = uuid4()
    asset_id = uuid4()
    strategy_id = uuid4()
    cycle = SimpleNamespace(
        cycle_id=uuid4(),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        decision_record_id=decision_id,
        risk_event_id=None,
        cycle_context={"strategy_interval": "15m"},
    )
    decision = SimpleNamespace(id=decision_id, source_lineage={"signals": [str(signal_id)]}, timeframe="15m", pnl=None)
    signals = [
        SimpleNamespace(
            id=signal_id,
            strategy_id=strategy_id,
            action="hold",
            ai_confidence=0.42,
            status="emitted",
            asset_id=asset_id,
        )
    ]

    payload = await service._build_cycle_forensics(
        db=_BuildDB(decision=decision, signals=signals),
        cycle=cycle,
    )

    assert payload["execution_candidate"]["status"] == "NO"
    assert payload["execution_candidate"]["reason_if_no"] == "HOLD"
    assert payload["risk"]["evaluated_status"] == "NOT APPLICABLE"
    assert payload["execution"]["execution_attempted_status"] == "NO"


@pytest.mark.asyncio
async def test_build_cycle_forensics_risk_rejection_without_trade() -> None:
    signal_id = uuid4()
    decision_id = uuid4()
    asset_id = uuid4()
    strategy_id = uuid4()
    risk_id = uuid4()

    cycle = SimpleNamespace(
        cycle_id=uuid4(),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        decision_record_id=decision_id,
        risk_event_id=risk_id,
        cycle_context={"strategy_interval": "15m"},
    )
    decision = SimpleNamespace(id=decision_id, source_lineage={"signals": [str(signal_id)]}, timeframe="15m", pnl=None)
    signals = [
        SimpleNamespace(
            id=signal_id,
            strategy_id=strategy_id,
            action="buy",
            ai_confidence=0.92,
            status="emitted",
            asset_id=asset_id,
        )
    ]
    risk_events = [SimpleNamespace(id=risk_id, created_at=datetime.now(timezone.utc), action_taken="rejected", detail="risk_limit")]
    signal_audits = [
        SimpleNamespace(
            id=uuid4(),
            created_at=datetime.now(timezone.utc),
            action="signal_execution_rejected",
            entity_type="signal",
            entity_id=signal_id,
            before_state={},
            after_state={},
        )
    ]

    payload = await service._build_cycle_forensics(
        db=_BuildDB(decision=decision, signals=signals, risk_events=risk_events, signal_audits=signal_audits),
        cycle=cycle,
    )

    assert payload["risk"]["evaluated_status"] == "YES"
    assert payload["execution"]["trade_created_status"] == "NO"
    assert payload["execution"]["rejected_status"] == "YES"
    assert payload["execution"]["filled_status"] == "NO"


@pytest.mark.asyncio
async def test_build_cycle_forensics_trade_fill_requires_trade_audit_evidence() -> None:
    signal_id = uuid4()
    decision_id = uuid4()
    asset_id = uuid4()
    strategy_id = uuid4()
    paper_account_id = uuid4()
    trade_id = uuid4()
    executed_at = datetime.now(timezone.utc)

    cycle = SimpleNamespace(
        cycle_id=uuid4(),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        decision_record_id=decision_id,
        risk_event_id=None,
        cycle_context={"strategy_interval": "15m"},
    )
    decision = SimpleNamespace(id=decision_id, source_lineage={"signals": [str(signal_id)]}, timeframe="15m", pnl=None)
    signals = [
        SimpleNamespace(
            id=signal_id,
            strategy_id=strategy_id,
            action="buy",
            ai_confidence=0.92,
            status="emitted",
            asset_id=asset_id,
        )
    ]
    trades = [
        SimpleNamespace(
            id=trade_id,
            signal_id=signal_id,
            paper_account_id=paper_account_id,
            asset_id=asset_id,
            side="buy",
            quantity="0.010",
            fee="0.01",
            executed_at=executed_at,
        )
    ]

    payload_without_audit = await service._build_cycle_forensics(
        db=_BuildDB(decision=decision, signals=signals, trades=trades, trade_audit=None),
        cycle=cycle,
    )
    assert payload_without_audit["execution"]["trade_created_status"] == "YES"
    assert payload_without_audit["execution"]["filled_status"] == "UNPROVEN"
    assert payload_without_audit["accounting"]["accounting_entry_persisted_status"] == "UNPROVEN"

    trade_audit = SimpleNamespace(before_state={"cash_balance": "1000.00"}, after_state={"cash_balance": "999.00"})
    payload_with_audit = await service._build_cycle_forensics(
        db=_BuildDB(decision=decision, signals=signals, trades=trades, trade_audit=trade_audit),
        cycle=cycle,
    )
    assert payload_with_audit["execution"]["filled_status"] == "YES"
    assert payload_with_audit["accounting"]["accounting_entry_persisted_status"] == "YES"


@pytest.mark.asyncio
async def test_build_cycle_forensics_candidate_without_execution_call_is_unproven() -> None:
    signal_id = uuid4()
    decision_id = uuid4()
    asset_id = uuid4()
    strategy_id = uuid4()
    cycle = SimpleNamespace(
        cycle_id=uuid4(),
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        decision_record_id=decision_id,
        risk_event_id=None,
        cycle_context={"strategy_interval": "15m"},
    )
    decision = SimpleNamespace(id=decision_id, source_lineage={"signals": [str(signal_id)]}, timeframe="15m", pnl=None)
    signals = [
        SimpleNamespace(
            id=signal_id,
            strategy_id=strategy_id,
            action="buy",
            ai_confidence=0.85,
            status="emitted",
            asset_id=asset_id,
        )
    ]

    payload = await service._build_cycle_forensics(
        db=_BuildDB(decision=decision, signals=signals),
        cycle=cycle,
    )

    assert payload["execution"]["execution_attempted_status"] == "YES"
    assert payload["execution"]["execution_service_called_status"] == "UNPROVEN"
    assert payload["execution"]["trade_created_status"] == "NO"
    assert payload["execution"]["filled_status"] == "NO"


@pytest.mark.asyncio
async def test_execution_forensics_payload_is_json_serializable(monkeypatch) -> None:
    cycle = SimpleNamespace(cycle_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), started_at=datetime.now(timezone.utc))
    db = _FetchDB(latest_cycle=cycle)
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

    async def _fake_build_cycle_forensics(*, db, cycle):
        return {"cycle_id": cycle.cycle_id, "timestamp": cycle.started_at, "summary": "ok"}

    monkeypatch.setattr(service, "_build_cycle_forensics", _fake_build_cycle_forensics)

    payload = await service.fetch_execution_forensics(since=None, cycle_id=None, latest=True)
    rendered = render_json(payload)

    assert "read_only_forensics" in rendered
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in rendered
