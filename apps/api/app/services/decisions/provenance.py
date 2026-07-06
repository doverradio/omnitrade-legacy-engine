from __future__ import annotations

from dataclasses import fields

from app.services.decisions.contracts import (
    DecisionRecordContract,
    DecisionSnapshotContract,
)


DECISION_RECORD_PROVENANCE_MAPPING: dict[str, tuple[str, ...]] = {
    "version": ("decision_engine",),
    "timestamp": ("signals.signal_time", "risk_events.created_at"),
    "asset": ("signals.asset_id",),
    "timeframe": ("signals",),
    "market_regime": ("signals.regime_tag", "model_outputs"),
    "indicators": ("model_outputs.input_summary",),
    "generated_signals": ("signals",),
    "signal_strength": ("signals.raw_strength",),
    "confidence": ("signals.ai_confidence", "model_outputs.output"),
    "supporting_strategies": ("model_outputs.output",),
    "opposing_strategies": ("model_outputs.output",),
    "risk_adjustments": ("risk_events.detail",),
    "expected_risk": ("risk_events.detail",),
    "expected_reward": ("model_outputs.output",),
    "position_size": ("trades.quantity", "risk_events.detail"),
    "trade_accepted": ("risk_events.action_taken",),
    "trade_rejected_reason": ("risk_events.detail",),
    "execution_details": ("trades",),
    "exit_details": ("trades",),
    "pnl": ("trades",),
    "duration": ("trades",),
    "outcome": ("trades",),
    "post_trade_notes": ("model_outputs",),
    "lessons_learned": ("model_outputs",),
    "ai_reflection": ("model_outputs",),
    "future_tags": ("model_outputs",),
    "confidence_calibration": ("model_outputs", "trades"),
    "review_status": ("decision_review",),
    "human_notes": ("decision_review",),
}


DECISION_SNAPSHOT_PROVENANCE_MAPPING: dict[str, tuple[str, ...]] = {
    "timestamp": ("signals.signal_time",),
    "asset": ("signals.asset_id",),
    "exchange": ("assets.exchange",),
    "timeframe": ("signals",),
    "ohlcv_context": ("candles",),
    "indicators": ("model_outputs.input_summary",),
    "generated_features": ("model_outputs.input_summary",),
    "market_regime": ("signals.regime_tag", "model_outputs.output"),
    "volatility": ("model_outputs.input_summary",),
    "spread_liquidity_context": ("model_outputs.input_summary",),
    "strategy_inputs": ("signals", "parameter_sets.params"),
    "risk_inputs": ("risk_events.detail", "paper_accounts"),
    "current_position_state": ("trades",),
    "open_trades": ("trades",),
    "portfolio_exposure": ("paper_accounts", "trades"),
    "parameter_set_version": ("parameter_sets",),
    "strategy_version": ("strategies.module_version",),
    "ai_model_version": ("model_outputs.model_version",),
    "decision_engine_version": ("decision_engine",),
    "configuration_version": ("risk_rule_configs",),
}


def validate_provenance_mappings() -> None:
    decision_record_fields = {field.name for field in fields(DecisionRecordContract)}
    decision_snapshot_fields = {field.name for field in fields(DecisionSnapshotContract)}

    missing_record_fields = decision_record_fields - set(DECISION_RECORD_PROVENANCE_MAPPING)
    missing_snapshot_fields = decision_snapshot_fields - set(DECISION_SNAPSHOT_PROVENANCE_MAPPING)

    if missing_record_fields:
        raise ValueError(
            "DECISION_RECORD_PROVENANCE_MAPPING is missing fields: "
            f"{sorted(missing_record_fields)}"
        )

    if missing_snapshot_fields:
        raise ValueError(
            "DECISION_SNAPSHOT_PROVENANCE_MAPPING is missing fields: "
            f"{sorted(missing_snapshot_fields)}"
        )
