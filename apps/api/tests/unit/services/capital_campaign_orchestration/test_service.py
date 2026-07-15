from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.capital_campaign_orchestration.service import build_campaign_orchestration_idempotency_key, fetch_campaign_orchestration_readiness
from app.services.risk import RiskDecisionAction, RiskEvaluationResult


class _FakeDb:
    async def scalar(self, _statement):
        return None

    async def execute(self, _statement):
        raise AssertionError("unexpected execute call in readiness test")


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
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
    )

    async def _get_campaign_definition(**_kwargs):
        return campaign

    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.service.get_campaign_definition",
        _get_campaign_definition,
    )

    payload = await fetch_campaign_orchestration_readiness(db=_FakeDb(), campaign_id=campaign.campaign_id, version=campaign.version)
    assert payload["items"][0]["ready"] is True
    assert payload["items"][0]["allows_draft_preview"] is True


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
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00"}
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


@pytest.mark.asyncio
async def test_authoritative_risk_veto_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00"}
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
    assert result.composition["selected_decision"]["decision_kind"] == "NO_ACTION"


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
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_strategy_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00"}, None)))
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