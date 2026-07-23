from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid
import re

import pytest

from app.models.audit_log import AuditLog
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.services.autonomous_cycle.contracts import AutonomousCycleRequest, ReconciliationStatus, RiskEvaluationSummary, StrategyProposal
from app.services.autonomous_cycle.orchestrator import (
    _candidate_asset_symbols_for_product,
    _evaluate_mandate_scope,
    _persist_decision_intelligence,
    _resolve_asset_for_cycle,
    _resolve_runtime_strategy_identity,
    _run_approved_strategy,
    run_autonomous_preview_cycle,
)
from app.services.decisions.replay_context import REPLAY_CONTEXT_KEYS
from app.services.mandates.contracts import (
    AUTONOMY_LEVEL_1,
    AUTONOMY_LEVEL_2,
    MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED,
    MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
    MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
    MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
)
from app.services.strategies.base import Signal
from app.services.strategies.identity import build_strategy_identity


class _FakeDb:
    def __init__(self) -> None:
        self.cycles_by_key: dict[str, AutonomousCycleRun] = {}
        self.connection = None
        self.authorizations: list[object] = []
        self.enforce_authorization_rows = False
        self.paper_account = SimpleNamespace(id=uuid.uuid4(), is_active=True, asset_class="crypto")
        self.added: list[object] = []

    async def scalar(self, statement):
        try:
            sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            sql = str(statement)
        if "autonomous_cycle_runs" in sql:
            match = re.search(r"idempotency_key\s*=\s*'([^']+)'", sql)
            if match is not None:
                existing_cycle = self.cycles_by_key.get(match.group(1))
                if existing_cycle is not None:
                    return existing_cycle
            return next(iter(self.cycles_by_key.values()), None)
        if "autonomous_capital_mandate_authorizations" in sql:
            if not self.enforce_authorization_rows:
                return None
            mandate_id_match = re.search(r"mandate_id\s*=\s*'([^']+)'", sql)
            mandate_version_id_match = re.search(r"mandate_version_id\s*=\s*'([^']+)'", sql)
            observed_at_match = re.search(r"expires_at\s*>\s*'([^']+)'", sql)
            observed_at = datetime.fromisoformat(observed_at_match.group(1)) if observed_at_match else datetime.now(timezone.utc)
            for row in self.authorizations:
                if mandate_id_match is not None and str(getattr(row, "mandate_id", "")) != mandate_id_match.group(1):
                    continue
                if mandate_version_id_match is not None and str(getattr(row, "mandate_version_id", "")) != mandate_version_id_match.group(1):
                    continue
                revoked_at = getattr(row, "revoked_at", None)
                expires_at = getattr(row, "expires_at", None)
                authorization_state = getattr(row, "authorization_state", "AUTHORIZED")
                if authorization_state != "AUTHORIZED" or revoked_at is not None:
                    continue
                if expires_at is not None and expires_at <= observed_at:
                    continue
                return getattr(row, "mandate_authorization_id", uuid.uuid4())
            return None
        if "paper_accounts" in sql:
            return self.paper_account
        return None

    async def get(self, model, _primary_key):
        if getattr(model, "__name__", None) == "ExchangeConnection":
            return self.connection
        return None

    def add(self, instance):
        self.added.append(instance)
        if isinstance(instance, AutonomousCycleRun) and getattr(instance, "idempotency_key", None):
            self.cycles_by_key[instance.idempotency_key] = instance

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, _instance):
        return None


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def _mandate(*, status: str = "ACTIVE", autonomy_level: str = AUTONOMY_LEVEL_2) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_id=uuid.uuid4(),
        status=status,
        autonomy_level=autonomy_level,
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=123,
    )


def _version(*, allowed_order_sides: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_version_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(),
        version_number=7,
        base_currency="USD",
        authorized_capital_usd=Decimal("1000"),
        allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("10"),
        max_daily_deployed_usd=Decimal("50"),
        max_daily_realized_loss_usd=Decimal("25"),
        max_campaign_drawdown_usd=Decimal("100"),
        max_consecutive_losses=3,
        position_limit=1,
        price_evidence_max_age_seconds=300,
        max_slippage_bps=Decimal("10"),
        max_fee_bps=Decimal("10"),
        allowed_products=("BTC-USD",),
        allowed_order_sides=allowed_order_sides or ["BUY", "SELL", "HOLD"],
        approval_policy=MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
        is_authorized=True,
        is_active=True,
    )


def _version_with_identity(
    *,
    mandate_id: uuid.UUID,
    version_number: int,
    identity: str,
    is_authorized: bool,
    is_active: bool,
) -> SimpleNamespace:
    version = _version()
    version.mandate_id = mandate_id
    version.version_number = version_number
    version.allowed_strategy_versions = [identity]
    version.approval_policy = MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED
    version.is_authorized = is_authorized
    version.is_active = is_active
    return version


def _authorized_row(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _market_tuple() -> tuple[SimpleNamespace, Decimal, int]:
    evidence = SimpleNamespace(
        evidence_id=uuid.uuid4(),
        provider="kraken_spot",
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        reference_price=Decimal("50000"),
        bid=Decimal("49999"),
        ask=Decimal("50001"),
        observed_at=datetime.now(timezone.utc),
    )
    return evidence, evidence.reference_price, 0


def _patch_happy_path(monkeypatch: pytest.MonkeyPatch, mandate: SimpleNamespace, version: SimpleNamespace, *, action: str, risk_verdict: str = "ACCEPTED") -> None:
    signal_id = uuid.uuid4()
    candle_close = datetime.now(timezone.utc)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._has_valid_exact_version_authorization", _async_return(True))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._reconcile_state", _async_return(ReconciliationStatus(True, True, 0, 0, False, ())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._evaluate_risk", _async_return(RiskEvaluationSummary(risk_verdict=risk_verdict, risk_event_id=uuid.uuid4() if risk_verdict != "NOT_EVALUATED" else None, reason_code=None, approved_quantity=Decimal("0.001") if risk_verdict != "NOT_EVALUATED" else None)))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._create_canonical_signal_for_cycle",
        _async_return(SimpleNamespace(id=signal_id, asset_id=uuid.uuid4())),
    )
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._run_approved_strategy",
        _async_return(
            StrategyProposal(
                action=action,
                strategy_name="ma_crossover",
                strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
                deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
                signal_payload={"action": action.lower(), "timestamp": candle_close.isoformat()},
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate",
        _async_return(
            SimpleNamespace(
                evaluation_id=uuid.uuid4(),
                approval_result=MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
                reason_code="authorized_under_active_mandate",
                deterministic_explanation=("CHECK_PASSED:authorized_under_active_mandate",),
            )
        ),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _async_return(SimpleNamespace(crypto_order_preview_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

@pytest.mark.asyncio
async def test_decision_record_replay_context_keeps_canonical_identity_unknown_for_mandate_only_records() -> None:
    db = _FakeDb()
    cycle_id = uuid.uuid4()
    mandate_version_id = uuid.uuid4()
    risk_event_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    mandate = SimpleNamespace(
        provider="kraken_spot",
        exchange_environment="production",
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=123,
        mandate_id=mandate_id,
    )

    await _persist_decision_intelligence(
        db=db,
        cycle=SimpleNamespace(cycle_id=cycle_id, mandate_id=mandate_id, mandate_version_id=mandate_version_id, risk_event_id=risk_event_id, audit_correlation_id=uuid.uuid4()),
        mandate=mandate,
        version=SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            version_number=7,
            allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
            max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("10"),
        ),
        proposal=StrategyProposal(
            action="BUY",
            strategy_name="ma_crossover",
            strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
            deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
            signal_payload={"action": "buy", "timestamp": "2026-07-01T00:00:00Z"},
        ),
        risk_summary=RiskEvaluationSummary(
            risk_verdict="ACCEPTED",
            risk_event_id=risk_event_id,
            reason_code=None,
            approved_quantity=Decimal("0.001"),
        ),
        product_id="BTC-USD",
        reference_price=Decimal("50000"),
        evidence_age_minutes=0,
        strategy_interval="15m",
        canonical_signal_id=signal_id,
    )

    record = next(item for item in db.added if isinstance(item, DecisionRecord))
    replay_context = record.indicators["replay_context"]
    assert sorted(replay_context.keys()) == sorted(REPLAY_CONTEXT_KEYS)
    assert replay_context["identity_source"] == "AUTONOMOUS_MANDATE"
    assert replay_context["canonical_identity_present"] is False
    assert replay_context["mandate_identity_present"] is True
    assert replay_context["autonomous_mandate_id"] == str(mandate_id)
    assert replay_context["autonomous_mandate_version"] == str(mandate_version_id)
    assert replay_context["mandate_capital_campaign_row_id"] == "123"
    assert replay_context["mandate_paper_account_id"] == str(mandate.paper_account_id)
    assert replay_context["mandate_live_trading_profile_id"] == str(mandate.live_trading_profile_id)
    assert replay_context["canonical_campaign_id"] == "UNKNOWN"
    assert replay_context["canonical_campaign_version"] == "UNKNOWN"
    assert replay_context["runtime_campaign_id"] == "UNKNOWN"
    assert replay_context["paper_account_id"] == "UNKNOWN"
    assert replay_context["live_trading_profile_id"] == "UNKNOWN"
    assert replay_context["capital_campaign_id"] == "UNKNOWN"
    assert replay_context["capital_campaign_version"] == "UNKNOWN"
    assert replay_context["normalized_risk_verdict"] == "ALLOW"
    assert replay_context["candle_id"] == "UNKNOWN"
    assert replay_context["candle_close_time"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_decision_record_replay_context_persists_canonical_identity_when_supplied() -> None:
    db = _FakeDb()
    cycle_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    risk_event_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    candle_id = uuid.uuid4()
    candle_close_time = datetime(2026, 7, 16, 13, 45, tzinfo=timezone.utc)
    mandate = SimpleNamespace(
        provider="kraken_spot",
        exchange_environment="production",
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=123,
        mandate_id=mandate_id,
    )
    canonical_identity = {
        "canonical_campaign_id": uuid.UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        "canonical_campaign_version": 1,
        "runtime_campaign_id": 2,
        "canonical_paper_account_id": mandate.paper_account_id,
        "canonical_live_trading_profile_id": mandate.live_trading_profile_id,
    }

    await _persist_decision_intelligence(
        db=db,
        cycle=SimpleNamespace(cycle_id=cycle_id, mandate_id=mandate_id, mandate_version_id=uuid.uuid4(), risk_event_id=risk_event_id, audit_correlation_id=uuid.uuid4()),
        mandate=mandate,
        version=SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            version_number=7,
            allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
            max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("10"),
        ),
        proposal=StrategyProposal(
            action="BUY",
            strategy_name="ma_crossover",
            strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
            deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
            signal_payload={"action": "buy", "timestamp": candle_close_time.isoformat()},
        ),
        risk_summary=RiskEvaluationSummary(
            risk_verdict="ACCEPTED",
            risk_event_id=risk_event_id,
            reason_code=None,
            approved_quantity=Decimal("0.001"),
        ),
        product_id="BTC-USD",
        reference_price=Decimal("50000"),
        evidence_age_minutes=0,
        strategy_interval="15m",
        canonical_signal_id=signal_id,
        candle_id=candle_id,
        candle_close_time=candle_close_time,
        canonical_identity=canonical_identity,
    )

    record = next(item for item in db.added if isinstance(item, DecisionRecord))
    replay_context = record.indicators["replay_context"]
    assert replay_context["identity_source"] == "BOTH_VERIFIED_MATCH"
    assert replay_context["canonical_identity_present"] is True
    assert replay_context["mandate_identity_present"] is True
    assert replay_context["canonical_campaign_id"] == "e9a9e8e9-9574-498d-b49e-f011218c7f2b"
    assert replay_context["canonical_campaign_version"] == "1"
    assert replay_context["runtime_campaign_id"] == "2"
    assert replay_context["canonical_paper_account_id"] == str(mandate.paper_account_id)
    assert replay_context["canonical_live_trading_profile_id"] == str(mandate.live_trading_profile_id)
    assert replay_context["paper_account_id"] == str(mandate.paper_account_id)
    assert replay_context["live_trading_profile_id"] == str(mandate.live_trading_profile_id)
    assert replay_context["capital_campaign_id"] == "e9a9e8e9-9574-498d-b49e-f011218c7f2b"
    assert replay_context["capital_campaign_version"] == "1"
    assert replay_context["candle_id"] == str(candle_id)
    assert replay_context["candle_close_time"] == candle_close_time.isoformat()
    assert replay_context["normalized_risk_verdict"] == "ALLOW"


@pytest.mark.asyncio
async def test_decision_record_replay_context_conflicts_fail_closed_without_overwrite() -> None:
    db = _FakeDb()
    canonical_identity = {
        "canonical_campaign_id": uuid.UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"),
        "canonical_campaign_version": 1,
        "runtime_campaign_id": 2,
        "canonical_paper_account_id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "canonical_live_trading_profile_id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    }

    await _persist_decision_intelligence(
        db=db,
        cycle=SimpleNamespace(cycle_id=uuid.uuid4(), mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), audit_correlation_id=uuid.uuid4()),
        mandate=SimpleNamespace(
            provider="kraken_spot",
            exchange_environment="production",
            live_trading_profile_id=uuid.uuid4(),
            paper_account_id=uuid.uuid4(),
            capital_campaign_id=123,
            mandate_id=uuid.uuid4(),
        ),
        version=SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            version_number=7,
            allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
            max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("10"),
        ),
        proposal=StrategyProposal(
            action="BUY",
            strategy_name="ma_crossover",
            strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
            deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
            signal_payload={"action": "buy", "timestamp": "2026-07-01T00:00:00Z"},
        ),
        risk_summary=RiskEvaluationSummary(
            risk_verdict="ACCEPTED",
            risk_event_id=uuid.uuid4(),
            reason_code=None,
            approved_quantity=Decimal("0.001"),
        ),
        product_id="BTC-USD",
        reference_price=Decimal("50000"),
        evidence_age_minutes=0,
        strategy_interval="15m",
        canonical_signal_id=uuid.uuid4(),
        canonical_identity=canonical_identity,
    )

    record = next(item for item in db.added if isinstance(item, DecisionRecord))
    replay_context = record.indicators["replay_context"]
    assert replay_context["identity_source"] == "UNKNOWN"
    assert replay_context["canonical_identity_present"] is True
    assert replay_context["mandate_identity_present"] is True
    assert replay_context["identity_mismatches"] == [
        "canonical_live_trading_profile_id!=mandate_live_trading_profile_id",
        "canonical_paper_account_id!=mandate_paper_account_id",
    ]
    assert replay_context["evidence_completeness"] != "COMPLETE"


@pytest.mark.asyncio
async def test_decision_record_replay_context_holds_risk_as_unknown() -> None:
    db = _FakeDb()
    await _persist_decision_intelligence(
        db=db,
        cycle=SimpleNamespace(cycle_id=uuid.uuid4(), mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), risk_event_id=None, audit_correlation_id=uuid.uuid4()),
        mandate=SimpleNamespace(
            provider="kraken_spot",
            exchange_environment="production",
            live_trading_profile_id=None,
            paper_account_id=None,
            capital_campaign_id=None,
            mandate_id=uuid.uuid4(),
        ),
        version=SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            version_number=7,
            allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
            max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("10"),
        ),
        proposal=StrategyProposal(
            action="HOLD",
            strategy_name="ma_crossover",
            strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
            deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
            signal_payload={"action": "hold"},
        ),
        risk_summary=RiskEvaluationSummary(
            risk_verdict="NOT_EVALUATED",
            risk_event_id=None,
            reason_code=None,
            approved_quantity=None,
        ),
        product_id="BTC-USD",
        reference_price=Decimal("50000"),
        evidence_age_minutes=0,
        strategy_interval="15m",
        canonical_signal_id=None,
    )

    record = next(item for item in db.added if isinstance(item, DecisionRecord))
    replay_context = record.indicators["replay_context"]
    assert replay_context["normalized_risk_verdict"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_cycle_selects_latest_canonical_governing_version_and_keeps_legacy_version_historical(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    legacy_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    canonical_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.1")
    version_1 = _version_with_identity(mandate_id=mandate.mandate_id, version_number=1, identity=legacy_identity, is_authorized=False, is_active=False)
    version_2 = _version_with_identity(mandate_id=mandate.mandate_id, version_number=2, identity=canonical_identity, is_authorized=False, is_active=False)
    db.authorizations = [
        _authorized_row(mandate_id=mandate.mandate_id, mandate_version_id=version_1.mandate_version_id, expires_at=datetime.now(timezone.utc).replace(year=2099)),
        _authorized_row(mandate_id=mandate.mandate_id, mandate_version_id=version_2.mandate_version_id, expires_at=datetime.now(timezone.utc).replace(year=2099)),
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version_2, version_1]))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._reconcile_state", _async_return(ReconciliationStatus(True, True, 0, 0, False, ())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._evaluate_risk",
        _async_return(
            RiskEvaluationSummary(
                risk_verdict="ACCEPTED",
                risk_event_id=uuid.uuid4(),
                reason_code=None,
                approved_quantity=Decimal("0.001"),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._create_canonical_signal_for_cycle",
        _async_return(SimpleNamespace(id=uuid.uuid4(), asset_id=uuid.uuid4())),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _async_return(uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate",
        _async_return(
            SimpleNamespace(
                evaluation_id=uuid.uuid4(),
                approval_result=MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
                reason_code="authorized_under_active_mandate",
                deterministic_explanation=("CHECK_PASSED:authorized_under_active_mandate",),
            )
        ),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _async_return(SimpleNamespace(crypto_order_preview_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="governing-version-selection"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is not None
    assert result.mandate_version_id == version_2.mandate_version_id


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = []
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="missing-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_authorization_is_for_another_version(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="other-version-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_is_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            revoked_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="revoked-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            expires_at=datetime.now(timezone.utc).replace(year=2000),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="expired-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PAUSED", "REVOKED", "EXPIRED", "DRAFT"])
async def test_cycle_non_active_mandate_finishes_hold(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    db = _FakeDb()
    mandate = _mandate(status=status)
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed=f"status-{status}"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "HOLD"
    assert result.preview_id is None
    assert "mandate_status" in (result.diagnostics.failure_reason or "")


@pytest.mark.asyncio
async def test_level1_mandate_finishes_hold_before_preview_or_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(autonomy_level=AUTONOMY_LEVEL_1)
    calls = {"versions": 0, "preview": 0, "paper_execution": 0}

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))

    async def _versions(*_args, **_kwargs):
        calls["versions"] += 1
        return [_version()]

    async def _preview(*_args, **_kwargs):
        calls["preview"] += 1
        raise AssertionError("LEVEL_1 must not create an autonomous preview")

    async def _paper_execution(*_args, **_kwargs):
        calls["paper_execution"] += 1
        raise AssertionError("LEVEL_1 must not execute an autonomous paper trade")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _versions)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _paper_execution)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(
            mandate_id=mandate.mandate_id,
            actor="orchestration_worker",
            forced_action="BUY",
            idempotency_seed="level1-central-guard",
        ),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.termination_stage == "validate_mandate"
    assert result.diagnostics.failure_reason == "autonomy_level_does_not_allow_autonomous_execution"
    assert calls == {"versions": 0, "preview": 0, "paper_execution": 0}


@pytest.mark.asyncio
async def test_failed_level2_mandate_evaluation_blocks_preview_and_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(autonomy_level=AUTONOMY_LEVEL_2)
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")
    calls = {"preview": 0, "paper_execution": 0}

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate",
        _async_return(
            SimpleNamespace(
                evaluation_id=uuid.uuid4(),
                approval_result=MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
                reason_code="strategy_not_allowed_by_mandate",
                deterministic_explanation=("CHECK_FAILED:strategy_not_allowed_by_mandate",),
            )
        ),
    )

    async def _preview(*_args, **_kwargs):
        calls["preview"] += 1
        raise AssertionError("failed mandate evaluation must not create a preview")

    async def _paper_execution(*_args, **_kwargs):
        calls["paper_execution"] += 1
        raise AssertionError("failed mandate evaluation must not execute a paper trade")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _paper_execution)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(
            mandate_id=mandate.mandate_id,
            actor="orchestration_worker",
            forced_action="BUY",
            idempotency_seed="level2-failed-evaluation-guard",
        ),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.termination_stage == "mandate_evaluation"
    assert result.diagnostics.failure_reason == "strategy_not_allowed_by_mandate"
    assert calls == {"preview": 0, "paper_execution": 0}


@pytest.mark.asyncio
async def test_provider_readiness_failure_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate()
    version = _version()
    version.approval_policy = MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="NOT_READY", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="not-ready"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "provider_not_ready"


@pytest.mark.asyncio
async def test_reconciliation_failure_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    version.approval_policy = MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._reconcile_state",
        _async_return(ReconciliationStatus(True, True, 1, 0, False, ("CHECK_FAILED:unresolved_live_order_exists",))),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="reconcile-fail"),
    )

    assert result.state == "COMPLETE"
    assert result.diagnostics.failure_reason == "reconciliation_not_ready"


@pytest.mark.asyncio
async def test_hold_proposal_does_not_generate_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="HOLD", risk_verdict="NOT_EVALUATED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="HOLD", idempotency_seed="hold-1"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "HOLD"
    assert result.preview_id is None


@pytest.mark.asyncio
async def test_hold_completes_end_to_end_even_when_hold_absent_from_allowed_order_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression for the exact production defect: a real mandate version
    # configured with only BUY/SELL in allowed_order_sides (no "HOLD" entry)
    # must still let a HOLD cycle complete with mandate_verdict AUTHORIZED,
    # not REJECTED/side_not_allowed.
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["BUY", "SELL"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="HOLD", risk_verdict="NOT_EVALUATED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="HOLD", idempotency_seed="hold-no-side-2"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "HOLD"
    assert result.diagnostics.termination_stage != "mandate_evaluation"
    assert result.diagnostics.failure_reason != "side_not_allowed"
    assert result.preview_id is None


@pytest.mark.asyncio
async def test_sell_proposal_generates_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="SELL", risk_verdict="ACCEPTED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="SELL", idempotency_seed="sell-1"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "SELL"
    assert result.preview_id is not None


@pytest.mark.asyncio
async def test_mandate_rejection_blocks_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["HOLD"])
    version.approval_policy = MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._reconcile_state",
        _async_return(ReconciliationStatus(True, True, 0, 0, False, ())),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="scope-reject"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "side_not_allowed"


@pytest.mark.asyncio
async def test_risk_rejection_blocks_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY", risk_verdict="REJECTED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="risk-reject-1"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.risk_verdict == "REJECTED"


@pytest.mark.asyncio
async def test_idempotent_repeated_cycle_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    calls = {"preview": 0, "persist": 0}

    async def _preview(**_kwargs):
        calls["preview"] += 1
        return SimpleNamespace(crypto_order_preview_id=uuid.uuid4())

    async def _persist(**_kwargs):
        calls["persist"] += 1
        return uuid.uuid4()

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _persist)

    req = AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="idem-1")
    first = await run_autonomous_preview_cycle(db=db, request=req)
    second = await run_autonomous_preview_cycle(db=db, request=req)

    assert first.replayed is False
    assert second.replayed is True
    assert calls["preview"] == 1
    assert calls["persist"] == 1
    cycle_rows = [item for item in db.added if isinstance(item, AutonomousCycleRun)]
    assert len(cycle_rows) == 1


@pytest.mark.asyncio
async def test_non_terminal_loading_cycle_resumes_without_new_cycle_row(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    existing_cycle = AutonomousCycleRun(
        idempotency_key="resume-loading",
        mandate_id=mandate.mandate_id,
        state="LOADING",
        evaluation_stage="load_mandate",
        cycle_context={},
        diagnostics={},
        deterministic_explanation=[],
        audit_correlation_id=uuid.uuid4(),
        started_at=datetime.now(timezone.utc),
    )
    existing_cycle.cycle_id = uuid.uuid4()
    db.cycles_by_key[existing_cycle.idempotency_key] = existing_cycle

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="resume-loading"),
    )

    assert result.state == "COMPLETE"
    assert result.cycle_id == existing_cycle.cycle_id
    cycle_rows = [item for item in db.added if isinstance(item, AutonomousCycleRun)]
    assert cycle_rows == []


@pytest.mark.asyncio
async def test_non_terminal_ready_cycle_fails_closed_as_not_resumable(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    existing_cycle = AutonomousCycleRun(
        idempotency_key="resume-ready",
        mandate_id=mandate.mandate_id,
        state="READY",
        evaluation_stage="provider_readiness",
        cycle_context={},
        diagnostics={},
        deterministic_explanation=[],
        audit_correlation_id=uuid.uuid4(),
        started_at=datetime.now(timezone.utc),
    )
    existing_cycle.cycle_id = uuid.uuid4()
    db.cycles_by_key[existing_cycle.idempotency_key] = existing_cycle

    calls = {"preview": 0, "persist": 0}

    async def _preview(**_kwargs):
        calls["preview"] += 1
        return SimpleNamespace(crypto_order_preview_id=uuid.uuid4())

    async def _persist(**_kwargs):
        calls["persist"] += 1
        return uuid.uuid4()

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _persist)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="resume-ready"),
    )

    assert result.state == "FAILED"
    assert result.diagnostics.failure_reason == "existing_non_terminal_cycle_not_resumable"
    assert calls["persist"] == 0
    assert calls["preview"] == 0


@pytest.mark.asyncio
async def test_stale_non_terminal_cycle_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    existing_cycle = AutonomousCycleRun(
        idempotency_key="resume-stale",
        mandate_id=mandate.mandate_id,
        state="LOADING",
        evaluation_stage="load_mandate",
        cycle_context={},
        diagnostics={},
        deterministic_explanation=[],
        audit_correlation_id=uuid.uuid4(),
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    existing_cycle.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    existing_cycle.cycle_id = uuid.uuid4()
    db.cycles_by_key[existing_cycle.idempotency_key] = existing_cycle

    calls = {"preview": 0, "persist": 0}

    async def _preview(**_kwargs):
        calls["preview"] += 1
        return SimpleNamespace(crypto_order_preview_id=uuid.uuid4())

    async def _persist(**_kwargs):
        calls["persist"] += 1
        return uuid.uuid4()

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _persist)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="resume-stale"),
    )

    assert result.state == "FAILED"
    assert result.diagnostics.failure_reason == "stale_non_terminal_cycle"
    assert calls["persist"] == 0
    assert calls["preview"] == 0


@pytest.mark.asyncio
async def test_audit_event_written_for_completed_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="audit-1"),
    )

    assert any(isinstance(item, AuditLog) and item.action == "AUTONOMOUS_CYCLE_COMPLETED" for item in db.added)


@pytest.mark.asyncio
async def test_cycle_diagnostics_include_termination_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="PAUSED")
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="diag-1"),
    )

    assert result.diagnostics.termination_stage == "validate_mandate"
    assert result.diagnostics.duration_ms >= 0


@pytest.mark.asyncio
async def test_no_live_submission_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    called = {"prepare": 0, "submit": 0}

    def _prepare_fail(*_args, **_kwargs):
        called["prepare"] += 1
        raise AssertionError("live prepare path must not be called")

    def _submit_fail(*_args, **_kwargs):
        called["submit"] += 1
        raise AssertionError("live submit path must not be called")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    monkeypatch.setattr("app.services.live_crypto_orders.service.prepare_confirmation", _prepare_fail)
    monkeypatch.setattr("app.services.live_crypto_orders.service.submit", _submit_fail)

    await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="nosubmit-1"),
    )

    assert called["prepare"] == 0
    assert called["submit"] == 0


def test_orchestrator_has_no_live_order_service_import() -> None:
    import app.services.autonomous_cycle.orchestrator as orchestrator_module

    assert not hasattr(orchestrator_module, "live_crypto_orders_service")


@pytest.mark.asyncio
async def test_cycle_buy_handoff_uses_paper_execution_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    signal_id = uuid.uuid4()
    calls = {"execute": 0}

    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._create_canonical_signal_for_cycle",
        _async_return(SimpleNamespace(id=signal_id, asset_id=uuid.uuid4())),
    )

    async def _execute(*, db, request):
        calls["execute"] += 1
        assert request.signal_id == signal_id
        return SimpleNamespace(
            outcome="EXECUTED",
            execution_status="executed",
            trade_id=uuid.uuid4(),
            execution_venue="internal_sim",
            reason_code=None,
            reason_text=None,
        )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _execute)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="handoff-buy-1"),
    )

    assert result.state == "COMPLETE"
    assert calls["execute"] == 1
    assert result.cycle_context["execution_handoff"]["status"] == "PAPER_EXECUTION_ACCEPTED"
    assert result.cycle_context["execution_handoff"]["execution_handoff"] == "PAPER_EXECUTION"
    assert result.cycle_context["execution_handoff"]["canonical_signal"]["signal_id"] == str(signal_id)


@pytest.mark.asyncio
async def test_cycle_sell_handoff_uses_paper_execution_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="SELL")
    signal_id = uuid.uuid4()
    calls = {"execute": 0}

    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._create_canonical_signal_for_cycle",
        _async_return(SimpleNamespace(id=signal_id, asset_id=uuid.uuid4())),
    )

    async def _execute(*, db, request):
        calls["execute"] += 1
        assert request.side == "sell"
        return SimpleNamespace(
            outcome="EXECUTED",
            execution_status="executed",
            trade_id=uuid.uuid4(),
            execution_venue="internal_sim",
            reason_code=None,
            reason_text=None,
        )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _execute)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="SELL", idempotency_seed="handoff-sell-1"),
    )

    assert result.state == "COMPLETE"
    assert calls["execute"] == 1
    assert result.cycle_context["execution_handoff"]["status"] == "PAPER_EXECUTION_ACCEPTED"


@pytest.mark.asyncio
async def test_cycle_risk_rejection_skips_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY", risk_verdict="REJECTED")
    calls = {"execute": 0}

    async def _execute_fail(*_args, **_kwargs):
        calls["execute"] += 1
        raise AssertionError("risk-rejected cycle must not execute paper signal")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _execute_fail)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="handoff-risk-reject"),
    )

    assert result.state == "COMPLETE"
    assert calls["execute"] == 0
    assert result.cycle_context["execution_handoff"]["status"] == "PAPER_EXECUTION_SKIPPED"


@pytest.mark.asyncio
async def test_cycle_hold_does_not_attempt_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="HOLD", risk_verdict="NOT_EVALUATED")
    calls = {"execute": 0}

    async def _execute_fail(*_args, **_kwargs):
        calls["execute"] += 1
        raise AssertionError("HOLD action must not execute")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _execute_fail)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="HOLD", idempotency_seed="handoff-hold-1"),
    )

    assert result.state == "COMPLETE"
    assert calls["execute"] == 0
    assert result.cycle_context["execution_handoff"]["status"] == "HOLD_NOT_EXECUTABLE"


@pytest.mark.asyncio
async def test_cycle_paper_handoff_replay_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    signal_id = uuid.uuid4()
    calls = {"signal": 0, "execute": 0}

    async def _signal(*_args, **_kwargs):
        calls["signal"] += 1
        return SimpleNamespace(id=signal_id, asset_id=uuid.uuid4())

    async def _execute(*, db, request):
        calls["execute"] += 1
        return SimpleNamespace(
            outcome="EXECUTED",
            execution_status="executed",
            trade_id=uuid.uuid4(),
            execution_venue="internal_sim",
            reason_code=None,
            reason_text=None,
        )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._create_canonical_signal_for_cycle", _signal)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.orchestrate_paper_signal_execution", _execute)

    request = AutonomousCycleRequest(
        mandate_id=mandate.mandate_id,
        actor="operator:owner",
        forced_action="BUY",
        idempotency_seed="handoff-replay-1",
    )
    first = await run_autonomous_preview_cycle(db=db, request=request)
    second = await run_autonomous_preview_cycle(db=db, request=request)

    assert first.replayed is False
    assert second.replayed is True
    assert calls["signal"] == 1
    assert calls["execute"] == 1


def test_resolve_runtime_strategy_identity_never_returns_none_for_selected_strategy() -> None:
    version = _version()
    proposal = StrategyProposal(
        action="HOLD",
        strategy_name="ma_crossover",
        strategy_version="none",
        deterministic_explanation=("CHECK_FAILED:insufficient_candle_context",),
    )

    resolved = _resolve_runtime_strategy_identity(proposal=proposal, version=version)

    assert resolved == build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    assert resolved != "none"


def test_mandate_scope_accepts_exact_canonical_identity_membership() -> None:
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    assert runtime_identity == version.allowed_strategy_versions[0]

    verdict, reason = _evaluate_mandate_scope(
        version=version,
        product_id="BTC-USD",
        action="HOLD",
        strategy_version=runtime_identity,
    )

    assert verdict == "AUTHORIZED"
    assert reason == "authorized_under_active_mandate"


def test_mandate_scope_buy_authorized_when_buy_allowed() -> None:
    version = _version(allowed_order_sides=["BUY", "SELL"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="BUY", strategy_version=runtime_identity
    )

    assert verdict == "AUTHORIZED"
    assert reason == "authorized_under_active_mandate"


def test_mandate_scope_sell_authorized_when_sell_allowed() -> None:
    version = _version(allowed_order_sides=["BUY", "SELL"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="SELL", strategy_version=runtime_identity
    )

    assert verdict == "AUTHORIZED"
    assert reason == "authorized_under_active_mandate"


def test_mandate_scope_hold_never_fails_side_authorization_even_when_absent_from_allowed_sides() -> None:
    # HOLD deliberately excluded from allowed_order_sides -- must still pass,
    # since HOLD submits no order and there is no "side" to authorize.
    version = _version(allowed_order_sides=["BUY", "SELL"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="HOLD", strategy_version=runtime_identity
    )

    assert verdict == "AUTHORIZED"
    assert reason == "authorized_under_active_mandate"


def test_mandate_scope_hold_authorized_even_with_empty_allowed_sides_representation() -> None:
    # Regression for the exact production defect: a mandate version whose
    # allowed_order_sides never listed "HOLD" at all must not block HOLD.
    version = _version(allowed_order_sides=["BUY"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="HOLD", strategy_version=runtime_identity
    )

    assert verdict == "AUTHORIZED"
    assert reason == "authorized_under_active_mandate"


def test_mandate_scope_unauthorized_buy_still_rejected() -> None:
    version = _version(allowed_order_sides=["SELL"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="BUY", strategy_version=runtime_identity
    )

    assert verdict == "REJECTED"
    assert reason == "side_not_allowed"


def test_mandate_scope_unauthorized_sell_still_rejected() -> None:
    version = _version(allowed_order_sides=["BUY"])
    runtime_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    verdict, reason = _evaluate_mandate_scope(
        version=version, product_id="BTC-USD", action="SELL", strategy_version=runtime_identity
    )

    assert verdict == "REJECTED"
    assert reason == "side_not_allowed"


@pytest.mark.asyncio
async def test_cycle_propagates_canonical_strategy_identity_into_mandate_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="ACTIVE")
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="HOLD", risk_verdict="NOT_EVALUATED")

    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._run_approved_strategy",
        _async_return(
            StrategyProposal(
                action="HOLD",
                strategy_name="ma_crossover",
                strategy_version="none",
                deterministic_explanation=("CHECK_FAILED:insufficient_candle_context",),
            )
        ),
    )

    captured: dict[str, object] = {}

    async def _capture_mandate_eval(*, db, request):
        captured["strategy_version"] = request.strategy_version
        return SimpleNamespace(
            evaluation_id=uuid.uuid4(),
            approval_result=MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
            reason_code="authorized_under_active_mandate",
            deterministic_explanation=("CHECK_PASSED:authorized_under_active_mandate",),
        )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate", _capture_mandate_eval)

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="identity-propagation"),
    )

    assert result.state == "COMPLETE"
    assert captured["strategy_version"] == build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


def test_candidate_asset_symbols_for_product_normalizes_btc_and_xbt_aliases() -> None:
    assert _candidate_asset_symbols_for_product("BTC-USD") == ("BTC", "XBT", "XXBT")
    assert _candidate_asset_symbols_for_product("BTC/USD") == ("BTC", "XBT", "XXBT")
    assert _candidate_asset_symbols_for_product("XBT/USD") == ("BTC", "XBT", "XXBT")


@pytest.mark.asyncio
async def test_resolve_asset_for_cycle_scopes_to_provider_exchange() -> None:
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _Db:
        async def execute(self, _statement):
            return _Result(
                [
                    SimpleNamespace(id=uuid.uuid4(), symbol="BTC", exchange="coinbase_advanced", asset_class="crypto", is_active=True),
                ]
            )

    asset, reason = await _resolve_asset_for_cycle(
        db=_Db(),
        product_id="BTC-USD",
        provider="coinbase_advanced",
        exchange_environment="production",
    )

    assert reason is None
    assert asset is not None
    assert asset.symbol == "BTC"
    assert asset.exchange == "coinbase_advanced"


@pytest.mark.asyncio
async def test_resolve_asset_for_cycle_fails_closed_when_ambiguous() -> None:
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _Db:
        async def execute(self, _statement):
            return _Result(
                [
                    SimpleNamespace(id=uuid.uuid4(), symbol="BTC", exchange="kraken_spot", asset_class="crypto", is_active=True),
                    SimpleNamespace(id=uuid.uuid4(), symbol="XBT", exchange="kraken_spot", asset_class="crypto", is_active=True),
                ]
            )

    asset, reason = await _resolve_asset_for_cycle(
        db=_Db(),
        product_id="BTC-USD",
        provider="kraken_spot",
        exchange_environment="production",
    )

    assert asset is None
    assert reason == "ambiguous_asset_resolution_for_strategy"


@pytest.mark.asyncio
async def test_resolve_asset_for_cycle_missing_asset_returns_not_found() -> None:
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Db:
        async def execute(self, _statement):
            return _Result()

    asset, reason = await _resolve_asset_for_cycle(
        db=_Db(),
        product_id="BTC-USD",
        provider="kraken_spot",
        exchange_environment="production",
    )

    assert asset is None
    assert reason == "asset_not_found_for_strategy"


@pytest.mark.asyncio
async def test_run_approved_strategy_executes_with_resolved_asset_and_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = SimpleNamespace(id=uuid.uuid4(), symbol="BTC", asset_class="crypto", exchange="kraken_spot", is_active=True)
    strategy = SimpleNamespace(
        id=uuid.uuid4(),
        slug="ma_crossover",
        module_version="1.0.0",
        created_at=datetime.now(timezone.utc),
    )
    candles = [
        SimpleNamespace(
            open_time=datetime.now(timezone.utc),
            close_time=datetime.now(timezone.utc),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("10"),
        )
        for _ in range(3)
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _Db:
        async def execute(self, statement):
            sql = str(statement)
            if "FROM assets" in sql:
                return _Result([asset])
            if "FROM strategies" in sql:
                return _Result([strategy])
            if "FROM candles" in sql:
                return _Result(candles)
            return _Result([])

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM parameter_sets" in sql:
                return None
            return None

    captured: dict[str, object] = {}

    class _Generator:
        def generate_signal(self, context):
            captured["candles"] = context.candles
            captured["asset_metadata"] = context.asset_metadata
            return Signal(
                action="buy",
                strength=Decimal("1.0"),
                reason="Fast SMA crossed above Slow SMA.",
                indicators={
                    "fast_ma": "3.0",
                    "slow_ma": "2.5",
                    "previous_fast_ma": "2.0",
                    "previous_slow_ma": "2.4",
                    "crossover_state": "bullish_cross",
                    "signal_generated": "buy",
                    "evaluated_conditions": {
                        "buy": {
                            "previous_fast_ma_lte_previous_slow_ma": True,
                            "fast_ma_gt_slow_ma": True,
                        },
                        "sell": {
                            "previous_fast_ma_gte_previous_slow_ma": False,
                            "fast_ma_lt_slow_ma": False,
                        },
                    },
                    "selection_explanations": {
                        "buy": "BUY selected because previous_fast_ma <= previous_slow_ma and fast_ma > slow_ma evaluated to true.",
                        "sell": "SELL not selected because previous_fast_ma >= previous_slow_ma and fast_ma < slow_ma evaluated to false.",
                        "hold": "HOLD not selected because the bullish crossover conditions were satisfied.",
                    },
                },
                timestamp=datetime.now(timezone.utc),
            )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.has", lambda _slug: True)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.get", lambda _slug: _Generator())

    proposal = await _run_approved_strategy(
        db=_Db(),
        mandate=SimpleNamespace(provider="kraken_spot", exchange_environment="production"),
        version=SimpleNamespace(allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")]),
        request=AutonomousCycleRequest(mandate_id=uuid.uuid4(), actor="operator:owner", product_id="BTC-USD", strategy_interval="15m"),
    )

    assert proposal.action == "BUY"
    assert proposal.strategy_name == "ma_crossover"
    assert proposal.strategy_version == build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    assert proposal.signal_payload is not None
    assert proposal.signal_payload["indicators"]["signal_generated"] == "buy"
    assert captured.get("candles") is not None
    assert len(captured["candles"]) == 3
    assert captured["asset_metadata"] == {"symbol": "BTC", "asset_class": "crypto"}


@pytest.mark.asyncio
async def test_run_approved_strategy_supports_lightweight_signal_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = SimpleNamespace(id=uuid.uuid4(), symbol="BTC", asset_class="crypto", exchange="kraken_spot", is_active=True)
    strategy = SimpleNamespace(
        id=uuid.uuid4(),
        slug="ma_crossover",
        module_version="1.0.0",
        created_at=datetime.now(timezone.utc),
    )
    candles = [
        SimpleNamespace(
            open_time=datetime.now(timezone.utc),
            close_time=datetime.now(timezone.utc),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("10"),
        )
        for _ in range(3)
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _Db:
        async def execute(self, statement):
            sql = str(statement)
            if "FROM assets" in sql:
                return _Result([asset])
            if "FROM strategies" in sql:
                return _Result([strategy])
            if "FROM candles" in sql:
                return _Result(candles)
            return _Result([])

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM parameter_sets" in sql:
                return None
            return None

    class _Generator:
        def generate_signal(self, _context):
            return SimpleNamespace(
                action="buy",
                strength=Decimal("1.0"),
                reason="Fast SMA crossed above Slow SMA.",
                indicators={"signal_generated": "buy", "crossover_state": "bullish_cross"},
                timestamp=datetime.now(timezone.utc),
            )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.has", lambda _slug: True)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.get", lambda _slug: _Generator())

    proposal = await _run_approved_strategy(
        db=_Db(),
        mandate=SimpleNamespace(provider="kraken_spot", exchange_environment="production"),
        version=SimpleNamespace(allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")]),
        request=AutonomousCycleRequest(mandate_id=uuid.uuid4(), actor="operator:owner", product_id="BTC-USD", strategy_interval="15m"),
    )

    assert proposal.action == "BUY"
    assert proposal.signal_payload is not None
    assert proposal.signal_payload["action"] == "buy"
    assert proposal.signal_payload["indicators"]["signal_generated"] == "buy"
    assert "timeline" in proposal.signal_payload


@pytest.mark.asyncio
async def test_run_approved_strategy_malformed_signal_fails_closed_to_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = SimpleNamespace(id=uuid.uuid4(), symbol="BTC", asset_class="crypto", exchange="kraken_spot", is_active=True)
    strategy = SimpleNamespace(
        id=uuid.uuid4(),
        slug="ma_crossover",
        module_version="1.0.0",
        created_at=datetime.now(timezone.utc),
    )
    candles = [
        SimpleNamespace(
            open_time=datetime.now(timezone.utc),
            close_time=datetime.now(timezone.utc),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("10"),
        )
        for _ in range(3)
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _Db:
        async def execute(self, statement):
            sql = str(statement)
            if "FROM assets" in sql:
                return _Result([asset])
            if "FROM strategies" in sql:
                return _Result([strategy])
            if "FROM candles" in sql:
                return _Result(candles)
            return _Result([])

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM parameter_sets" in sql:
                return None
            return None

    class _Generator:
        def generate_signal(self, _context):
            return SimpleNamespace(
                action="invalid",
                reason="Malformed signal action",
                indicators={"unsafe": object()},
                timestamp=datetime.now(timezone.utc),
            )

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.has", lambda _slug: True)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.strategy_registry.get", lambda _slug: _Generator())

    proposal = await _run_approved_strategy(
        db=_Db(),
        mandate=SimpleNamespace(provider="kraken_spot", exchange_environment="production"),
        version=SimpleNamespace(allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")]),
        request=AutonomousCycleRequest(mandate_id=uuid.uuid4(), actor="operator:owner", product_id="BTC-USD", strategy_interval="15m"),
    )

    assert proposal.action == "HOLD"
    assert proposal.signal_payload is not None
    assert proposal.signal_payload.get("indicators", {}).get("unsafe") is None


@pytest.mark.asyncio
async def test_decision_record_persists_canonical_product_identity() -> None:
    db = _FakeDb()
    cycle_id = uuid.uuid4()
    mandate_version_id = uuid.uuid4()
    await _persist_decision_intelligence(
        db=db,
        cycle=SimpleNamespace(cycle_id=cycle_id, mandate_id=uuid.uuid4(), mandate_version_id=mandate_version_id, risk_event_id=None, audit_correlation_id=uuid.uuid4()),
        mandate=SimpleNamespace(provider="kraken_spot", mandate_id=uuid.uuid4()),
        version=SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
            max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("10"),
        ),
        proposal=StrategyProposal(
            action="BUY",
            strategy_name="ma_crossover",
            strategy_version=build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
            deterministic_explanation=("CHECK_PASSED:strategy_evaluated",),
            signal_payload={
                "action": "buy",
                "reason": "Fast SMA crossed above Slow SMA.",
                "indicators": {
                    "fast_ma": "3.0",
                    "slow_ma": "2.5",
                    "previous_fast_ma": "2.0",
                    "previous_slow_ma": "2.4",
                    "crossover_state": "bullish_cross",
                    "signal_generated": "buy",
                    "evaluated_conditions": {
                        "buy": {
                            "previous_fast_ma_lte_previous_slow_ma": True,
                            "fast_ma_gt_slow_ma": True,
                        },
                        "sell": {
                            "previous_fast_ma_gte_previous_slow_ma": False,
                            "fast_ma_lt_slow_ma": False,
                        },
                    },
                    "selection_explanations": {
                        "buy": "BUY selected because previous_fast_ma <= previous_slow_ma and fast_ma > slow_ma evaluated to true.",
                        "sell": "SELL not selected because previous_fast_ma >= previous_slow_ma and fast_ma < slow_ma evaluated to false.",
                        "hold": "HOLD not selected because the bullish crossover conditions were satisfied.",
                    },
                },
                "strength": "1.0",
                "timestamp": "2026-07-01T00:00:00Z",
            },
        ),
        risk_summary=RiskEvaluationSummary(
            risk_verdict="ACCEPTED",
            risk_event_id=None,
            reason_code=None,
            approved_quantity=Decimal("0.001"),
        ),
        product_id="BTC-USD",
        reference_price=Decimal("50000"),
        evidence_age_minutes=0,
        strategy_interval="15m",
        canonical_signal_id=uuid.uuid4(),
    )

    record = next(item for item in db.added if isinstance(item, DecisionRecord))
    snapshot = next(item for item in db.added if isinstance(item, DecisionSnapshot))
    assert record.asset["product_id"] == "BTC-USD"
    assert record.indicators["signal_generated"] == "buy"
    assert record.indicators["selection_explanations"]["hold"].startswith("HOLD not selected because")
    assert record.generated_signals[0]["strategy_evidence"]["crossover_state"] == "bullish_cross"
    assert snapshot.strategy_inputs["strategy_evidence"]["fast_ma"] == "3.0"
    assert snapshot.strategy_inputs["signal_reason"] == "Fast SMA crossed above Slow SMA."
