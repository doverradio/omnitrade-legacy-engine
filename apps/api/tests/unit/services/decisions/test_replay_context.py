from __future__ import annotations

from app.services.decisions.replay_context import REPLAY_CONTEXT_KEYS, build_canonical_replay_context


def test_replay_context_schema_is_stable_and_unknowns_are_explicit() -> None:
    replay_context = build_canonical_replay_context(
        evidence={
            "strategy_identity": "ma_crossover",
            "strategy_version": "1.0.0",
            "action": "buy",
            "product": "BTC-USD",
            "timeframe": "15m",
            "decision_timestamp": "2026-07-16T00:00:00+00:00",
            "normalized_risk_verdict": "approved",
            "signal_ids": ["sig-1"],
            "risk_event_ids": ["risk-1"],
            "trade_ids": [],
        }
    )

    assert sorted(replay_context.keys()) == sorted(REPLAY_CONTEXT_KEYS)
    assert replay_context["confidence"] == "UNKNOWN"
    assert "confidence" in replay_context["unknown_fields"]
    assert replay_context["evidence_completeness"] == "PARTIAL"


def test_replay_context_evidence_completeness_formula_is_deterministic() -> None:
    complete = build_canonical_replay_context(
        evidence={
            "strategy_identity": "ma_crossover",
            "strategy_version": "1.0.0",
            "action": "BUY",
            "confidence": "0.9",
            "product": "BTC-USD",
            "timeframe": "15m",
            "provider": "kraken_spot",
            "environment": "production",
            "paper_account_id": "paper-1",
            "live_trading_profile_id": "profile-1",
            "capital_campaign_id": "campaign-1",
            "capital_campaign_version": "1",
            "runtime_campaign_id": "runtime-1",
            "position_lifecycle_id": "position-1",
            "signal_ids": ["sig-1"],
            "risk_event_ids": ["risk-1"],
            "trade_ids": ["trade-1"],
            "candle_id": "candle-1",
            "candle_close_time": "2026-07-16T00:00:00+00:00",
            "decision_timestamp": "2026-07-16T00:00:00+00:00",
            "market_data_timestamp": "2026-07-16T00:00:00+00:00",
            "normalized_risk_verdict": "ALLOW",
            "expected_gross_edge": "1.0",
            "expected_fees": "0.1",
            "expected_slippage": "0.1",
            "expected_net_edge": "0.8",
            "actual_execution_fee": "0.1",
            "actual_execution_price": "100.0",
            "actual_execution_quantity": "0.01",
        }
    )
    minimal = build_canonical_replay_context(evidence={})

    assert complete["evidence_completeness"] == "COMPLETE"
    assert complete["unknown_fields"] == []
    assert minimal["evidence_completeness"] == "MINIMAL"


def test_replay_context_mapper_has_no_execution_or_capital_side_effect_calls() -> None:
    forbidden = {
        "evaluate_signal_risk",
        "orchestrate_paper_signal_execution",
        "create_crypto_order_preview",
        "create_canonical_preview_package",
        "authorize_canonical_preview_package",
        "activate_canonical_proving_campaign",
    }
    names = set(build_canonical_replay_context.__code__.co_names)
    assert forbidden.isdisjoint(names)
