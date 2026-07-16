from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import inspect
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.canonical_paper_cash_causality_audit import (
    CanonicalPaperCashCausalityAuditRequest,
    run_canonical_paper_cash_causality_audit,
)


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(
        self,
        *,
        definition,
        runtime,
        paper_account,
        profile,
        trades,
        assets,
        campaign_cycles,
        autonomous_cycles,
        decisions,
        live_orders=None,
        reconciliation_events=None,
        accounting_records=None,
        execution_events=None,
    ):
        self.definition = definition
        self.runtime = runtime
        self.paper_account = paper_account
        self.profile = profile
        self.trades = list(trades)
        self.assets = list(assets)
        self.campaign_cycles = list(campaign_cycles)
        self.autonomous_cycles = list(autonomous_cycles)
        self.decisions = list(decisions)
        self.live_orders = list(live_orders or [])
        self.reconciliation_events = list(reconciliation_events or [])
        self.accounting_records = list(accounting_records or [])
        self.execution_events = list(execution_events or [])
        self.candle_close_by_asset = {item.id: Decimal("60000") for item in assets}

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is not None:
            name = entity.__name__
            if name == "CapitalCampaignDefinition":
                return self.definition
            if name == "CapitalCampaign":
                return self.runtime
            return None
        # scalar column requests such as Candle.close
        sql = str(stmt)
        if "FROM candles" in sql:
            params = stmt.compile().params
            asset_id = params.get("asset_id_1") or params.get("asset_id")
            return self.candle_close_by_asset.get(asset_id)
        return None

    async def get(self, model, identifier):
        name = model.__name__
        if name == "PaperAccount":
            return self.paper_account
        if name == "LiveTradingProfile":
            return self.profile
        return None

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is None:
            return _ScalarListResult([])
        name = entity.__name__
        if name == "Trade":
            return _ScalarListResult(self.trades)
        if name == "Asset":
            return _ScalarListResult(self.assets)
        if name == "LiveCryptoOrder":
            return _ScalarListResult(self.live_orders)
        if name == "LiveReconciliationEvent":
            return _ScalarListResult(self.reconciliation_events)
        if name == "LiveAccountingRecord":
            return _ScalarListResult(self.accounting_records)
        if name == "LiveExecutionEvent":
            return _ScalarListResult(self.execution_events)
        if name == "CapitalCampaign":
            return _ScalarListResult([self.runtime])
        if name == "AutonomousCycleRun":
            sql = str(stmt)
            if "cycle_kind =" in sql and "campaign" in sql:
                return _ScalarListResult(self.campaign_cycles)
            return _ScalarListResult(self.autonomous_cycles)
        if name == "DecisionRecord":
            return _ScalarListResult(self.decisions)
        return _ScalarListResult([])

    async def flush(self):
        raise AssertionError("read-only audit must not call flush")

    async def commit(self):
        raise AssertionError("read-only audit must not call commit")

    def add(self, _obj):
        raise AssertionError("read-only audit must not add rows")


@pytest.mark.asyncio
async def test_causality_audit_reconstructs_cash_and_marks_wrong_account_when_noncanonical_history() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    asset_id = uuid4()
    other_campaign_id = uuid4()

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        minimum_position_size=Decimal("5"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
    )
    runtime = SimpleNamespace(
        id=2,
        uuid=campaign_id,
        status="READY",
        paper_account_id=paper_account_id,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        fees=Decimal("0"),
        definition_campaign_id=campaign_id,
        definition_version=1,
        created_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("4.33159379773015"),
        asset_class="crypto",
        is_active=True,
    )
    profile = SimpleNamespace(id=profile_id)

    trades = [
        SimpleNamespace(
            id=uuid4(),
            paper_account_id=paper_account_id,
            signal_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.00032"),
            price=Decimal("64500"),
            fee=Decimal("0.02840620226985"),
            execution_venue="paper",
            executed_at=now,
        ),
        SimpleNamespace(
            id=uuid4(),
            paper_account_id=paper_account_id,
            signal_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            asset_id=asset_id,
            side="sell",
            quantity=Decimal("0.00032"),
            price=Decimal("0"),
            fee=Decimal("0"),
            execution_venue="paper",
            executed_at=now.replace(minute=1),
        ),
    ]
    assets = [
        SimpleNamespace(
            id=asset_id,
            symbol="BTC",
            exchange="kraken_spot",
            base_currency="USD",
            min_order_notional=Decimal("5"),
            qty_step_size=Decimal("0.00001"),
            supports_fractional=True,
        )
    ]

    campaign_cycles = [
        SimpleNamespace(
            cycle_id=uuid4(),
            cycle_kind="campaign",
            capital_campaign_id=other_campaign_id,
            decision_record_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            started_at=now,
        )
    ]
    autonomous_cycles = []
    decisions = [
        SimpleNamespace(
            decision_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            source_lineage={"signals": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]},
        )
    ]

    db = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=profile,
        trades=trades,
        assets=assets,
        campaign_cycles=campaign_cycles,
        autonomous_cycles=autonomous_cycles,
        decisions=decisions,
    )

    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )

    assert payload["cash_reconstruction"]["reconstructed_cash_after_trades"] == "4.33159379773015"
    assert payload["paper_account"]["difference_from_starting_balance"] == "20.66840620226985"
    assert payload["risk_sizing_authority"]["authoritative_source"] == "paper_account_liquid_cash_hard_cap"
    assert payload["risk_sizing_authority"]["can_support_exact_5"] is False
    assert payload["outcome"]["code"] == "WRONG_ACCOUNT_BOUND"


@pytest.mark.asyncio
async def test_causality_audit_marks_accounting_stale_when_reconstruction_mismatch() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        minimum_position_size=Decimal("5"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
    )
    runtime = SimpleNamespace(
        id=2,
        uuid=campaign_id,
        status="READY",
        paper_account_id=paper_account_id,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        fees=Decimal("0"),
        definition_campaign_id=campaign_id,
        definition_version=1,
        created_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("20"),
        asset_class="crypto",
        is_active=True,
    )
    asset_id = uuid4()

    trades = [
        SimpleNamespace(
            id=uuid4(),
            paper_account_id=paper_account_id,
            signal_id=None,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.0001"),
            price=Decimal("1000"),
            fee=Decimal("0"),
            execution_venue="paper",
            executed_at=now,
        ),
        SimpleNamespace(
            id=uuid4(),
            paper_account_id=paper_account_id,
            signal_id=None,
            asset_id=asset_id,
            side="sell",
            quantity=Decimal("0.0001"),
            price=Decimal("0"),
            fee=Decimal("0"),
            execution_venue="paper",
            executed_at=now.replace(minute=1),
        ),
    ]
    assets = [
        SimpleNamespace(
            id=trades[0].asset_id,
            symbol="BTC",
            exchange="kraken_spot",
            base_currency="USD",
            min_order_notional=Decimal("5"),
            qty_step_size=Decimal("0.00001"),
            supports_fractional=True,
        )
    ]

    db = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=SimpleNamespace(id=profile_id),
        trades=trades,
        assets=assets,
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )

    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )

    assert payload["cash_reconstruction"]["reconstruction_delta"] == "-4.9000"
    assert payload["outcome"]["code"] == "ACCOUNTING_IS_STALE"


@pytest.mark.asyncio
async def test_causality_audit_open_exposure_marks_reserved_or_open_exposure() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    asset_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        minimum_position_size=Decimal("5"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
    )
    runtime = SimpleNamespace(
        id=2,
        uuid=campaign_id,
        status="READY",
        paper_account_id=paper_account_id,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        fees=Decimal("0"),
        definition_campaign_id=campaign_id,
        definition_version=1,
        created_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("24.5"),
        asset_class="crypto",
        is_active=True,
    )
    trades = [
        SimpleNamespace(
            id=uuid4(),
            paper_account_id=paper_account_id,
            signal_id=None,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.0001"),
            price=Decimal("5000"),
            fee=Decimal("0"),
            execution_venue="paper",
            executed_at=now,
        )
    ]
    assets = [
        SimpleNamespace(
            id=asset_id,
            symbol="BTC",
            exchange="kraken_spot",
            base_currency="USD",
            min_order_notional=Decimal("5"),
            qty_step_size=Decimal("0.00001"),
            supports_fractional=True,
        )
    ]

    db = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=SimpleNamespace(id=profile_id),
        trades=trades,
        assets=assets,
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )

    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )

    assert Decimal(payload["cash_reconstruction"]["reconstruction_delta"]) == Decimal("0.5")
    assert payload["exposure"]["open_position_count"] == 1
    assert payload["outcome"]["code"] == "RESERVED_OR_OPEN_EXPOSURE"


@pytest.mark.asyncio
async def test_causality_audit_pending_order_or_unresolved_reconciliation_marks_reserved_or_open_exposure() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        minimum_position_size=Decimal("5"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
    )
    runtime = SimpleNamespace(
        id=2,
        uuid=campaign_id,
        status="READY",
        paper_account_id=paper_account_id,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        fees=Decimal("0"),
        definition_campaign_id=campaign_id,
        definition_version=1,
        created_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        asset_class="crypto",
        is_active=True,
    )

    live_order = SimpleNamespace(
        live_crypto_order_id=uuid4(),
        status="OPEN",
        product_id="BTC-USD",
        side="buy",
        requested_quote_size=Decimal("5"),
        provider_order_id="provider-order-1",
        created_at=now,
        decision_record_id=None,
        risk_event_id=None,
    )
    recon_event = SimpleNamespace(
        id=uuid4(),
        reconciliation_status="reconciliation_required",
        provider_order_id="provider-order-1",
        recorded_at=now,
    )

    db = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[live_order],
        reconciliation_events=[recon_event],
    )

    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )

    assert payload["exposure"]["pending_order_count"] == 1
    assert payload["exposure"]["unresolved_reconciliation_count"] == 1
    assert payload["outcome"]["code"] == "RESERVED_OR_OPEN_EXPOSURE"


@pytest.mark.asyncio
async def test_causality_audit_is_deterministic_for_same_inputs() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        minimum_position_size=Decimal("5"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
    )
    runtime = SimpleNamespace(
        id=2,
        uuid=campaign_id,
        status="READY",
        paper_account_id=paper_account_id,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        fees=Decimal("0"),
        definition_campaign_id=campaign_id,
        definition_version=1,
        created_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
        asset_class="crypto",
        is_active=True,
    )

    request = CanonicalPaperCashCausalityAuditRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        runtime_campaign_id=2,
        paper_account_id=paper_account_id,
        live_trading_profile_id=profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
    )

    db1 = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )
    db2 = _FakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )

    payload_1 = await run_canonical_paper_cash_causality_audit(db=db1, request=request)
    payload_2 = await run_canonical_paper_cash_causality_audit(db=db2, request=request)

    assert json.dumps(payload_1, sort_keys=True) == json.dumps(payload_2, sort_keys=True)


@pytest.mark.asyncio
async def test_causality_audit_incomplete_history_returns_unproven() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("20"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["outcome"]["code"] == "UNPROVEN"
    assert "missing_ledger_history" in " ".join(payload["cash_reconstruction"]["missing_evidence"])


@pytest.mark.asyncio
async def test_causality_audit_unknown_provider_state_returns_unproven() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    live_order = SimpleNamespace(
        live_crypto_order_id=uuid4(),
        status="RECONCILIATION_REQUIRED",
        product_id="BTC-USD",
        side="buy",
        requested_quote_size=Decimal("5"),
        provider_order_id=None,
        created_at=now,
        decision_record_id=None,
        risk_event_id=None,
    )
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[live_order],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["provider_state"]["unknown_provider_state"] is True
    assert payload["outcome"]["code"] == "UNPROVEN"


@pytest.mark.asyncio
async def test_causality_audit_complete_matching_reconstruction_returns_balance_is_correct() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["cash_reconstruction"]["reconstruction_completeness"] is True
    assert payload["outcome"]["code"] == "BALANCE_IS_CORRECT"


@pytest.mark.asyncio
async def test_causality_audit_quantified_reserved_capital_is_included() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    live_order = SimpleNamespace(
        live_crypto_order_id=uuid4(),
        status="OPEN",
        product_id="BTC-USD",
        side="buy",
        requested_quote_size=Decimal("5"),
        provider_order_id="po-1",
        created_at=now,
        decision_record_id=None,
        risk_event_id=None,
    )
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("2"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("18"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[live_order],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["reserved_capital"]["total_reserved_amount"] == "7"
    assert payload["reserved_capital"]["unknown_unquantified_reservation"] is False


@pytest.mark.asyncio
async def test_causality_audit_unquantified_reservation_returns_unproven() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    live_order = SimpleNamespace(
        live_crypto_order_id=uuid4(),
        status="OPEN",
        product_id="BTC-USD",
        side="buy",
        requested_quote_size=None,
        provider_order_id="po-1",
        created_at=now,
        decision_record_id=None,
        risk_event_id=None,
    )
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[live_order],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["reserved_capital"]["unknown_unquantified_reservation"] is True
    assert payload["outcome"]["code"] == "UNPROVEN"


@pytest.mark.asyncio
async def test_causality_audit_reports_archived_legacy_and_unrelated_campaign_usage() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    legacy_id = UUID("f1e8a655-70ee-47f3-8e9e-89c8735b6542")
    unrelated_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    asset_id = uuid4()
    sig_legacy = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    sig_other = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    decision_legacy = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    decision_other = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[
            SimpleNamespace(id=uuid4(), paper_account_id=paper_account_id, signal_id=sig_legacy, asset_id=asset_id, side="buy", quantity=Decimal("0"), price=Decimal("0"), fee=Decimal("0"), execution_venue="paper", executed_at=now),
            SimpleNamespace(id=uuid4(), paper_account_id=paper_account_id, signal_id=sig_other, asset_id=asset_id, side="buy", quantity=Decimal("0"), price=Decimal("0"), fee=Decimal("0"), execution_venue="paper", executed_at=now),
        ],
        assets=[SimpleNamespace(id=asset_id, symbol="BTC", exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=Decimal("0.00001"), supports_fractional=True)],
        campaign_cycles=[
            SimpleNamespace(cycle_id=uuid4(), cycle_kind="campaign", capital_campaign_id=legacy_id, decision_record_id=decision_legacy, started_at=now),
            SimpleNamespace(cycle_id=uuid4(), cycle_kind="campaign", capital_campaign_id=unrelated_id, decision_record_id=decision_other, started_at=now),
        ],
        autonomous_cycles=[],
        decisions=[
            SimpleNamespace(decision_id=decision_legacy, source_lineage={"signals": [str(sig_legacy)]}),
            SimpleNamespace(decision_id=decision_other, source_lineage={"signals": [str(sig_other)]}),
        ],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["ownership"]["summary"]["archived_legacy_trade_count"] == 1
    assert payload["ownership"]["summary"]["unrelated_campaign_trade_count"] == 1


@pytest.mark.asyncio
async def test_causality_audit_returns_exact_execution_events_and_excludes_unrelated() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    live_order_id = uuid4()
    related_event = SimpleNamespace(
        id=uuid4(),
        event_type="execution_intent_created",
        provider_name="kraken_spot",
        recorded_at=now,
        event_payload={"live_crypto_order_id": str(live_order_id), "product_id": "BTC-USD", "amount": "5"},
        provenance={"campaign_uuid": str(campaign_id)},
    )
    unrelated_event = SimpleNamespace(
        id=uuid4(),
        event_type="execution_intent_created",
        provider_name="kraken_spot",
        recorded_at=now,
        event_payload={"live_crypto_order_id": str(uuid4()), "product_id": "ETH-USD", "amount": "5"},
        provenance={"campaign_uuid": str(uuid4())},
    )
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[SimpleNamespace(live_crypto_order_id=live_order_id, status="OPEN", product_id="BTC-USD", side="buy", requested_quote_size=Decimal("5"), provider_order_id="po-1", created_at=now, decision_record_id=None, risk_event_id=None)],
        execution_events=[related_event, unrelated_event],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["execution_events"]["count"] == 1
    assert payload["execution_events"]["events"][0]["live_crypto_order_id"] == str(live_order_id)


@pytest.mark.asyncio
async def test_causality_audit_verdict_precedence_is_deterministic_unproven_first() -> None:
    campaign_id = uuid4()
    paper_account_id = uuid4()
    profile_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    db = _FakeDb(
        definition=SimpleNamespace(
            campaign_id=campaign_id,
            version=1,
            capital_budget=Decimal("25"),
            remaining_unallocated_capital=Decimal("25"),
            minimum_position_size=Decimal("5"),
            maximum_position_size=Decimal("5"),
            maximum_total_exposure=Decimal("5"),
            allowed_instruments=["BTC-USD"],
            reserved_capital=Decimal("0"),
        ),
        runtime=SimpleNamespace(
            id=2,
            uuid=campaign_id,
            status="READY",
            paper_account_id=paper_account_id,
            starting_capital=Decimal("25"),
            current_equity=Decimal("25"),
            realized_profit=Decimal("0"),
            fees=Decimal("0"),
            definition_campaign_id=campaign_id,
            definition_version=1,
            created_at=now,
        ),
        paper_account=SimpleNamespace(
            id=paper_account_id,
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("20"),
            asset_class="crypto",
            is_active=True,
        ),
        profile=SimpleNamespace(id=profile_id),
        trades=[],
        assets=[],
        campaign_cycles=[],
        autonomous_cycles=[],
        decisions=[],
        live_orders=[SimpleNamespace(live_crypto_order_id=uuid4(), status="RECONCILIATION_REQUIRED", product_id="BTC-USD", side="buy", requested_quote_size=Decimal("5"), provider_order_id=None, created_at=now, decision_record_id=None, risk_event_id=None)],
    )
    payload = await run_canonical_paper_cash_causality_audit(
        db=db,
        request=CanonicalPaperCashCausalityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            runtime_campaign_id=2,
            paper_account_id=paper_account_id,
            live_trading_profile_id=profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )
    assert payload["outcome"]["code"] == "UNPROVEN"
    assert payload["outcome"]["precedence"][0] == "UNPROVEN"


def test_causality_audit_source_contains_no_auditlog_creation_path() -> None:
    source = inspect.getsource(run_canonical_paper_cash_causality_audit)
    assert "AuditLog" not in source
    assert ".add(" not in source


def test_causality_audit_source_contains_no_provider_submission_call_path() -> None:
    source = inspect.getsource(run_canonical_paper_cash_causality_audit)
    assert "create_order(" not in source
    assert "submit_order" not in source
