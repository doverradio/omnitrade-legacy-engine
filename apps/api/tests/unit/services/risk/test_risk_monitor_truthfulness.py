from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.errors import ServiceUnavailableError
from app.services.risk import risk_context, risk_monitor
from app.services.risk.equity_evidence import EquityBaselineSnapshot, EquityRiskEvidence, EquityValuationSnapshot
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationRequest, evaluate_signal_risk


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


def _equity_evidence(
    *,
    current_equity: Decimal,
    cash_balance: Decimal,
    position_value: Decimal,
    start_of_day_equity: Decimal,
    high_water_mark_equity: Decimal,
    start_source: str = "rolled_from_prior_last_equity",
    high_source: str = "updated_from_current_equity_observation",
    valuation_state: str = "ready",
    ready: bool = True,
    fail_closed_reason: str | None = None,
    unresolved_reconciliation_count: int = 0,
    unknown_provider_order_count: int = 0,
) -> EquityRiskEvidence:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    return EquityRiskEvidence(
        valuation=EquityValuationSnapshot(
            generated_at=now,
            current_equity=current_equity,
            cash_balance=cash_balance,
            position_value=position_value,
            latest_price_timestamp=now,
            valuation_source="paper_account_snapshot_mark_to_market_candles",
            valuation_state=valuation_state,
            missing_price_assets=[],
            stale_price_assets=[],
            stale_cutoff=now,
            price_evidence=[],
        ),
        baseline=EquityBaselineSnapshot(
            start_of_day_equity=start_of_day_equity,
            high_water_mark_equity=high_water_mark_equity,
            start_of_day_source=start_source,
            high_water_mark_source=high_source,
            session_date=date(2026, 7, 15),
            baseline_state="ready" if ready else "bootstrap_first_observation",
            baseline_ready=ready,
        ),
        unresolved_reconciliation_count=unresolved_reconciliation_count,
        unknown_provider_order_count=unknown_provider_order_count,
        ready=ready,
        fail_closed_reason=fail_closed_reason,
    )


@pytest.mark.asyncio
async def test_get_risk_status_equity_based_does_not_treat_deployed_cash_as_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(
        account=_account(current_cash_balance=Decimal("4.34816564978665")),
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

    async def _evidence(*_args, **_kwargs):
        return _equity_evidence(
            current_equity=Decimal("25.47709304978665"),
            cash_balance=Decimal("4.34816564978665"),
            position_value=Decimal("21.1289274"),
            start_of_day_equity=Decimal("25.00"),
            high_water_mark_equity=Decimal("25.50"),
        )

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _evidence)

    status = await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))

    assert status.current_cash_balance == Decimal("4.34816564978665")
    assert status.current_position_value == Decimal("21.1289274")
    assert status.current_equity == Decimal("25.47709304978665")
    assert status.daily_loss.used == Decimal("0")
    assert status.drawdown.used == Decimal("0.02290695021335")
    assert status.daily_loss_input_source == "current_equity"
    assert status.drawdown_input_source == "current_equity"


@pytest.mark.asyncio
async def test_get_risk_status_profitable_portfolio_reports_zero_daily_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(
        account=_account(current_cash_balance=Decimal("10")),
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

    async def _evidence(*_args, **_kwargs):
        return _equity_evidence(
            current_equity=Decimal("26"),
            cash_balance=Decimal("10"),
            position_value=Decimal("16"),
            start_of_day_equity=Decimal("25"),
            high_water_mark_equity=Decimal("26"),
        )

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _evidence)

    status = await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))

    assert status.daily_loss.used == Decimal("0")
    assert status.drawdown.used == Decimal("0")


@pytest.mark.asyncio
async def test_get_risk_status_reports_genuine_equity_decline_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(
        account=_account(current_cash_balance=Decimal("9")),
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

    async def _evidence(*_args, **_kwargs):
        return _equity_evidence(
            current_equity=Decimal("23.5"),
            cash_balance=Decimal("9"),
            position_value=Decimal("14.5"),
            start_of_day_equity=Decimal("25"),
            high_water_mark_equity=Decimal("27"),
        )

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _evidence)

    status = await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))

    assert status.daily_loss.used == Decimal("1.5")
    assert status.drawdown.used == Decimal("3.5")
    assert status.daily_loss.limit == Decimal("0.75")
    assert status.drawdown.limit == Decimal("2.7")
    assert status.daily_loss.pct_used == Decimal("2")
    assert status.drawdown.pct_used == Decimal("1.296296296296296296296296296")
    assert status.daily_loss_baseline_source == "rolled_from_prior_last_equity"
    assert status.drawdown_baseline_source == "updated_from_current_equity_observation"


@pytest.mark.asyncio
async def test_get_risk_status_fails_closed_on_untrusted_equity_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(
        account=_account(current_cash_balance=Decimal("25")),
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

    async def _evidence(*_args, **_kwargs):
        return _equity_evidence(
            current_equity=Decimal("25"),
            cash_balance=Decimal("25"),
            position_value=Decimal("0"),
            start_of_day_equity=Decimal("25"),
            high_water_mark_equity=Decimal("25"),
            ready=False,
            fail_closed_reason="missing_price_evidence",
            valuation_state="missing_price_evidence",
        )

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _evidence)

    with pytest.raises(ServiceUnavailableError):
        await risk_monitor.get_risk_status(db=db, account_id=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))


@pytest.mark.asyncio
async def test_resolve_execution_risk_context_uses_equity_evidence_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(account=_account(current_cash_balance=Decimal("23.81")), scalars=[])

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

    async def _evidence(*_args, **_kwargs):
        return _equity_evidence(
            current_equity=Decimal("40"),
            cash_balance=Decimal("24"),
            position_value=Decimal("16"),
            start_of_day_equity=Decimal("39.5"),
            high_water_mark_equity=Decimal("41"),
        )

    monkeypatch.setattr(risk_context, "_resolve_effective_risk_rules", _rules)
    monkeypatch.setattr(risk_context, "_resolve_kill_switch_state", _switch)
    monkeypatch.setattr(risk_context, "_resolve_data_quality_inputs", _stale)
    monkeypatch.setattr(risk_context, "resolve_equity_risk_evidence", _evidence)

    context = await risk_context.resolve_execution_risk_context(
        db=db,
        paper_account=db.account,
        asset=SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
    )

    assert context.current_equity == Decimal("40")
    assert context.account_equity == Decimal("40")
    assert context.start_of_day_equity == Decimal("39.5")
    assert context.high_water_mark_equity == Decimal("41")
    assert context.start_of_day_equity_source == "rolled_from_prior_last_equity"
    assert context.high_water_mark_equity_source == "updated_from_current_equity_observation"


def test_risk_engine_rejects_genuine_threshold_breaches() -> None:
    request = RiskEvaluationRequest(
        signal_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        asset_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        side="buy",
        quantity=Decimal("0.1"),
        account_equity=Decimal("20"),
        max_position_size_pct=Decimal("0.5"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.0001"),
        supports_fractional=True,
        start_of_day_equity=Decimal("25"),
        current_equity=Decimal("24"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("30"),
        max_drawdown_pct=Decimal("0.10"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("100"))

    assert result.action == RiskDecisionAction.REJECT
    assert result.reason_code in {"max_daily_loss_breached", "max_drawdown_breached"}


def test_risk_engine_does_not_reject_merely_because_cash_is_deployed() -> None:
    request = RiskEvaluationRequest(
        signal_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        asset_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        side="buy",
        quantity=Decimal("0.01"),
        account_equity=Decimal("25.5"),
        max_position_size_pct=Decimal("0.05"),
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.0001"),
        supports_fractional=True,
        start_of_day_equity=Decimal("25"),
        current_equity=Decimal("25.5"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("25.5"),
        max_drawdown_pct=Decimal("0.10"),
    )

    result = evaluate_signal_risk(request=request, reference_price=Decimal("100"))

    assert result.action in {RiskDecisionAction.APPROVE, RiskDecisionAction.RESIZE}
