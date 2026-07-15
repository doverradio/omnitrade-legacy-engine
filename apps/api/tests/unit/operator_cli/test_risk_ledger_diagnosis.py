from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.operator_cli.service as service


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
            daily_loss=SimpleNamespace(used=Decimal("1.19"), limit=Decimal("0.75"), pct_used=Decimal("1.586666666666666666666666667")),
            drawdown=SimpleNamespace(used=Decimal("1.19"), limit=Decimal("2.50"), pct_used=Decimal("0.476")),
            daily_loss_input_source="current_cash_balance",
            drawdown_input_source="current_cash_balance",
            policy_source="system_default_config",
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
    monkeypatch.setattr(service, "build_account_snapshot", _snapshot)

    payload = await service.fetch_risk_ledger_diagnosis(account_id=account_id)

    assert payload["account"]["account_id"] == str(account_id)
    assert payload["inputs"]["starting_balance"]["value"] == "25.00"
    assert payload["inputs"]["current_cash_balance"]["value"] == "23.81"
    assert payload["inputs"]["max_daily_loss_pct"]["value"] == "0.03"
    assert payload["inputs"]["max_drawdown_pct"]["value"] == "0.10"
    assert payload["evaluation"]["latest_trade_executed_at"] == latest_trade_at
    assert payload["formulas"]["daily_loss.used"] == "max(0, starting_balance - current_cash_balance)"
    assert payload["status"]["daily_loss"]["used"] == "1.19"
    assert payload["status"]["drawdown"]["limit"] == "2.50"
    assert payload["snapshot"]["cash_balance"] == "19.00"
    assert payload["diagnosis"]["ledger_alignment"] == "divergent"
