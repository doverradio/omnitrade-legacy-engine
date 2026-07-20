from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.audit_log import AuditLog
from app.services.capital_campaign_orchestration.service import build_campaign_orchestration_idempotency_key, fetch_campaign_orchestration_history, fetch_campaign_orchestration_readiness, fetch_campaign_orchestration_status
from app.services.risk import RiskDecisionAction, RiskEvaluationResult


class _FakeDb:
    async def scalar(self, _statement):
        return None

    async def execute(self, _statement):
        raise AssertionError("unexpected execute call in readiness test")


class _OrchestrationReadOnlyDb:
    def __init__(self, *, definition, runtime=None, asset=None, cycles=None) -> None:
        self.definition = definition
        self.runtime = runtime
        self.asset = asset
        self.cycles = cycles or []

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM capital_campaign_definitions" in sql:
            return self.definition
        if "FROM capital_campaigns" in sql:
            return self.runtime
        if "FROM assets" in sql:
            return self.asset
        if "FROM autonomous_cycle_runs" in sql:
            return self.cycles[0] if self.cycles else None
        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM capital_campaign_definitions" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [self.definition]))
        if "FROM autonomous_cycle_runs" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.cycles)))
        raise AssertionError(f"unexpected execute call: {sql}")


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_campaign_idempotency_key_changes_with_version() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    close_time = datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)
    first = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    second = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=2,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    assert first != second


def test_campaign_idempotency_key_changes_with_instrument_set_and_execution_mode() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    close_time = datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)
    baseline = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD", "ETH-USD"],
        execution_mode="preview",
    )
    different_instruments = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    different_mode = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD", "ETH-USD"],
        execution_mode="replay",
    )

    assert baseline != different_instruments
    assert baseline != different_mode


@pytest.mark.asyncio
async def test_campaign_readiness_accepts_draft_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="DRAFT",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
        metadata_evidence={},
    )
    runtime = SimpleNamespace(id=1, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=campaign.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=campaign, runtime=runtime, asset=None),
        campaign_id=campaign.campaign_id,
        version=campaign.version,
    )
    assert payload["items"][0]["ready"] is True
    assert payload["items"][0]["allows_draft_preview"] is True


@pytest.mark.asyncio
async def test_campaign_readiness_blocks_legacy_compounding_policy_without_policy_type() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={},
        metadata_evidence={"commissioned_seed_campaign": {"state": "READY"}},
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)
    asset = SimpleNamespace(id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), symbol="BTC", exchange="kraken_spot", created_at=datetime.now(timezone.utc))

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=asset),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    item = payload["items"][0]
    assert item["ready"] is False
    assert item["blockers"] == ["compounding_policy_missing_policy_type"]
    assert item["campaign_snapshot"]["compounding_policy_compatibility"]["status"] == "legacy_missing_policy_type"


@pytest.mark.asyncio
async def test_campaign_snapshot_recognizes_legacy_top_level_commissioned_metadata() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "REINVEST_ALL_NET_PROFIT"},
        metadata_evidence={
            "state": "READY",
            "authority_metadata": {"commissioned_by": "operator:eric"},
        },
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=None),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    snapshot = payload["items"][0]["campaign_snapshot"]
    assert snapshot["commissioned_metadata_present"] is True
    assert snapshot["commissioned_metadata_shape"] == "legacy_top_level_commissioned_fields"


@pytest.mark.asyncio
async def test_campaign_snapshot_does_not_treat_unrelated_metadata_as_commissioned() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "REINVEST_ALL_NET_PROFIT"},
        metadata_evidence={"dedicated_proving_account": {"paper_account_id": "x"}},
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=None),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    snapshot = payload["items"][0]["campaign_snapshot"]
    assert snapshot["commissioned_metadata_present"] is False
    assert snapshot["commissioned_metadata_shape"] is None


@pytest.mark.asyncio
async def test_campaign_status_and_history_surface_legacy_policy_blocker_without_validation_failure() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={},
        metadata_evidence={"commissioned_seed_campaign": {"state": "READY"}},
        created_at=datetime.now(timezone.utc),
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), updated_at=datetime.now(timezone.utc), uuid=campaign_id)
    asset = SimpleNamespace(id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), symbol="BTC", exchange="kraken_spot", created_at=datetime.now(timezone.utc))
    cycle = SimpleNamespace(
        cycle_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        state="FAILED_CLOSED",
        cycle_kind="campaign",
        capital_campaign_id=campaign_id,
        capital_campaign_version=1,
        started_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 16, 12, 1, tzinfo=timezone.utc),
        termination_stage="policy_validation",
        failure_reason="legacy_compounding_policy_payload",
        deterministic_explanation=[],
        cycle_context={},
    )
    db = _OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=asset, cycles=[cycle])

    status_payload = await fetch_campaign_orchestration_status(db=db, campaign_id=campaign_id, version=1)
    history_payload = await fetch_campaign_orchestration_history(db=db, campaign_id=campaign_id, version=1, limit=50)

    assert status_payload["ready"] is False
    assert status_payload["blockers"] == ["compounding_policy_missing_policy_type"]
    assert status_payload["campaign_snapshot"]["paper_account_id"] == str(runtime.paper_account_id)
    assert history_payload["blockers"] == ["compounding_policy_missing_policy_type"]
    assert history_payload["items"][0]["failure_reason"] == "legacy_compounding_policy_payload"


@pytest.mark.asyncio
async def test_campaign_status_rejects_malformed_compounding_policy_payload_fail_closed() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy="not-a-dict",
        metadata_evidence={},
        created_at=datetime.now(timezone.utc),
    )
    db = _OrchestrationReadOnlyDb(definition=definition, runtime=None, asset=None, cycles=[])

    payload = await fetch_campaign_orchestration_status(db=db, campaign_id=definition.campaign_id, version=1)

    assert payload["ready"] is False
    assert payload["blockers"] == ["compounding_policy_invalid_payload"]


@pytest.mark.asyncio
async def test_worker_preview_ignores_draft_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="DRAFT",
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        campaign_modes=[],
        aggression_mode="BALANCED",
        accounting_state=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        remaining_unallocated_capital=Decimal("1"),
    )

    class _CandleDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.scalar_calls = 0

        async def scalar(self, _statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return SimpleNamespace(id=UUID("12345678-1234-1234-1234-1234567890ab"))
            return SimpleNamespace(open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc))

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        async def commit(self):
            return None

    async def _get_campaign_definition(**_kwargs):
        return campaign

    async def _list_campaign_definitions(**_kwargs):
        return SimpleNamespace(items=[campaign])

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.get_campaign_definition", _get_campaign_definition)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.list_campaign_definitions", _list_campaign_definitions)

    payload = await run_campaign_orchestration_preview_for_candle(db=_CandleDb(), campaign_id=campaign.campaign_id, version=campaign.version, allow_draft_preview=False)
    assert payload["cycle_count"] == 0


@pytest.mark.asyncio
async def test_worker_preview_persists_null_mandate_and_campaign_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle
    from app.models.autonomous_cycle_run import AutonomousCycleRun

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=7,
        status="READY",
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        campaign_modes=[],
        aggression_mode="BALANCED",
        accounting_state=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        remaining_unallocated_capital=Decimal("1"),
    )

    class _CandleDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.scalar_calls = 0
            self.added = None

        async def scalar(self, _statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return SimpleNamespace(id=UUID("12345678-1234-1234-1234-1234567890ab"))
            if self.scalar_calls == 2:
                return SimpleNamespace(asset_id=UUID("12345678-1234-1234-1234-1234567890ab"), open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc))
            return None

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, item):
            self.added = item

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def _get_campaign_definition(**_kwargs):
        return campaign

    async def _list_campaign_definitions(**_kwargs):
        return SimpleNamespace(items=[campaign])

    async def _compose_campaign_authoritative_cycle(**_kwargs):
        return SimpleNamespace(
            composition={
                "failed_closed": False,
                "decision_record_id": "11111111-1111-1111-1111-111111111111",
                "selected_decision": {"decision_kind": "NO_ACTION", "risk_verdict": "NOT_APPLICABLE"},
                "deterministic_explanation": ["stub"],
            },
            preview=SimpleNamespace(model_dump=lambda **_dump_kwargs: {"campaign": "preview"}),
        )

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.get_campaign_definition", _get_campaign_definition)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.list_campaign_definitions", _list_campaign_definitions)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.compose_campaign_authoritative_cycle", _compose_campaign_authoritative_cycle)

    db = _CandleDb()
    payload = await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=campaign.campaign_id, version=campaign.version, allow_draft_preview=False)

    assert payload["cycle_count"] == 1
    assert isinstance(db.added, AutonomousCycleRun)
    assert db.added.mandate_id is None
    assert db.added.mandate_version_id is None
    assert db.added.cycle_kind == "campaign"
    assert db.added.capital_campaign_id == campaign.campaign_id
    assert db.added.capital_campaign_version == campaign.version
    assert str(db.added.decision_record_id) == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_authoritative_open_candidate_selects_best(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = _campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=Decimal("25"),
        start_of_day_equity=Decimal("25"),
        current_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("25"),
        max_drawdown_pct=Decimal("0.10"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"),
        evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.10"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    assert result.composition["risk_outputs"]["BTC-USD"]["risk_event_id"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert result.composition["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result.composition["selected_decision"]["sizing_trace"]["minimum_viable_amount"] == "5"


@pytest.mark.asyncio
async def test_authoritative_risk_veto_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(account_equity=Decimal("25"), start_of_day_equity=Decimal("25"), current_equity=Decimal("25"), max_position_size_pct=Decimal("0.10"), max_daily_loss_pct=Decimal("0.03"), high_water_mark_equity=Decimal("25"), max_drawdown_pct=Decimal("0.10"), consecutive_losses_on_pair=0, cooldown_after_losses=3, last_loss_at=None, cooldown_duration_minutes=Decimal("1440"), evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc), data_is_stale=False, data_has_gaps=False, global_kill_switch_engaged_state=False, global_kill_switch_rearm_required=False, account_kill_switch_engaged_state=False, account_kill_switch_rearm_required=False, global_kill_switch_state_observed=True, account_kill_switch_state_observed=True, risk_policy_source="module_fallback_default")

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.REJECT, reason_code="global_kill_switch_engaged", approved_quantity=Decimal("0"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["risk_outputs"]["BTC-USD"]["verdict"] == "VETO"
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"


@pytest.mark.asyncio
async def test_authoritative_risk_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return({"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("25"),
                start_of_day_equity=Decimal("25"),
                current_equity=Decimal("25"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("25"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("risk engine unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_stale_market_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "STALE", "reason": "stale_market_data", "freshness": "stale"}, None, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_missing_strategy_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((None, "strategy_evidence_unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_scopes_to_trigger_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    visited: list[str] = []

    async def _load_market_evidence(**kwargs):
        visited.append(kwargs["symbol"])
        return ({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _load_market_evidence)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((None, "strategy_evidence_unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert visited == ["BTC-USD"]
    assert result.composition["candidate_instruments"] == ["BTC-USD"]


@pytest.mark.asyncio
async def test_authoritative_strategy_identity_from_metadata_passed_to_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1"},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    captured: dict[str, str | None] = {"preferred": None}

    async def _load_strategy(**kwargs):
        captured["preferred"] = kwargs.get("preferred_strategy_identity")
        return None, "strategy_evidence_unavailable"

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _load_strategy)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert captured["preferred"] == "ma_crossover@1"


@pytest.mark.asyncio
async def test_authoritative_no_action_reason_is_minimum_order_continuity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("1"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("1"),
                start_of_day_equity=Decimal("1"),
                current_equity=Decimal("1"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("1"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_499_rejects_without_risk_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("500"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("4.99"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("500"),
                start_of_day_equity=Decimal("500"),
                current_equity=Decimal("500"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("500"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )

    called = {"risk": False}

    def _risk_should_not_run(**_kwargs):
        called["risk"] = True
        raise AssertionError("risk evaluation must not run when liquid cash is below minimum")

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", _risk_should_not_run)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert called["risk"] is False
    assert result.composition["execution_submitted"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"
    trace = result.composition["selected_decision"]["sizing_trace"]
    assert trace["liquid_cash_cap"] == "4.99"
    assert trace["pre_risk_proposed_amount"] == "4.99"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_500_permits_exact_five(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("500"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("5.00"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("500"),
                start_of_day_equity=Decimal("500"),
                current_equity=Decimal("500"),
                max_position_size_pct=Decimal("1"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("500"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.05"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    trace = result.composition["selected_decision"]["sizing_trace"]
    assert trace["liquid_cash_cap"] == "5.00"
    assert Decimal(trace["campaign_allocation"]) == Decimal("5")
    assert trace["pre_risk_proposed_amount"] == "5.00"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_cap_wins_over_campaign_and_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("500"),
        available_authority=Decimal("500"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("23.7205"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))


@pytest.mark.asyncio
async def test_authoritative_hold_preserves_strategy_evidence_in_preview_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1.0.0"},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("25"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(
        asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        close=Decimal("100"),
        close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
        interval="15m",
        open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
    )
    asset = SimpleNamespace(
        id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        exchange="kraken_spot",
        base_currency="USD",
        min_order_notional=Decimal("5"),
        qty_step_size=None,
        supports_fractional=True,
    )
    market = {
        "authority_class": "AUTHORITATIVE",
        "reason": "market data resolved from canonical asset and candle tables",
        "freshness": "fresh",
        "close_price": "100",
    }
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1.0.0",
        "strategy_version": "1.0.0",
        "action": "HOLD",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": "4.2",
        "expected_value": "4.2",
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "280390e3-180c-4247-8d82-56ab51f463cf"},
    }

    captured: dict[str, object] = {}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    def _capture_preview(*, campaign, request, now):
        captured["strategy_evidence"] = request.strategy_evidence
        captured["lifecycle_snapshots"] = request.lifecycle_snapshots
        captured["risk_preview"] = request.risk_preview
        return SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"})

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", _capture_preview)

    result = await compose_campaign_authoritative_cycle(
        db=_Db(),
        campaign_definition=campaign,
        trigger="kraken_btc_15m_candle_close",
        candle=candle,
    )

    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "strategy_hold_signal"
    assert result.composition["selected_decision"]["decision_record_id"] == "280390e3-180c-4247-8d82-56ab51f463cf"
    assert result.composition["termination_stage"] == "hold_no_package_created"
    assert result.composition["authoritative_evidence"]["strategy_authority"]["authority_source"] == "campaign_metadata_evidence"
    strategy_inputs = captured["strategy_evidence"]
    assert len(strategy_inputs) == 1
    assert strategy_inputs[0].instrument == "BTC-USD"
    assert strategy_inputs[0].authority_class == "AUTHORITATIVE"


@pytest.mark.asyncio
async def test_authoritative_rejected_candidate_preserves_strategy_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1.0.0"},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("1"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1"), current_cash_balance=Decimal("1"))
    candle = SimpleNamespace(
        asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        close=Decimal("100"),
        close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
        interval="15m",
        open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
    )
    asset = SimpleNamespace(
        id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        exchange="kraken_spot",
        base_currency="USD",
        min_order_notional=Decimal("5"),
        qty_step_size=None,
        supports_fractional=True,
    )
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1.0.0",
        "strategy_version": "1.0.0",
        "action": "BUY",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": "4.2",
        "expected_value": "4.2",
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "280390e3-180c-4247-8d82-56ab51f463cf"},
    }
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    captured: dict[str, object] = {}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    def _capture_preview(*, campaign, request, now):
        captured["strategy_evidence"] = request.strategy_evidence
        return SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"})

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("1"),
                start_of_day_equity=Decimal("1"),
                current_equity=Decimal("1"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("1"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", _capture_preview)

    result = await compose_campaign_authoritative_cycle(
        db=_Db(),
        campaign_definition=campaign,
        trigger="kraken_btc_15m_candle_close",
        candle=candle,
    )

    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"
    strategy_inputs = captured["strategy_evidence"]
    assert len(strategy_inputs) == 1
    assert strategy_inputs[0].instrument == "BTC-USD"


@pytest.mark.asyncio
async def test_authoritative_strategy_hold_signal_returns_hold_no_package_created(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("4.33159379773015"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "HOLD", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["termination_stage"] == "hold_no_package_created"
    assert result.composition["proposed_action"] == "HOLD"
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["decision_record_id"] == "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"


@pytest.mark.asyncio
async def test_authoritative_incoherent_strategy_identity_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "donchian_breakout@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "BUY", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["reason"] == "strategy_identity_incoherent"


@pytest.mark.asyncio
async def test_authoritative_historical_package_conflict_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_campaign_strategy_authority", _async_return({"authority_source": "canonical_preview_package_continuity_only", "preferred_strategy_identity": None, "historical_strategy_identity": "donchian_breakout@1.0.0"}))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "BUY", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["reason"] == "strategy_continuity_conflict"


# NOTE: test_latest_strategy_evidence_uses_decision_signal_identity_not_scorecard_best
# and test_latest_strategy_evidence_conflicting_generated_signals_fail_closed were
# removed here. Both asserted internal mechanics of the OLD single-best-scorecard
# + single-DecisionRecord-lookup implementation of _load_latest_strategy_evidence
# (a `best_scorecard` variable and a scan of decision_record.supporting_strategies
# for a match) that no longer exist -- that function is now a governed, deterministic
# multi-strategy aggregator (app/services/strategy_roster/decision_aggregator.py) and
# always produces its own paired DecisionRecord rather than searching for a
# pre-existing one. The principle those tests encoded -- that the strategy evidence
# used for the authoritative decision must come from a governed record, not raw
# unvetted scorecard ranking -- is preserved and re-tested against the new
# implementation in:
#   tests/unit/services/strategy_roster/test_decision_aggregator.py
#     (test_ma_crossover_does_not_automatically_win_against_stronger_evidence)
#   tests/unit/services/capital_campaign_orchestration/test_strategy_decision_aggregator_integration.py


@pytest.mark.asyncio
async def test_market_evidence_15m_freshness_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    close_time = datetime(2026, 7, 16, 16, 30, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=101, interval="15m", close=Decimal("100"), close_time=close_time, open_time=close_time.replace(minute=15))

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload_15, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=15))
    assert payload_15["freshness_verdict"] == "fresh"

    payload_18, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=18))
    assert payload_18["freshness_verdict"] == "fresh"

    payload_20, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=20))
    assert payload_20["freshness_verdict"] == "fresh"
    assert payload_20["maximum_age_minutes"] == 20

    payload_21, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=20, seconds=1))
    assert payload_21["reason"] == "stale_market_data"
    assert payload_21["freshness_verdict"] == "stale"


@pytest.mark.asyncio
async def test_market_evidence_future_candle_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    now = datetime(2026, 7, 16, 16, 48, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=102, interval="15m", close=Decimal("100"), close_time=now + timedelta(minutes=1), open_time=now)

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=now)
    assert payload["reason"] == "stale_market_data"
    assert payload["freshness_verdict"] == "fail_closed_future_timestamp"


@pytest.mark.asyncio
async def test_market_evidence_incomplete_current_candle_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    now = datetime(2026, 7, 16, 16, 48, tzinfo=timezone.utc)

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _no_closed(**_kwargs):
        return None

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _no_closed)

    payload, _, candle = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=now)
    assert candle is None
    assert payload["reason"] == "market_data_unavailable"


@pytest.mark.asyncio
async def test_market_evidence_unrelated_interval_uses_safe_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    close_time = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=103, interval="1m", close=Decimal("100"), close_time=close_time, open_time=close_time - timedelta(minutes=1))

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload_fresh, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="1m", now=close_time + timedelta(minutes=1))
    assert payload_fresh["freshness_verdict"] == "fresh"

    payload_stale, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="1m", now=close_time + timedelta(minutes=2, seconds=1))
    assert payload_stale["reason"] == "stale_market_data"
    assert payload_stale["freshness_verdict"] == "stale"