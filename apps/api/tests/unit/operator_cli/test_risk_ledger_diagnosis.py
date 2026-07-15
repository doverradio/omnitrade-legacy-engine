from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.operator_cli.service as service
from app.services.risk.equity_evidence import EquityBaselineSnapshot, EquityRiskEvidence, EquityValuationSnapshot


class _SessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FetchDB:
    def __init__(self, *, account=None, scalars=None) -> None:
        self.account = account
        self.scalars = list(scalars or [])

    async def get(self, model, account_id):
        _ = model, account_id
        return self.account

    async def scalar(self, _stmt):
        if self.scalars:
            return self.scalars.pop(0)
        return None

    async def commit(self):
        raise AssertionError("read-only command must not call commit")

    async def flush(self):
        raise AssertionError("read-only command must not call flush")


@pytest.mark.asyncio
async def test_fetch_risk_ledger_diagnosis_reports_formula_trace_and_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    account_id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
    latest_trade_at = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    db = _FetchDB(
        account=SimpleNamespace(
            id=account_id,
            created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            asset_class="crypto",
            is_active=True,
            starting_balance=Decimal("25.00"),
            current_cash_balance=Decimal("23.81"),
        ),
        scalars=[SimpleNamespace(executed_at=latest_trade_at), 4],
    )

    async def _policy(*_args, **_kwargs):
        return SimpleNamespace(
            max_position_size_pct=Decimal("0.10"),
            max_daily_loss_pct=Decimal("0.03"),
            max_drawdown_pct=Decimal("0.10"),
            default_stop_loss_pct=Decimal("0.03"),
            cooldown_after_losses=3,
            cooldown_duration_hours=24,
            source="system_default_config",
        )

    async def _status(*_args, **_kwargs):
        return SimpleNamespace(
            daily_loss=SimpleNamespace(used=Decimal("0"), limit=Decimal("0.75"), pct_used=Decimal("0")),
            drawdown=SimpleNamespace(used=Decimal("0"), limit=Decimal("2.50"), pct_used=Decimal("0")),
            daily_loss_input_source="current_equity",
            drawdown_input_source="current_equity",
            policy_source="system_default_config",
            current_equity=Decimal("25.00"),
            current_cash_balance=Decimal("23.81"),
            current_position_value=Decimal("1.19"),
            start_of_day_equity=Decimal("25.00"),
            high_water_mark_equity=Decimal("25.00"),
            valuation_source="provider_quotes",
            valuation_state="ready",
            daily_loss_baseline_source="rolled_from_prior_last_equity",
            drawdown_baseline_source="updated_from_current_equity_observation",
            baseline_state="ready",
            generated_at=datetime(2026, 1, 1, 12, 31, tzinfo=timezone.utc),
        )

    async def _evidence(*_args, **_kwargs):
        now = datetime(2026, 1, 1, 12, 31, tzinfo=timezone.utc)
        return EquityRiskEvidence(
            valuation=EquityValuationSnapshot(
                generated_at=now,
                current_equity=Decimal("25.00"),
                cash_balance=Decimal("23.81"),
                position_value=Decimal("1.19"),
                latest_price_timestamp=now,
                valuation_source="provider_quotes",
                valuation_state="ready",
                missing_price_assets=[],
                stale_price_assets=[],
                stale_cutoff=now,
                price_evidence=[],
            ),
            baseline=EquityBaselineSnapshot(
                start_of_day_equity=Decimal("25.00"),
                high_water_mark_equity=Decimal("25.00"),
                start_of_day_source="rolled_from_prior_last_equity",
                high_water_mark_source="updated_from_current_equity_observation",
                session_date=now.date(),
                baseline_state="ready",
                baseline_ready=True,
            ),
            unresolved_reconciliation_count=0,
            unknown_provider_order_count=0,
            ready=True,
            fail_closed_reason=None,
        )

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            cash_balance=Decimal("19.00"),
            position_value=Decimal("6.00"),
            equity=Decimal("25.00"),
            equity_return_usd=Decimal("0.00"),
            equity_return_pct=Decimal("0.00"),
            positions=(),
        )

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
    monkeypatch.setattr(service, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(service.risk_monitor, "get_risk_status", _status)
    monkeypatch.setattr(service, "resolve_equity_risk_evidence", _evidence)
    monkeypatch.setattr(service, "build_account_snapshot", _snapshot)

    payload = await service.fetch_risk_ledger_diagnosis(account_id=account_id)

    assert payload["account"]["account_id"] == str(account_id)
    assert payload["inputs"]["starting_balance"]["value"] == "25.00"
    assert payload["inputs"]["current_cash_balance"]["value"] == "23.81"
    assert payload["inputs"]["max_daily_loss_pct"]["value"] == "0.03"
    assert payload["inputs"]["max_drawdown_pct"]["value"] == "0.10"
    assert payload["evaluation"]["latest_trade_executed_at"] == latest_trade_at
    assert payload["formulas"]["legacy_cash_only.daily_loss.used"] == "max(0, starting_balance - current_cash_balance)"
    assert payload["formulas"]["authoritative_equity.daily_loss.used"] == "max(0, start_of_day_equity - current_equity)"
    assert payload["status"]["daily_loss"]["used"] == "0"
    assert payload["status"]["drawdown"]["limit"] == "2.50"
    assert payload["equity_evidence"]["ready"] is True
    assert payload["snapshot"]["cash_balance"] == "19.00"
    assert payload["diagnosis"]["ledger_alignment"] == "divergent"
