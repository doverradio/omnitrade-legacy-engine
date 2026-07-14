from __future__ import annotations

from datetime import datetime, timezone

from app.operator_cli.formatting import (
    RenderOptions,
    render_candles_text,
    render_execution_forensics_text,
    render_preview_show_text,
    render_preview_text,
    render_roster_text,
    render_scorecards_text,
    render_status_text,
    render_watch_text,
)


def _opts() -> RenderOptions:
    return RenderOptions(color_enabled=False, unicode_enabled=False, verbose=True)


def test_render_preview_text_includes_safety_line() -> None:
    text = render_preview_text(
        {
            "cycle_id": "cid",
            "state": "COMPLETE",
            "command_mode": "NEW_PREVIEW",
            "evaluation_mode": "NEW_PREVIEW",
            "proposed_action": "BUY",
            "outcome": "BUY",
            "decision_classification": "strategy-derived",
            "capital_state": "PREVIEW_ONLY",
            "mandate_verdict": "allowed",
            "risk_verdict": "ACCEPTED",
            "preview_id": "pid",
            "decision_record_id": "did",
            "decision_snapshot": {"decision_id": "sid"},
            "timeline": {
                "evaluated_at": "2024-05-01T10:00:00Z",
                "cycle_age_seconds": 120,
                "decision_age_seconds": 90,
                "market_data_age_seconds": 30,
                "latest_completed_candle_open": "2024-05-01T09:45:00Z",
                "latest_completed_candle_close": "2024-05-01T10:00:00Z",
                "oldest_candle_used_open": "2024-04-30T15:00:00Z",
                "history_candle_count": 50,
                "decision_applies_to": "2024-05-01T10:00:00Z",
                "current_incomplete_candle_excluded": True,
            },
            "replayed": False,
            "diagnostics": {
                "termination_stage": "complete",
                "failure_reason": None,
                "deterministic_explanation": ["CHECK_OK:risk"],
            },
        },
        _opts(),
    )

    assert "AUTONOMOUS PREVIEW" in text
    assert "Preview-only path" in text
    assert "CHECK_OK:risk" in text
    assert "NEW PREVIEW" in text
    assert "Cycle age" in text
    assert "Decision age" in text
    assert "Candle age" in text
    assert "Latest candle open" in text


def test_render_preview_show_text_includes_decision_metadata() -> None:
    text = render_preview_show_text(
        {
            "command_mode": "VIEW_EXISTING",
            "preview": {
                "crypto_order_preview_id": "pid",
                "status": "PREVIEW_READY",
                "provider": "kraken_spot",
                "environment": "production",
                "product_id": "BTC-USD",
                "side": "BUY",
                "requested_amount": "5",
                "requested_amount_currency": "USD",
                "warning_messages": ["warn"],
            },
            "decision_record": {
                "decision_id": "did",
                "trade_accepted": True,
                "outcome": "pending_preview",
                "timeframe": "15m",
            },
            "decision_snapshot": {
                "decision_id": "sid",
                "strategy_version": "ma_crossover@1.0.0",
                "configuration_version": "autonomous_cycle_preview_v1",
                "strategy_inputs": {
                    "signal_reason": "cross_up",
                },
            },
            "cycle": {
                "cycle_id": "cid",
                "state": "COMPLETE",
                "deterministic_explanation": ["CHECK_OK:signal"],
            },
            "timeline": {
                "record_created_at": "2024-05-01T10:00:00Z",
                "decision_age_seconds": 3600,
                "latest_completed_candle_close": "2024-05-01T10:00:00Z",
                "history_candle_count": 48,
                "decision_applies_to": "2024-05-01T10:00:00Z",
            },
            "capital_state": "PREVIEW_ONLY",
        }
        , _opts()
    )

    assert "PREVIEW EVIDENCE" in text
    assert "Decision Record ID" in text
    assert "did" in text
    assert "VIEW EXISTING" in text
    assert "Signal" in text
    assert "cross_up" in text
    assert "Warnings" in text
    assert "History loaded" in text
    assert "Verbose deterministic codes" in text


def test_render_candles_text_includes_readiness() -> None:
    text = render_candles_text(
        {
            "symbol": "BTC",
            "exchange": "kraken_spot",
            "interval": "15m",
            "asset_id": "aid",
            "latest_open_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "latest_close_time": datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
            "row_count": 42,
            "age_minutes": 2,
            "ready": True,
            "reason": "ok",
        }
    , _opts())

    assert "CANDLE READINESS" in text
    assert "READY" in text


def test_render_status_text_includes_connection_summary() -> None:
    text = render_status_text(
        {
            "environment": "local",
            "database_url_configured": True,
            "safety_flags": {
                "live_crypto_order_submission_enabled": False,
                "live_crypto_dry_run_enabled": True,
                "live_crypto_max_order_usd": "5",
            },
            "latest_cycle": {
                "cycle_id": "cid",
                "state": "COMPLETE",
                "proposed_action": "HOLD",
            },
            "latest_preview": {
                "crypto_order_preview_id": "pid",
                "status": "PREVIEW_READY",
            },
            "connection_summary": [
                {
                    "provider": "kraken_spot",
                    "environment": "production",
                    "status": "connected",
                    "last_readiness_verdict": "READY",
                }
            ],
            "candle_summary": {
                "symbol": "BTC",
                "interval": "15m",
                "ready": True,
                "age_minutes": 1,
            },
        }
    , _opts())

    assert "MISSION CONTROL STATUS" in text
    assert "Git SHA" in text
    assert "Operator" in text


def test_render_watch_text_contains_expected_fields() -> None:
    text = render_watch_text(
        {
            "latest_cycle": {"proposed_action": "HOLD"},
            "worker_heartbeat": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "campaign_count": 2,
            "decision_count": 5,
            "candle_summary": {"reason": "ok"},
            "system_health": "healthy",
        },
        _opts(),
    )

    assert "OPERATOR WATCH" in text
    assert "Latest decision" in text
    assert "Press Ctrl+C" in text


def test_render_roster_text_statuses_and_summary_reconcile() -> None:
    text = render_roster_text(
        {
            "provider": "kraken_spot",
            "product_id": "BTC-USD",
            "interval": "15m",
            "roster_run": {
                "candle_close_time": "2026-07-13T23:00:00Z",
                "trigger": "kraken_btc_15m_candle_close",
                "execution_mode": "SHADOW",
                "live_submission_allowed": False,
            },
            "proposals": [
                {"strategy_slug": "ma_crossover", "action": "HOLD", "evaluation_status": "EVALUATED"},
                {"strategy_slug": "momentum", "action": "BUY", "evaluation_status": "EVALUATED"},
                {"strategy_slug": "breakout", "action": "SELL", "evaluation_status": "EVALUATED"},
                {
                    "strategy_slug": "mean_reversion",
                    "action": "HOLD",
                    "evaluation_status": "FAILED",
                    "deterministic_explanation": ["CHECK_FAILED:strategy_row_missing"],
                    "reason": "Strategy row not found.",
                },
                {
                    "strategy_slug": "bollinger_reversion",
                    "action": "HOLD",
                    "evaluation_status": "INSUFFICIENT_CONTEXT",
                    "deterministic_explanation": ["CHECK_FAILED:insufficient_candle_history"],
                    "reason": "Insufficient candle history.",
                },
                {
                    "strategy_slug": "donchian_breakout",
                    "action": "HOLD",
                    "evaluation_status": "FAILED",
                    "deterministic_explanation": ["CHECK_FAILED:stale_candle"],
                    "reason": "Candle is stale for roster evaluation.",
                },
                {
                    "strategy_slug": "rsi_mean_reversion",
                    "action": "HOLD",
                    "evaluation_status": "FAILED",
                    "deterministic_explanation": ["CHECK_FAILED:strategy_evaluation_exception"],
                    "reason": "Strategy evaluation failed: RuntimeError",
                },
            ],
        },
        _opts(),
    )

    assert "Ma Crossover" in text
    assert "Momentum" in text
    assert "BUY                1" in text
    assert "SELL               1" in text
    assert "HOLD               1" in text
    assert "Failed             4" in text
    assert "reason: strategy_row_missing" in text
    assert "reason: insufficient_history" in text
    assert "reason: stale_candle" in text


def test_render_roster_text_failed_not_rendered_as_hold() -> None:
    text = render_roster_text(
        {
            "provider": "kraken_spot",
            "product_id": "BTC-USD",
            "interval": "15m",
            "roster_run": {
                "candle_close_time": "2026-07-13T23:00:00Z",
                "trigger": "kraken_btc_15m_candle_close",
                "execution_mode": "SHADOW",
                "live_submission_allowed": False,
            },
            "proposals": [
                {
                    "strategy_slug": "momentum",
                    "action": "HOLD",
                    "evaluation_status": "FAILED",
                    "deterministic_explanation": ["CHECK_FAILED:strategy_row_missing"],
                    "reason": "Strategy row not found.",
                }
            ],
        },
        _opts(),
    )

    assert "Momentum" in text
    assert "[FAILED]" in text
    assert "reason: strategy_row_missing" in text
    assert "HOLD               0" in text


def test_render_scorecards_text_contains_summary_rows() -> None:
    text = render_scorecards_text(
        {
            "provider": "kraken_spot",
            "product_id": "BTC-USD",
            "interval": "15m",
            "latest_outcome_evaluated_at": "2026-07-14T10:00:00Z",
            "scorecards": [
                {
                    "strategy_slug": "momentum",
                    "per_horizon": [
                        {
                            "horizon": "15m",
                            "total_evaluated": 3,
                            "buy_evaluations": 1,
                            "buy_correct": 1,
                            "sell_evaluations": 1,
                            "sell_correct": 0,
                            "hold_evaluations": 1,
                            "hold_correct": 1,
                            "overall_correct_pct": "66.6667",
                            "average_raw_return_pct": "0.3012",
                            "average_fee_adjusted_return_pct": "0.1012",
                            "average_mfe_pct": "0.8100",
                            "average_mae_pct": "-0.4200",
                        },
                        {
                            "horizon": "1h",
                            "total_evaluated": 1,
                            "buy_evaluations": 0,
                            "buy_correct": 0,
                            "sell_evaluations": 1,
                            "sell_correct": 1,
                            "hold_evaluations": 0,
                            "hold_correct": 0,
                            "overall_correct_pct": "100.0000",
                            "average_raw_return_pct": "0.5000",
                            "average_fee_adjusted_return_pct": "0.3000",
                            "average_mfe_pct": "1.0000",
                            "average_mae_pct": "-0.2000",
                        },
                    ],
                    "aggregate": {
                        "horizon": "aggregate",
                        "total_evaluated": 4,
                        "buy_evaluations": 1,
                        "buy_correct": 1,
                        "sell_evaluations": 2,
                        "sell_correct": 1,
                        "hold_evaluations": 1,
                        "hold_correct": 1,
                        "overall_correct_pct": "75.0000",
                        "average_raw_return_pct": "0.3500",
                        "average_fee_adjusted_return_pct": "0.1500",
                        "average_mfe_pct": "0.9000",
                        "average_mae_pct": "-0.3500",
                    },
                    "best_regime": None,
                    "worst_regime": None,
                    "regime_evidence_count": 4,
                    "regime_min_evidence_required": 50,
                }
            ],
        },
        _opts(),
    )

    assert "STRATEGY SCORECARDS" in text
    assert "Per Horizon [15m]" in text
    assert "Per Horizon [1h]" in text
    assert "Aggregate [all horizons combined]" in text
    assert "BUY evaluations/correct: 1/1" in text
    assert "SELL evaluations/correct: 2/1" in text
    assert "HOLD evaluations/correct: 1/1" in text
    assert "reconciliation(BUY+SELL+HOLD==total): 4==4" in text
    assert "Insufficient evidence (4/50)" in text


def test_render_scorecards_text_empty_state() -> None:
    text = render_scorecards_text(
        {
            "provider": "kraken_spot",
            "product_id": "BTC-USD",
            "interval": "15m",
            "latest_outcome_evaluated_at": None,
            "scorecards": [],
        },
        _opts(),
    )

    assert "No scorecards found for this market." in text


def test_render_execution_forensics_text_contains_trace_sections() -> None:
    text = render_execution_forensics_text(
        {
            "mode": "read_only_forensics",
            "criteria": {"selector": "latest", "since": None, "cycle_id": None},
            "cycle_count": 1,
            "cycles": [
                {
                    "cycle_id": "cid",
                    "timestamp": "2026-07-14T09:49:40Z",
                    "asset": "BTC",
                    "provider": "kraken_spot",
                    "interval": "15m",
                    "latest_candle_time": "2026-07-14T09:45:00Z",
                    "signal_section": {
                        "signals_generated": 1,
                        "source": "signals_table_via_decision_lineage",
                        "signals": [
                            {
                                "signal_id": "sid",
                                "strategy": "momentum",
                                "action": "BUY",
                                "confidence": "0.78",
                                "reason": "breakout",
                            }
                        ],
                    },
                    "strategy_roster": {
                        "proposal_count": 7,
                        "buy_count": 1,
                        "sell_count": 2,
                        "hold_count": 4,
                        "mode": "SHADOW",
                        "executable": "NO",
                        "reason": "Strategy Roster proposals are shadow research observations and never executable orders",
                    },
                    "autonomous_decision": {
                        "proposed_action": "BUY",
                        "mandate_verdict": "AUTHORIZED",
                        "risk_verdict": "ACCEPTED",
                        "execution_handoff": "NOT IMPLEMENTED",
                        "exact_blocker": "AUTONOMOUS_CANONICAL_SIGNAL_HANDOFF_NOT_IMPLEMENTED",
                    },
                    "execution_candidate": {"is_candidate": True, "reason_if_no": None},
                    "risk": {
                        "evaluated": True,
                        "decision": "accepted",
                        "reason": {"decision": "accept"},
                        "risk_event_ids": ["rid"],
                    },
                    "execution": {
                        "execution_attempted": True,
                        "execution_service_called": True,
                        "order_created": False,
                        "trade_created": True,
                        "filled": True,
                        "rejected": False,
                        "skipped": False,
                        "error": False,
                        "trade_ids": ["tid"],
                    },
                    "accounting": {
                        "paper_account_ids": ["aid"],
                        "fees_total": "0.01",
                        "pnl": {"net": "0.02"},
                        "buy_quantity_total": "1",
                        "sell_quantity_total": "0",
                        "entries": [
                            {
                                "trade_id": "tid",
                                "paper_account_id": "aid",
                                "balance_before": "100.0",
                                "balance_after": "95.0",
                                "position_before": "0",
                                "position_after": "1",
                                "fee": "0.01",
                            }
                        ],
                    },
                    "decision_records": {
                        "decision_record_id": "did",
                        "outcome_score_linkage_count": 1,
                        "outcome_score_ids": ["oid"],
                        "autonomous_cycle_linkage": {"cycle_id": "cid", "scheduled_roster_run_ids": ["rrid"]},
                        "research_linkage": [
                            {
                                "event_id": 1,
                                "event_type": "RESEARCH_CYCLE_STARTED",
                                "campaign_id": "campid",
                                "created_at": "2026-07-14T09:50:00Z",
                            }
                        ],
                    },
                    "summary": "Actionable signal became paper trade",
                }
            ],
        },
        _opts(),
    )

    assert "PRODUCTION EXECUTION FORENSICS" in text
    assert "Cycle ID" in text
    assert "Legacy Signals" in text
    assert "Strategy Roster" in text
    assert "Autonomous Decision" in text
    assert "Execution" in text
    assert "Accounting" in text
    assert "Decision Linkage" in text
    assert "Actionable signal became paper trade" in text


def test_render_execution_forensics_shadow_roster_is_not_executable() -> None:
    text = render_execution_forensics_text(
        {
            "mode": "read_only_forensics",
            "criteria": {"selector": "latest", "since": None, "cycle_id": None},
            "cycle_count": 1,
            "cycles": [
                {
                    "cycle_id": "cid",
                    "timestamp": "2026-07-14T09:49:40Z",
                    "asset": "BTC",
                    "provider": "kraken_spot",
                    "interval": "15m",
                    "latest_candle_time": "2026-07-14T09:45:00Z",
                    "signal_section": {
                        "signals_generated": 0,
                        "source": "signals_table_via_decision_lineage",
                        "signals": [],
                    },
                    "strategy_roster": {
                        "proposal_count": 7,
                        "buy_count": 1,
                        "sell_count": 2,
                        "hold_count": 4,
                        "mode": "SHADOW",
                        "executable": "NO",
                        "reason": "Strategy Roster proposals are shadow research observations and never executable orders",
                    },
                    "autonomous_decision": {
                        "proposed_action": "BUY",
                        "mandate_verdict": "AUTHORIZED",
                        "risk_verdict": "ACCEPTED",
                        "execution_handoff": "NOT IMPLEMENTED",
                        "exact_blocker": "AUTONOMOUS_CANONICAL_SIGNAL_HANDOFF_NOT_IMPLEMENTED",
                    },
                    "execution_candidate": {"status": "UNPROVEN", "reason_if_no": "NOT APPLICABLE"},
                    "risk": {"evaluated_status": "UNPROVEN", "decision": "UNPROVEN", "reason": "UNPROVEN", "risk_event_ids": []},
                    "execution": {
                        "execution_attempted_status": "NO",
                        "execution_service_called_status": "NOT APPLICABLE",
                        "order_created_status": "NOT APPLICABLE",
                        "trade_created_status": "NO",
                        "filled_status": "NOT APPLICABLE",
                        "rejected_status": "NOT APPLICABLE",
                        "skipped_status": "NOT APPLICABLE",
                        "error_status": "NOT APPLICABLE",
                        "trade_ids": [],
                    },
                    "accounting": {
                        "paper_account_ids": [],
                        "entries": [],
                        "fees_total": "0",
                        "pnl": None,
                        "buy_quantity_total": "0",
                        "sell_quantity_total": "0",
                        "account_balance_changed_status": "NOT APPLICABLE",
                        "position_changed_status": "NOT APPLICABLE",
                        "accounting_entry_persisted_status": "NOT APPLICABLE",
                    },
                    "decision_records": {
                        "decision_record_id": "did",
                        "outcome_score_linkage_count": 0,
                        "outcome_score_ids": [],
                        "decision_record_linkage_status": "YES",
                        "outcome_linkage_status": "NO",
                        "research_linkage_status": "NO",
                        "autonomous_cycle_linkage": {"cycle_id": "cid", "scheduled_roster_run_ids": ["rrid"]},
                        "research_linkage": [],
                    },
                    "summary": "No legacy executable signals linked to this autonomous cycle",
                }
            ],
        },
        _opts(),
    )

    assert "Legacy Signals" in text
    assert "Executable signals" in text
    assert "Strategy Roster" in text
    assert "SHADOW" in text
    assert "Execution handoff" in text
    assert "NOT IMPLEMENTED" in text
    assert "No legacy executable signals linked to this autonomous cycle" in text
