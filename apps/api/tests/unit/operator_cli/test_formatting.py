from __future__ import annotations

from datetime import datetime, timezone

from app.operator_cli.formatting import (
    RenderOptions,
    render_candles_text,
    render_preview_show_text,
    render_preview_text,
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
