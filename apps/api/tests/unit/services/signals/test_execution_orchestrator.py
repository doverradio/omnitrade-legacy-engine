from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.risk.risk_engine import RiskDecisionAction
from app.services.signals import execution_orchestrator as orchestrator


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _HelperDb:
    async def execute(self, _statement):
        return _Rows(
            [
                (
                    UUID("22222222-2222-2222-2222-222222222222"),
                    {
                        "dedicated_proving_transition": {
                            "old_paper_account_id": "11111111-1111-1111-1111-111111111111",
                            "new_paper_account_id": "22222222-2222-2222-2222-222222222222",
                        }
                    },
                )
            ]
        )


class _MalformedEvidenceDb:
    async def execute(self, _statement):
        return _Rows(
            [
                (
                    UUID("33333333-3333-3333-3333-333333333333"),
                    {
                        "dedicated_proving_transition": {
                            "old_paper_account_id": "11111111-1111-1111-1111-111111111111",
                            "new_paper_account_id": "",
                        }
                    },
                )
            ]
        )


class _NoEvidenceDb:
    async def execute(self, _statement):
        return _Rows([])


class _ExecutionDb:
    def __init__(self, *, account_id: UUID, asset_id: UUID):
        self._account_id = account_id
        self._asset_id = asset_id
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is None:
            return None
        if entity.__name__ == "Trade":
            return None
        if entity.__name__ == "PaperAccount":
            return SimpleNamespace(id=self._account_id, asset_class="crypto")
        if entity.__name__ == "Asset":
            return SimpleNamespace(
                id=self._asset_id,
                symbol="BTC",
                exchange="kraken_spot",
                asset_class="crypto",
                min_order_notional=Decimal("0"),
                qty_step_size=Decimal("0.0001"),
                supports_fractional=True,
            )
        return None

    async def execute(self, _statement):
        return _Rows([])

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


class _DuplicateExecutionDb(_ExecutionDb):
    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is not None and entity.__name__ == "Trade":
            return SimpleNamespace(
                id=uuid4(),
                execution_venue="internal_sim",
                is_paper=True,
            )
        return await super().scalar(statement)


@dataclass(frozen=True)
class _RiskContext:
    account_equity: Decimal = Decimal("100")
    max_position_size_pct: Decimal = Decimal("1")
    start_of_day_equity: Decimal = Decimal("100")
    current_equity: Decimal = Decimal("100")
    max_daily_loss_pct: Decimal = Decimal("0.2")
    high_water_mark_equity: Decimal = Decimal("100")
    max_drawdown_pct: Decimal = Decimal("0.2")
    consecutive_losses_on_pair: int = 0
    cooldown_after_losses: int = 3
    last_loss_at: datetime | None = None
    cooldown_duration_minutes: Decimal = Decimal("60")
    evaluation_time: datetime = datetime.now(timezone.utc)
    data_is_stale: bool = False
    data_has_gaps: bool = False
    global_kill_switch_engaged_state: bool | None = False
    global_kill_switch_rearm_required: bool | None = False
    account_kill_switch_engaged_state: bool | None = False
    account_kill_switch_rearm_required: bool | None = False
    global_kill_switch_state_observed: bool = True
    account_kill_switch_state_observed: bool = True


@pytest.mark.asyncio
async def test_detects_old_proving_account_entry_block_from_transition_audit() -> None:
    blocked = await orchestrator._new_entries_blocked_for_legacy_proving_account(
        db=_HelperDb(),
        paper_account_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert blocked is True


@pytest.mark.asyncio
async def test_missing_transition_evidence_does_not_block_unrelated_account() -> None:
    blocked = await orchestrator._new_entries_blocked_for_legacy_proving_account(
        db=_NoEvidenceDb(),
        paper_account_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert blocked is False


@pytest.mark.asyncio
async def test_malformed_transition_evidence_fails_closed_for_buy_gate() -> None:
    blocked = await orchestrator._new_entries_blocked_for_legacy_proving_account(
        db=_MalformedEvidenceDb(),
        paper_account_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert blocked is True


@pytest.mark.asyncio
async def test_buy_is_rejected_when_old_proving_account_new_entries_are_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    signal_id = uuid4()
    account_id = uuid4()
    asset_id = uuid4()
    db = _ExecutionDb(account_id=account_id, asset_id=asset_id)

    async def _resolve_execution_risk_context(**_kwargs):
        return _RiskContext()

    def _evaluate_signal_risk(**_kwargs):
        return SimpleNamespace(action=RiskDecisionAction.APPROVE, approved_quantity=Decimal("0.01"), reason_code=None)

    async def _persist_risk_decision(**_kwargs):
        return None

    async def _load_latest_reference_price(**_kwargs):
        return Decimal("100")

    async def _blocked(**_kwargs):
        return True

    async def _should_not_execute_internal_fill(**_kwargs):
        raise AssertionError("internal fill should not be called when legacy entry block is active")

    monkeypatch.setattr(orchestrator, "resolve_execution_risk_context", _resolve_execution_risk_context)
    monkeypatch.setattr(orchestrator, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(orchestrator, "persist_risk_decision", _persist_risk_decision)
    monkeypatch.setattr(orchestrator, "_load_latest_reference_price", _load_latest_reference_price)
    monkeypatch.setattr(orchestrator, "_new_entries_blocked_for_legacy_proving_account", _blocked)
    monkeypatch.setattr(orchestrator, "execute_internal_crypto_fill", _should_not_execute_internal_fill)

    result = await orchestrator.orchestrate_paper_signal_execution(
        db=db,
        request=orchestrator.SignalExecutionRequest(
            signal_id=signal_id,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.01"),
            actor="operator:human",
        ),
    )

    assert result.outcome == "REJECTED"
    assert result.reason_code == "OLD_PROVING_ACCOUNT_NEW_ENTRIES_BLOCKED"
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_sell_is_allowed_even_when_old_proving_account_new_entries_are_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    signal_id = uuid4()
    account_id = uuid4()
    asset_id = uuid4()
    db = _ExecutionDb(account_id=account_id, asset_id=asset_id)

    async def _resolve_execution_risk_context(**_kwargs):
        return _RiskContext()

    def _evaluate_signal_risk(**_kwargs):
        return SimpleNamespace(action=RiskDecisionAction.APPROVE, approved_quantity=Decimal("0.01"), reason_code=None)

    async def _persist_risk_decision(**_kwargs):
        return None

    async def _load_latest_reference_price(**_kwargs):
        return Decimal("100")

    async def _blocked(**_kwargs):
        return True

    async def _execute_internal_fill(**_kwargs):
        return SimpleNamespace(trade_id=uuid4())

    monkeypatch.setattr(orchestrator, "resolve_execution_risk_context", _resolve_execution_risk_context)
    monkeypatch.setattr(orchestrator, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(orchestrator, "persist_risk_decision", _persist_risk_decision)
    monkeypatch.setattr(orchestrator, "_load_latest_reference_price", _load_latest_reference_price)
    monkeypatch.setattr(orchestrator, "_new_entries_blocked_for_legacy_proving_account", _blocked)
    monkeypatch.setattr(orchestrator, "execute_internal_crypto_fill", _execute_internal_fill)

    result = await orchestrator.orchestrate_paper_signal_execution(
        db=db,
        request=orchestrator.SignalExecutionRequest(
            signal_id=signal_id,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="sell",
            quantity=Decimal("0.01"),
            actor="operator:human",
        ),
    )

    assert result.outcome == "EXECUTED"
    assert result.reason_code is None


@pytest.mark.asyncio
async def test_buy_is_unaffected_for_unrelated_account(monkeypatch: pytest.MonkeyPatch) -> None:
    signal_id = uuid4()
    account_id = uuid4()
    asset_id = uuid4()
    db = _ExecutionDb(account_id=account_id, asset_id=asset_id)

    async def _resolve_execution_risk_context(**_kwargs):
        return _RiskContext()

    def _evaluate_signal_risk(**_kwargs):
        return SimpleNamespace(action=RiskDecisionAction.APPROVE, approved_quantity=Decimal("0.01"), reason_code=None)

    async def _persist_risk_decision(**_kwargs):
        return None

    async def _load_latest_reference_price(**_kwargs):
        return Decimal("100")

    async def _blocked(**_kwargs):
        return False

    async def _execute_internal_fill(**_kwargs):
        return SimpleNamespace(trade_id=uuid4())

    monkeypatch.setattr(orchestrator, "resolve_execution_risk_context", _resolve_execution_risk_context)
    monkeypatch.setattr(orchestrator, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(orchestrator, "persist_risk_decision", _persist_risk_decision)
    monkeypatch.setattr(orchestrator, "_load_latest_reference_price", _load_latest_reference_price)
    monkeypatch.setattr(orchestrator, "_new_entries_blocked_for_legacy_proving_account", _blocked)
    monkeypatch.setattr(orchestrator, "execute_internal_crypto_fill", _execute_internal_fill)

    result = await orchestrator.orchestrate_paper_signal_execution(
        db=db,
        request=orchestrator.SignalExecutionRequest(
            signal_id=signal_id,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.01"),
            actor="operator:human",
        ),
    )

    assert result.outcome == "EXECUTED"
    assert result.reason_code is None


@pytest.mark.asyncio
async def test_duplicate_signal_short_circuits_before_buy_entry_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    signal_id = uuid4()
    account_id = uuid4()
    asset_id = uuid4()
    db = _DuplicateExecutionDb(account_id=account_id, asset_id=asset_id)

    async def _should_not_check_gate(**_kwargs):
        raise AssertionError("legacy BUY gate should not run for duplicate signals")

    monkeypatch.setattr(orchestrator, "_new_entries_blocked_for_legacy_proving_account", _should_not_check_gate)

    result = await orchestrator.orchestrate_paper_signal_execution(
        db=db,
        request=orchestrator.SignalExecutionRequest(
            signal_id=signal_id,
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.01"),
            actor="operator:human",
        ),
    )

    assert result.execution_status == "duplicate"
    assert result.outcome == "SKIPPED"
