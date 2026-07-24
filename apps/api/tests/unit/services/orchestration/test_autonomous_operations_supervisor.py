from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import asyncio
import pytest

from app.operator_cli import service
from app.operator_cli.main import parse_args

from app.services.orchestration.autonomous_operations_supervisor import resolve_autonomous_profit_snapshot


def evidence(**overrides):
    now = datetime.now(timezone.utc)
    cycle = SimpleNamespace(
        cycle_id=uuid4(), capital_campaign_id=uuid4(), capital_campaign_version=1,
        mandate_id=uuid4(), mandate_version_id=uuid4(), decision_record_id=uuid4(),
        mandate_evaluation_id=uuid4(), proposed_action="HOLD", failure_reason=None,
        completed_at=now, updated_at=now,
    )
    value = {
        "now": now, "provider": "kraken_spot", "environment": "production", "product": "BTC-USD",
        "cycle": cycle, "package": None, "activation": None, "order": None, "position": None,
        "reconciliation": None, "readiness": {}, "position_open": False, "buy_reconciled": False,
        "sell_reconciled": False, "autonomous_buy_provenance": False,
        "autonomous_sell_provenance": False, "net_profit": "0",
        "automatic_activation_enabled": True, "live_submission_enabled": True,
    }
    value.update(overrides)
    return value


def package(state="READY", age=timedelta()):
    now = datetime.now(timezone.utc) - age
    return SimpleNamespace(
        package_id=uuid4(), campaign_id=uuid4(), campaign_version=1, mandate_id=uuid4(),
        mandate_version_id=uuid4(), decision_record_id=uuid4(), package_state=state,
        generated_at=now, updated_at=now, preview_expires_at=now + timedelta(minutes=5),
    )


def test_healthy_hold_is_waiting_without_human_action():
    result = resolve_autonomous_profit_snapshot(evidence())
    assert result["overall_status"] == "HEALTHY_WAITING"
    assert result["human_action_required"] is False


def test_scorecard_timeout_is_blocked():
    item = evidence()
    item["cycle"].failure_reason = "strategy_scorecard_fetch_timeout"
    assert resolve_autonomous_profit_snapshot(item)["reason_codes"] == ["scorecard_fetch_timeout"]


def test_session_failure_is_blocked_and_resolution_is_pure():
    item = evidence()
    item["cycle"].failure_reason = "database_session_unrecoverable"
    before = dict(item)
    result = resolve_autonomous_profit_snapshot(item)
    assert result["overall_status"] == "BLOCKED"
    assert item == before


def test_stale_package_is_blocked():
    item = evidence(readiness={"reason_codes": [{"code": "stale_package"}]}, historical_package=package(age=timedelta(minutes=10)))
    item["cycle"].proposed_action = "BUY"
    result = resolve_autonomous_profit_snapshot(item)
    assert result["overall_status"] == "BLOCKED"
    assert result["stale_package"]["package_id"] == str(item["historical_package"].package_id)
    assert "past preview_expires_at" in result["recommended_action"]


def test_historical_stale_package_does_not_block_healthy_hold():
    item = evidence(
        readiness={"reason_codes": [{"code": "stale_package"}]},
        historical_package=package("AUTHORIZED", age=timedelta(minutes=10)),
    )
    item["cycle"].failure_reason = "strategy_hold_signal"
    result = resolve_autonomous_profit_snapshot(item)
    assert result["overall_status"] == "HEALTHY_WAITING"
    assert result["current_stage"] == "MANDATE_EVALUATED"
    assert "stale_package" not in result["reason_codes"]
    assert result["latest_package_id"] == str(item["historical_package"].package_id)


def test_stale_history_does_not_block_newer_fresh_package():
    old = package("AUTHORIZED", age=timedelta(minutes=10))
    current = package("READY")
    result = resolve_autonomous_profit_snapshot(evidence(package=current, historical_package=current, readiness={}))
    assert result["current_stage"] == "PACKAGE_READY"
    assert result["active_package_id"] == str(current.package_id)
    assert str(old.package_id) not in result["reason_codes"]


def test_old_campaign_package_identity_is_not_selected_as_active():
    old = package("AUTHORIZED", age=timedelta(minutes=10))
    item = evidence(historical_package=old)
    result = resolve_autonomous_profit_snapshot(item)
    assert result["campaign_id"] == str(item["cycle"].capital_campaign_id)
    assert result["active_package_id"] is None


def test_activation_disabled_is_safety_disabled():
    result = resolve_autonomous_profit_snapshot(evidence(package=package(), automatic_activation_enabled=False))
    assert result["overall_status"] == "SAFETY_DISABLED"
    assert "automatic_activation_disabled" in result["reason_codes"]


def test_live_submission_disabled_is_safety_disabled_and_unreachable():
    activation = SimpleNamespace(activation_id=uuid4(), activated_at=datetime.now(timezone.utc))
    result = resolve_autonomous_profit_snapshot(evidence(package=package("ACTIVATED"), activation=activation, live_submission_enabled=False))
    assert result["overall_status"] == "SAFETY_DISABLED"
    assert result["provider_submission_reachable"] is False


def test_lifecycle_stage_advances_deterministically():
    ready = resolve_autonomous_profit_snapshot(evidence(package=package()))
    activated = resolve_autonomous_profit_snapshot(evidence(package=package("ACTIVATED"), activation=SimpleNamespace(activation_id=uuid4(), activated_at=datetime.now(timezone.utc))))
    assert ready["current_stage"] == "PACKAGE_READY"
    assert activated["current_stage"] == "PACKAGE_ACTIVATED"


def test_open_position_not_stalled_before_threshold():
    order = SimpleNamespace(live_crypto_order_id=uuid4(), side="BUY", status="FILLED", submitted_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    result = resolve_autonomous_profit_snapshot(evidence(order=order, position_open=True, position_updated_at=datetime.now(timezone.utc)))
    assert result["current_stage"] == "POSITION_OPEN"
    assert result["stalled"] is False


def test_reconciled_loss_does_not_complete_profit():
    result = resolve_autonomous_profit_snapshot(evidence(buy_reconciled=True, sell_reconciled=True, autonomous_buy_provenance=True, autonomous_sell_provenance=True, net_profit="-0.01"))
    assert result["overall_status"] != "FIRST_AUTONOMOUS_PROFIT_COMPLETE"


def test_positive_profit_requires_full_canonical_provenance():
    incomplete = resolve_autonomous_profit_snapshot(evidence(buy_reconciled=True, sell_reconciled=True, net_profit="1"))
    complete = resolve_autonomous_profit_snapshot(evidence(buy_reconciled=True, sell_reconciled=True, autonomous_buy_provenance=True, autonomous_sell_provenance=True, net_profit="1"))
    assert incomplete["overall_status"] != "FIRST_AUTONOMOUS_PROFIT_COMPLETE"
    assert complete["overall_status"] == "FIRST_AUTONOMOUS_PROFIT_COMPLETE"


def test_repeated_resolution_is_idempotent():
    item = evidence()
    assert resolve_autonomous_profit_snapshot(item) == resolve_autonomous_profit_snapshot(item)


def test_progressing_package_becomes_stalled_after_threshold():
    item = evidence(package=package(age=timedelta(minutes=11)))
    item["cycle"].proposed_action = "BUY"
    result = resolve_autonomous_profit_snapshot(item)
    assert result["overall_status"] == "STALLED"
    assert result["stalled"] is True


@pytest.mark.asyncio
async def test_status_propagates_cancellation(monkeypatch):
    async def cancelled(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_gather_autonomous_supervisor_evidence", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await service.autonomous_profit_status(provider="kraken_spot", environment="production", product="BTC-USD")


def test_operator_status_and_report_commands_parse():
    status = parse_args(["autonomous-profit-status", "--provider", "kraken_spot", "--environment", "production", "--product", "BTC-USD"])
    report = parse_args(["autonomous-profit-report", "--provider", "kraken_spot", "--environment", "production", "--product", "BTC-USD", "--since", "12h"])
    assert status.command == "autonomous-profit-status"
    assert report.command == "autonomous-profit-report"
    assert report.since == "12h"


def test_stale_package_inspect_command_is_read_only_and_scoped():
    args = parse_args(["stale-package-inspect", "--provider", "kraken_spot", "--environment", "production", "--product", "BTC-USD", "--json"])
    assert args.command == "stale-package-inspect"
    assert args.provider == "kraken_spot"
    assert args.environment == "production"
    assert args.product == "BTC-USD"
