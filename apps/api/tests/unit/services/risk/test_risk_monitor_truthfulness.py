from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.errors import ServiceUnavailableError
from app.services.risk import risk_context, risk_monitor


class _FakeDb:
    def __init__(self, *, account=None, scalars=None) -> None:
        self.account = account
        self.scalars = list(scalars or [])

    async def get(self, model, account_id):
        _ = model, account_id
        return self.account

    async def scalar(self, _statement):
        if self.scalars:
            return self.scalars.pop(0)
        return None


def _account(*, current_cash_balance: Decimal) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"),
        current_cash_balance=current_cash_balance,
        starting_balance=Decimal("25"),
    )


@pytest.mark.asyncio
async def test_get_risk_status_uses_current_cash_balance_and_dimensionally_correct_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(
        account=_account(current_cash_balance=Decimal("1.85502572734465")),
        scalars=[
            SimpleNamespace(engaged=False, changed_by="system_bootstrap", changed_at=None, reason="bootstrap_default"),
            SimpleNamespace(engaged=False, changed_by="system_bootstrap", changed_at=None, reason="bootstrap_default"),
            None,
            None,
        ],
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

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)

    status = await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))

    assert status.daily_loss.used == Decimal("23.14497427265535")
    assert status.drawdown.used == Decimal("23.14497427265535")
    assert status.daily_loss.limit == Decimal("0.75")
    assert status.drawdown.limit == Decimal("2.50")
    assert status.daily_loss.pct_used == Decimal("30.8599656968738")
    assert status.drawdown.pct_used == Decimal("9.25798970906214")
    assert status.daily_loss_input_source == "current_cash_balance"
    assert status.drawdown_input_source == "current_cash_balance"


@pytest.mark.asyncio
async def test_resolve_execution_risk_context_uses_snapshot_equity_and_fallback_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(account=_account(current_cash_balance=Decimal("23.81")), scalars=[])

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(equity=Decimal("40.00"))

    async def _rules(*_args, **_kwargs):
        return {
            "max_position_size_pct": Decimal("0.10"),
            "max_daily_loss_pct": Decimal("0.03"),
            "max_drawdown_pct": Decimal("0.10"),
            "cooldown_after_losses": 3,
            "cooldown_duration_hours": 24,
            "source": "system_default_config",
        }

    async def _switch(*_args, **_kwargs):
        return (False, False)

    async def _stale(*_args, **_kwargs):
        return (False, False)

    monkeypatch.setattr(risk_context, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(risk_context, "_resolve_effective_risk_rules", _rules)
    monkeypatch.setattr(risk_context, "_resolve_kill_switch_state", _switch)
    monkeypatch.setattr(risk_context, "_resolve_data_quality_inputs", _stale)

    context = await risk_context.resolve_execution_risk_context(
        db=db,
        paper_account=db.account,
        asset=SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
    )

    assert context.current_equity == Decimal("40.00")
    assert context.account_equity == Decimal("40.00")
    assert context.start_of_day_equity_source == "fallback_starting_balance"
    assert context.high_water_mark_equity_source == "fallback_max_starting_vs_current"


@pytest.mark.asyncio
async def test_get_risk_status_fails_closed_when_kill_switch_state_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(account=_account(current_cash_balance=Decimal("25")), scalars=[None, None])

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

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)

    with pytest.raises(ServiceUnavailableError):
        await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))
