from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import app.operator_cli.service as service


_DECISION_ID = UUID("939b4ea0-4d25-4970-8eff-0b7596c7557d")
_CAMPAIGN_ID = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
_PAPER_ACCOUNT_ID = UUID("8e76a2fa-ae85-45c6-95d1-798cce8f8cc9")
_PROFILE_ID = UUID("9da09ae9-475e-41e8-b2c2-717ba5acfa3d")
_SIGNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
_RISK_ID = UUID("22222222-2222-2222-2222-222222222222")


def _decision(*, action: str = "BUY", confidence: Decimal = Decimal("1.0"), trade_accepted: bool = True, source_lineage: dict | None = None, fee_edge: Decimal = Decimal("0.30"), strategy_identity: str = "ma_crossover@1.0.0", pnl: dict | None = None):
    return SimpleNamespace(
        decision_id=_DECISION_ID,
        timestamp=datetime(2026, 7, 16, 13, 59, tzinfo=timezone.utc),
        generated_signals=[{"action": action.lower()}],
        confidence=confidence,
        source_lineage=source_lineage if source_lineage is not None else {
            "signals": [str(_SIGNAL_ID)],
            "risk_events": [str(_RISK_ID)],
            "trades": [],
        },
        supporting_strategies=[
            {
                "strategy_identity": strategy_identity,
                "strategy_version": strategy_identity,
                "expected_gross_edge": str(fee_edge + Decimal("0.10")),
                "expected_fees": "0.05",
                "expected_slippage": "0.05",
            }
        ],
        trade_accepted=trade_accepted,
        trade_rejected_reason=None if trade_accepted else "rejected",
        asset={"symbol": "BTC-USD"},
        execution_details={"status": "accepted"},
        pnl=pnl,
    )


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime(2026, 7, 16, 13, 59, tzinfo=timezone.utc),
        asset={"symbol": "BTC-USD"},
        timeframe="15m",
    )


def _signal() -> SimpleNamespace:
    return SimpleNamespace(id=_SIGNAL_ID, action="buy", status="accepted")


def _risk_event(*, action_taken: str = "allow") -> SimpleNamespace:
    return SimpleNamespace(id=_RISK_ID, action_taken=action_taken, event_type="risk_check")


def _definition(*, allowlist: list[str] | None = None, min_size: str = "5", max_size: str = "5", max_exposure: str = "5") -> SimpleNamespace:
    metadata = {}
    if allowlist is not None:
        metadata["authorized_strategy_identities"] = allowlist
    return SimpleNamespace(
        campaign_id=_CAMPAIGN_ID,
        version=1,
        aggression_mode="MAXIMUM_GOVERNED",
        maximum_open_positions=1,
        minimum_position_size=Decimal(min_size),
        maximum_position_size=Decimal(max_size),
        maximum_total_exposure=Decimal(max_exposure),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        metadata_evidence=metadata,
    )


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        uuid=_CAMPAIGN_ID,
        definition_version=1,
        paper_account_id=_PAPER_ACCOUNT_ID,
    )


def _profile() -> SimpleNamespace:
    return SimpleNamespace(id=_PROFILE_ID, paper_account_id=_PAPER_ACCOUNT_ID)


def _payload(**kwargs):
    return service._historical_buy_campaign_replay_audit_payload(
        decision_id=_DECISION_ID,
        campaign_id=_CAMPAIGN_ID,
        campaign_version=1,
        runtime_campaign_id=2,
        paper_account_id=_PAPER_ACCOUNT_ID,
        live_trading_profile_id=_PROFILE_ID,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        decision=kwargs.get("decision", _decision()),
        snapshot=kwargs.get("snapshot", _snapshot()),
        signal=kwargs.get("signal", _signal()),
        risk_event=kwargs.get("risk_event", _risk_event()),
        definition=kwargs.get("definition", _definition()),
        latest_version=kwargs.get("latest_version", 1),
        runtime=kwargs.get("runtime", _runtime()),
        paper_account=kwargs.get("paper_account", SimpleNamespace(id=_PAPER_ACCOUNT_ID)),
        profile=kwargs.get("profile", _profile()),
        open_live_order_count=kwargs.get("open_live_order_count", 0),
        matching_sell_decision=kwargs.get("matching_sell_decision"),
        matching_sell_decision_id=kwargs.get("matching_sell_decision_id"),
    )


def test_ready_package_eligible_when_all_gates_pass() -> None:
    payload = _payload(definition=_definition(allowlist=["ma_crossover@1.0.0"]))
    assert payload["primary_blocker"] == "READY_PACKAGE_ELIGIBLE"
    assert payload["current_campaign_simulation"]["campaign_replay_outcome"] == "READY_PACKAGE_ELIGIBLE"


def test_non_buy_returns_decision_not_buy() -> None:
    payload = _payload(decision=_decision(action="SELL"))
    assert payload["primary_blocker"] == "DECISION_NOT_BUY"


def test_missing_decision_fails_closed() -> None:
    payload = _payload(decision=None, snapshot=None, signal=None, risk_event=None)
    assert payload["primary_blocker"] == "DECISION_RECORD_MISSING"


def test_incomplete_lineage_detected() -> None:
    payload = _payload(decision=_decision(source_lineage={"signals": [], "risk_events": []}))
    assert payload["primary_blocker"] == "SOURCE_LINEAGE_INCOMPLETE"


def test_strategy_incompatibility_detected() -> None:
    payload = _payload(definition=_definition(allowlist=["different_strategy@1.0.0"]))
    assert payload["primary_blocker"] == "STRATEGY_NOT_AUTHORIZED"


def test_confidence_rejection_detected() -> None:
    payload = _payload(decision=_decision(confidence=Decimal("0.10")))
    assert payload["primary_blocker"] == "CONFIDENCE_BELOW_THRESHOLD"


def test_risk_rejection_detected() -> None:
    payload = _payload(risk_event=_risk_event(action_taken="veto"))
    assert payload["primary_blocker"] == "RISK_REJECTED"


def test_non_five_dollar_feasibility_detected() -> None:
    payload = _payload(definition=_definition(min_size="4", max_size="5", max_exposure="5"))
    assert payload["primary_blocker"] == "FIVE_DOLLAR_SIZE_NOT_FEASIBLE"


def test_fee_negative_opportunity_rejected() -> None:
    payload = _payload(decision=_decision(fee_edge=Decimal("-0.10")))
    assert payload["primary_blocker"] == "FEE_ADJUSTED_EDGE_NOT_POSITIVE"


def test_matching_sell_compatibility_and_unknown_financials() -> None:
    sell = _decision(action="SELL", pnl={"gross_profit": "1.2", "fees": "0.1", "net_profit": "1.1"})
    sell.timestamp = datetime(2026, 7, 16, 14, 30, tzinfo=timezone.utc)
    payload = _payload(matching_sell_decision_id=UUID("cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28"), matching_sell_decision=sell)
    matching_sell = payload["observed_later_outcome"]["matching_sell"]
    assert matching_sell["exists"] is True
    assert matching_sell["feasible_closing_action"] is True
    assert matching_sell["known_historical_net_profit"] == "1.1"

    sell_missing = _decision(action="SELL", pnl=None)
    sell_missing.timestamp = datetime(2026, 7, 16, 14, 30, tzinfo=timezone.utc)
    payload_missing = _payload(matching_sell_decision_id=UUID("cc1f15f2-d2f7-4ac6-bdc5-9fc84f742a28"), matching_sell_decision=sell_missing)
    missing = payload_missing["observed_later_outcome"]["matching_sell"]
    assert missing["known_historical_gross_profit"] is None
    assert missing["known_fees"] is None
    assert missing["known_historical_net_profit"] is None


def test_command_implementation_is_read_only() -> None:
    source = service.historical_buy_campaign_replay_audit.__code__.co_names
    assert "commit" not in source
    assert "rollback" not in source
    assert "flush" not in source
    assert "create_canonical_preview_package" not in source
    assert "authorize_canonical_preview_package" not in source
    assert "activate_canonical_proving_campaign" not in source


def test_deterministic_repeated_payload() -> None:
    payload_a = _payload(definition=_definition(allowlist=["ma_crossover@1.0.0"]))
    payload_b = _payload(definition=_definition(allowlist=["ma_crossover@1.0.0"]))
    assert payload_a == payload_b
