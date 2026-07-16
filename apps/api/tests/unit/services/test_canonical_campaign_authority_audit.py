from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.canonical_campaign_authority_audit import (
    CanonicalCampaignAuthorityAuditRequest,
    run_canonical_campaign_authority_audit,
)


class _ScalarListResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _ReadOnlyFakeDb:
    def __init__(
        self,
        *,
        definition=None,
        runtime=None,
        paper_account=None,
        live_profile=None,
        asset=None,
        strategy=None,
        parameter_sets=None,
        packages=None,
        linked_package=None,
        cycle=None,
        decision=None,
        decision_snapshot=None,
        risk_events=None,
        preview=None,
        signals=None,
    ) -> None:
        self.definition = definition
        self.runtime = runtime
        self.paper_account = paper_account
        self.live_profile = live_profile
        self.asset = asset
        self.strategy = strategy
        self.parameter_sets = list(parameter_sets or [])
        self.packages = list(packages or [])
        self.linked_package = linked_package
        self.cycle = cycle
        self.decision = decision
        self.decision_snapshot = decision_snapshot
        self.risk_events = dict(risk_events or {})
        self.preview = preview
        self.signals = list(signals or [])
        self.get_calls = []

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is None:
            return None
        name = entity.__name__
        if name == "CapitalCampaignDefinition":
            return self.definition
        if name == "CapitalCampaign":
            return self.runtime
        if name == "Asset":
            return self.asset
        if name == "CanonicalPreviewPackage":
            if self.linked_package is not None:
                return self.linked_package
            return self.packages[0] if self.packages else None
        return None

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is None:
            return _ScalarListResult([])
        name = entity.__name__
        if name == "ParameterSet":
            return _ScalarListResult(self.parameter_sets)
        if name == "CanonicalPreviewPackage":
            return _ScalarListResult(self.packages)
        if name == "Signal":
            return _ScalarListResult(self.signals)
        return _ScalarListResult([])

    async def get(self, model, identifier):
        self.get_calls.append((model.__name__, str(identifier)))
        name = model.__name__
        if name == "PaperAccount":
            return self.paper_account
        if name == "LiveTradingProfile":
            return self.live_profile
        if name == "Strategy":
            return self.strategy
        if name == "AutonomousCycleRun":
            return self.cycle if self.cycle is not None and self.cycle.cycle_id == identifier else None
        if name == "DecisionRecord":
            return self.decision if self.decision is not None and self.decision.decision_id == identifier else None
        if name == "DecisionSnapshot":
            return self.decision_snapshot if self.decision_snapshot is not None and self.decision_snapshot.decision_id == identifier else None
        if name == "RiskEvent":
            return self.risk_events.get(identifier)
        if name == "CryptoOrderPreview":
            return self.preview if self.preview is not None and self.preview.crypto_order_preview_id == identifier else None
        return None

    async def flush(self):
        raise AssertionError("read-only command must not call flush")

    async def commit(self):
        raise AssertionError("read-only command must not call commit")

    def add(self, _obj):
        raise AssertionError("read-only command must not create AuditLog or any write model")


@pytest.mark.asyncio
async def test_authority_audit_returns_exact_cycle_linkage_and_no_writes() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    cycle_id = UUID("ce8c5594-c39e-4634-945c-66ef0395a7c3")
    paper_account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    live_profile_id = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")

    linked_risk_event_id = uuid4()
    unrelated_recent_risk_event_id = uuid4()
    decision_id = uuid4()
    preview_id = uuid4()
    strategy_id = uuid4()
    parameter_set_id = uuid4()
    signal_id = uuid4()

    now = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)

    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        status="READY",
        allowed_asset_classes=["crypto"],
        allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"],
        campaign_modes=["COMPOUND"],
        metadata_evidence={"canonical_strategy_identity": "momentum_v1@1.2.0"},
        risk_policy_id="risk_policy_default",
        risk_policy_version="1",
        profitability_policy_id="profit_policy_default",
        profitability_policy_version="1",
        capital_budget=Decimal("5.00000000"),
        minimum_position_size=Decimal("5.00000000"),
        maximum_position_size=Decimal("5.00000000"),
        maximum_total_exposure=Decimal("5.00000000"),
        remaining_unallocated_capital=Decimal("5.00000000"),
    )
    runtime = SimpleNamespace(
        id=11,
        uuid=campaign_id,
        definition_campaign_id=campaign_id,
        definition_version=1,
        status="READY",
        paper_account_id=paper_account_id,
        exchange="kraken_spot",
        strategy_id=strategy_id,
        starting_capital=Decimal("25.0"),
        current_equity=Decimal("25.3"),
        realized_profit=Decimal("0.35"),
        fees=Decimal("0.04"),
        created_at=now,
        updated_at=now,
    )
    paper_account = SimpleNamespace(
        id=paper_account_id,
        asset_class="crypto",
        is_active=True,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("23.81"),
        created_at=now,
    )
    live_profile = SimpleNamespace(
        id=live_profile_id,
        paper_account_id=paper_account_id,
        operating_mode="live",
        lifecycle_state="enabled",
        approval_state="approved",
        provenance_metadata={"provider": "kraken_spot", "exchange_environment": "production"},
        created_at=now,
        updated_at=now,
    )
    asset = SimpleNamespace(
        id=uuid4(),
        symbol="BTC",
        base_currency="USD",
        exchange="kraken_spot",
        is_active=True,
        supports_fractional=True,
        min_order_notional=Decimal("5"),
        qty_step_size=Decimal("0.00001"),
        created_at=now,
    )
    strategy = SimpleNamespace(
        id=strategy_id,
        slug="momentum_v1",
        name="Momentum",
        module_version="1.2.0",
        is_active=True,
        created_at=now,
    )
    parameter_set = SimpleNamespace(
        id=parameter_set_id,
        strategy_id=strategy_id,
        label="primary",
        created_by="ops",
        created_at=now,
    )
    linked_package = SimpleNamespace(
        package_id=uuid4(),
        package_state="READY",
        strategy_id=strategy_id,
        strategy_version="1.2.0",
        parameter_set_id=parameter_set_id,
        parameter_set_version="3",
        decision_record_id=decision_id,
        risk_event_id=linked_risk_event_id,
        crypto_order_preview_id=preview_id,
        generated_at=now,
        created_at=now,
        updated_at=now,
    )
    older_package = SimpleNamespace(
        package_id=uuid4(),
        package_state="FAILED_CLOSED",
        strategy_id=strategy_id,
        strategy_version="1.1.0",
        parameter_set_id=parameter_set_id,
        parameter_set_version="2",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        generated_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
    )
    cycle = SimpleNamespace(
        cycle_id=cycle_id,
        capital_campaign_id=campaign_id,
        capital_campaign_version=1,
        state="FAILED_CLOSED",
        evaluation_stage="campaign_authoritative_preview",
        termination_stage="failed_closed",
        failure_reason="asset_mapping_unavailable",
        proposed_action="FAILED_CLOSED",
        decision_record_id=decision_id,
        risk_event_id=linked_risk_event_id,
        preview_id=preview_id,
        cycle_context={
            "authoritative_composition": {
                "selected_decision": {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": "asset_mapping_unavailable"},
                "authoritative_evidence": {
                    "strategy": {"BTC-USD": {"strategy_identity": "momentum_v1@1.2.0"}},
                    "strategy_authority": {"authority_source": "campaign_metadata_evidence"},
                },
                "eligible_candidates": [],
                "rejected_candidates": [{"instrument": "BTC-USD", "reason": "missing_strategy_evidence"}],
                "ranked_candidates": [],
            }
        },
        diagnostics={"status": "complete"},
        deterministic_explanation=["trigger=kraken_btc_15m_candle_close", "candidates=0"],
        started_at=now,
        completed_at=now,
    )
    decision = SimpleNamespace(
        decision_id=decision_id,
        timestamp=now,
        timeframe="15m",
        trade_accepted=False,
        trade_rejected_reason="risk_rejected",
        asset={"symbol": "BTC-USD"},
        source_lineage={"signals": [str(signal_id)]},
    )
    decision_snapshot = SimpleNamespace(
        decision_id=decision_id,
        strategy_version="1.2.0",
        parameter_set_version="3",
        timestamp=now,
    )
    linked_risk_event = SimpleNamespace(
        id=linked_risk_event_id,
        paper_account_id=paper_account_id,
        related_signal_id=signal_id,
        event_type="risk_evaluation",
        action_taken="rejected",
        detail={"reason": "position_below_minimum_order_size"},
        created_at=now,
    )
    preview = SimpleNamespace(
        crypto_order_preview_id=preview_id,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        status="rejected",
        readiness_verdict="not_ready",
        decision_record_id=decision_id,
        risk_event_id=linked_risk_event_id,
        strategy_id=strategy_id,
        parameter_set_id=parameter_set_id,
        requested_amount=Decimal("5"),
        estimated_fee=Decimal("0.01"),
        created_at=now,
    )
    signal = SimpleNamespace(
        id=signal_id,
        strategy_id=strategy_id,
        parameter_set_id=parameter_set_id,
        asset_id=asset.id,
        signal_time=now,
        action="buy",
        status="risk_rejected",
        raw_strength=Decimal("0.77"),
        ai_confidence=Decimal("0.82"),
        regime_tag="trend",
        created_at=now,
    )

    db = _ReadOnlyFakeDb(
        definition=definition,
        runtime=runtime,
        paper_account=paper_account,
        live_profile=live_profile,
        asset=asset,
        strategy=strategy,
        parameter_sets=[parameter_set],
        packages=[linked_package, older_package],
        linked_package=linked_package,
        cycle=cycle,
        decision=decision,
        decision_snapshot=decision_snapshot,
        risk_events={linked_risk_event_id: linked_risk_event, unrelated_recent_risk_event_id: SimpleNamespace(id=unrelated_recent_risk_event_id)},
        preview=preview,
        signals=[signal],
    )

    request = CanonicalCampaignAuthorityAuditRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        cycle_id=cycle_id,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
    )

    payload = await run_canonical_campaign_authority_audit(db=db, request=request)

    assert payload["command"] == "canonical-campaign-authority-audit"
    assert payload["target_cycle"]["cycle_id"] == str(cycle_id)
    assert payload["cycle_linked_evidence"]["risk_event"]["risk_event_id"] == str(linked_risk_event_id)
    assert payload["cycle_linked_evidence"]["canonical_package"]["decision_record_id"] == str(decision_id)
    assert payload["cycle_linked_evidence"]["decision_snapshot"]["strategy_version"] == "1.2.0"
    assert payload["canonical_packages"]["count"] == 2
    assert payload["strategy_authority"]["historical_continuity_only"] is True
    assert all(name not in {"AuditLog", "ExchangeConnection"} for name, _ in db.get_calls)


@pytest.mark.asyncio
async def test_authority_audit_missing_optional_records_returns_null_and_empty_sections() -> None:
    cycle_id = uuid4()
    campaign_id = uuid4()
    db = _ReadOnlyFakeDb(
        definition=None,
        runtime=None,
        paper_account=None,
        live_profile=None,
        asset=None,
        strategy=None,
        parameter_sets=[],
        packages=[],
        linked_package=None,
        cycle=SimpleNamespace(
            cycle_id=cycle_id,
            capital_campaign_id=campaign_id,
            capital_campaign_version=1,
            state="FAILED_CLOSED",
            evaluation_stage="campaign_authoritative_preview",
            termination_stage="failed_closed",
            failure_reason="missing_strategy_evidence",
            proposed_action="FAILED_CLOSED",
            decision_record_id=None,
            risk_event_id=None,
            preview_id=None,
            cycle_context={},
            diagnostics={},
            deterministic_explanation=[],
            started_at=datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
            completed_at=None,
        ),
        decision=None,
        decision_snapshot=None,
        risk_events={},
        preview=None,
        signals=[],
    )
    payload = await run_canonical_campaign_authority_audit(
        db=db,
        request=CanonicalCampaignAuthorityAuditRequest(
            campaign_id=campaign_id,
            campaign_version=1,
            cycle_id=cycle_id,
            paper_account_id=uuid4(),
            live_trading_profile_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
        ),
    )

    assert payload["campaign_definition"] is None
    assert payload["runtime_campaign"] is None
    assert payload["paper_account"] is None
    assert payload["live_trading_profile"] is None
    assert payload["asset_mapping"] is None
    assert payload["cycle_linked_evidence"]["decision_record"] is None
    assert payload["cycle_linked_evidence"]["risk_event"] is None
    assert payload["cycle_linked_evidence"]["crypto_order_preview"] is None
    assert payload["cycle_linked_evidence"]["canonical_package"] is None
    assert payload["cycle_linked_evidence"]["signals"] == []
    assert payload["canonical_packages"]["items"] == []


@pytest.mark.asyncio
async def test_authority_audit_is_deterministic_for_identical_inputs() -> None:
    cycle_id = uuid4()
    campaign_id = uuid4()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    cycle = SimpleNamespace(
        cycle_id=cycle_id,
        capital_campaign_id=campaign_id,
        capital_campaign_version=1,
        state="COMPLETE",
        evaluation_stage="campaign_authoritative_preview",
        termination_stage="hold_terminal",
        failure_reason=None,
        proposed_action="NO_ACTION",
        decision_record_id=None,
        risk_event_id=None,
        preview_id=None,
        cycle_context={"authoritative_composition": {}},
        diagnostics={"status": "complete"},
        deterministic_explanation=["candidates=0"],
        started_at=now,
        completed_at=now,
    )
    db = _ReadOnlyFakeDb(cycle=cycle)
    request = CanonicalCampaignAuthorityAuditRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        cycle_id=cycle_id,
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
    )

    first = await run_canonical_campaign_authority_audit(db=db, request=request)
    second = await run_canonical_campaign_authority_audit(db=db, request=request)

    assert first == second


@pytest.mark.asyncio
async def test_authority_audit_rejects_invalid_environment() -> None:
    with pytest.raises(ValueError, match="unsupported exchange environment"):
        await run_canonical_campaign_authority_audit(
            db=_ReadOnlyFakeDb(cycle=SimpleNamespace(cycle_id=uuid4())),
            request=CanonicalCampaignAuthorityAuditRequest(
                campaign_id=uuid4(),
                campaign_version=1,
                cycle_id=uuid4(),
                paper_account_id=uuid4(),
                live_trading_profile_id=uuid4(),
                provider="kraken_spot",
                environment="invalid",
                product="BTC-USD",
            ),
        )
