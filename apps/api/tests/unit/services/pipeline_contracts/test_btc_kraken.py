from __future__ import annotations

import builtins
import os
import socket
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.pipeline_contracts.btc_kraken import (
    ACCOUNTING_RESULT_REFERENCE_VERSION,
    BTC_KRAKEN_INSTRUMENT_VERSION,
    CANDLE_OBSERVATION_VERSION,
    EXECUTION_INTENT_VERSION,
    GOVERNANCE_AUTHORIZATION_REFERENCE_VERSION,
    PROVIDER_FILL_REFERENCE_VERSION,
    PROVIDER_SUBMISSION_RESULT_VERSION,
    RECONCILIATION_RESULT_REFERENCE_VERSION,
    RISK_DECISION_REFERENCE_VERSION,
    STRATEGY_EVALUATION_VERSION,
    AccountingResultReferenceV1,
    BtcKrakenInstrumentV1,
    CandleObservationV1,
    ExecutionIntentV1,
    ExecutionSide,
    GovernanceAuthorizationReferenceV1,
    GovernanceDisposition,
    OrderType,
    ProviderFillReferenceV1,
    ProviderOutcome,
    ProviderSubmissionResultV1,
    ReconciliationResultReferenceV1,
    ReconciliationStatus,
    RiskDecisionReferenceV1,
    RiskDisposition,
    StrategyAction,
    StrategyEvaluationResultV1,
)
from app.services.pipeline_contracts.envelope import (
    CANONICAL_ENVELOPE_SCHEMA_VERSION,
    CanonicalEnvelopeV1,
)
from app.services.pipeline_contracts.identifiers import (
    AccountingId,
    AssetId,
    AssetIdentity,
    CampaignId,
    EventId,
    ExecutionClaimId,
    InstrumentId,
    InstrumentIdentity,
    LineageAuthority,
    LineageKind,
    LineageReference,
    PackageId,
    ProofId,
    ProviderOrderId,
    ReconciliationId,
)


AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _envelope(number: int = 1) -> CanonicalEnvelopeV1:
    return CanonicalEnvelopeV1(
        event_id=EventId(value=_uuid(number)), event_type="stage_result",
        schema_version=CANONICAL_ENVELOPE_SCHEMA_VERSION, source="unit:test",
        occurred_at=AT, received_at=AT, stage_version="stage/v1",
    )


def _instrument() -> BtcKrakenInstrumentV1:
    return BtcKrakenInstrumentV1(
        schema_version=BTC_KRAKEN_INSTRUMENT_VERSION,
        asset_identity=AssetIdentity(asset_id=AssetId(value=_uuid(10)), symbol="BTC"),
        instrument_identity=InstrumentIdentity(instrument_id=InstrumentId(value=_uuid(11)), canonical_symbol="BTC-USD"),
        internal_product="BTC-USD", canonical_base_asset="BTC", quote_asset="USD",
        provider="kraken_spot", provider_asset_code="XBT", provider_pair="XXBTZUSD",
    )


def _lineage(kind: LineageKind, authority: LineageAuthority = LineageAuthority.VERIFIED) -> LineageReference:
    return LineageReference(kind=kind, value=None if authority is LineageAuthority.ABSENT else f"{kind.value.lower()}-1", authority=authority)


def _candle() -> CandleObservationV1:
    return CandleObservationV1(
        schema_version=CANDLE_OBSERVATION_VERSION, envelope=_envelope(), instrument=_instrument(), interval="15m",
        open_time=AT, close_time=datetime(2026, 7, 15, 12, 15, tzinfo=timezone.utc),
        open=Decimal("50000.00"), high=Decimal("50100.00"), low=Decimal("49900.00"),
        close=Decimal("50050.00"), volume=Decimal("1.2300"),
    )


def _strategy() -> StrategyEvaluationResultV1:
    return StrategyEvaluationResultV1(
        schema_version=STRATEGY_EVALUATION_VERSION, envelope=_envelope(2), instrument=_instrument(),
        action=StrategyAction.BUY, strength=Decimal("0.7500"), reason="Fast average crossed above slow average.",
        explanation=("condition_a", "condition_b"), strategy_identity="momentum", strategy_version="1.0.0",
        source_signal_ref=_lineage(LineageKind.EVENT), evaluated_at=AT,
    )


def _risk(disposition: RiskDisposition = RiskDisposition.APPROVED) -> RiskDecisionReferenceV1:
    return RiskDecisionReferenceV1(
        schema_version=RISK_DECISION_REFERENCE_VERSION, envelope=_envelope(3),
        risk_event=_lineage(LineageKind.RISK), disposition=disposition,
        requested_notional=Decimal("5.00"),
        approved_notional=Decimal("5.00") if disposition is not RiskDisposition.REJECTED else None,
        reason_code="risk_approved" if disposition is not RiskDisposition.REJECTED else "risk_veto",
        recorded_at=AT,
    )


def _governance(disposition: GovernanceDisposition = GovernanceDisposition.AUTHORIZED) -> GovernanceAuthorizationReferenceV1:
    return GovernanceAuthorizationReferenceV1(
        schema_version=GOVERNANCE_AUTHORIZATION_REFERENCE_VERSION, envelope=_envelope(4), disposition=disposition,
        campaign_id=CampaignId(value=_uuid(20)), campaign_version=3, runtime_campaign_id=17,
        mandate_id=_uuid(21), mandate_version_id=_uuid(22), mandate_version_number=7,
        authorization_evidence=_lineage(LineageKind.APPROVAL), reason_code="authorized_under_active_mandate",
        recorded_at=AT,
    )


def _intent(side: ExecutionSide = ExecutionSide.BUY) -> ExecutionIntentV1:
    return ExecutionIntentV1(
        schema_version=EXECUTION_INTENT_VERSION, envelope=_envelope(5), instrument=_instrument(),
        side=side, order_type=OrderType.MARKET,
        quote_notional=Decimal("5.00") if side is ExecutionSide.BUY else None,
        base_quantity=Decimal("0.00010000") if side is ExecutionSide.SELL else None,
        package_id=PackageId(value=_uuid(30)), claim_id=ExecutionClaimId(value=_uuid(31)),
        proof_id=ProofId(value=_uuid(32)), internal_client_order_id="client-order-1",
        authorization_evidence=_lineage(LineageKind.APPROVAL),
        custody_evidence=_lineage(LineageKind.EXECUTION_CLAIM),
    )


def _provider(outcome: ProviderOutcome = ProviderOutcome.ACCEPTED) -> ProviderSubmissionResultV1:
    return ProviderSubmissionResultV1(
        schema_version=PROVIDER_SUBMISSION_RESULT_VERSION, envelope=_envelope(6), outcome=outcome,
        provider="kraken_spot", internal_client_order_id="client-order-1",
        provider_order_id=ProviderOrderId(value="KRAKEN-1") if outcome is ProviderOutcome.ACCEPTED else None,
        provider_status="open" if outcome is ProviderOutcome.ACCEPTED else None,
        reason_code="missing_provider_order_id" if outcome is ProviderOutcome.AMBIGUOUS else None,
        safe_error_fields={"code": "EOrder:Invalid price", "category": "provider_rejection"},
        provider_timestamp=AT,
        reconciliation_required=outcome in {ProviderOutcome.ACCEPTED, ProviderOutcome.AMBIGUOUS},
        blind_resubmission_permitted=False,
    )


def _reconciliation(status: ReconciliationStatus = ReconciliationStatus.PARTIALLY_FILLED) -> ReconciliationResultReferenceV1:
    return ReconciliationResultReferenceV1(
        schema_version=RECONCILIATION_RESULT_REFERENCE_VERSION, envelope=_envelope(7), live_order_id=_uuid(40),
        provider_order_id=ProviderOrderId(value="KRAKEN-1"), reconciliation_id=ReconciliationId(value=_uuid(41)),
        status=status, filled_quantity=Decimal("0.00004000"),
        remaining_quantity=Decimal("0.00006000") if status is ReconciliationStatus.PARTIALLY_FILLED else Decimal("0"),
        fills=(
            ProviderFillReferenceV1(schema_version=PROVIDER_FILL_REFERENCE_VERSION, provider_fill_id="fill-2", quantity=Decimal("0.00002000"), price=Decimal("50010.00"), occurred_at=AT),
            ProviderFillReferenceV1(schema_version=PROVIDER_FILL_REFERENCE_VERSION, provider_fill_id="fill-1", quantity=Decimal("0.00002000"), price=Decimal("50000.00"), occurred_at=AT),
        ),
        provider_truth_at=AT, idempotency_key="reconciliation-key-1", evidence=_lineage(LineageKind.RECONCILIATION),
    )


def _accounting() -> AccountingResultReferenceV1:
    return AccountingResultReferenceV1(
        schema_version=ACCOUNTING_RESULT_REFERENCE_VERSION, envelope=_envelope(8),
        accounting_id=AccountingId(value=_uuid(50)), reconciliation_id=ReconciliationId(value=_uuid(41)),
        source_fill_ids=("fill-2", "fill-1"), gross_amount=Decimal("5.00000000"),
        fee_amount=Decimal("0.01250000"), fee_asset="USD", net_amount=Decimal("4.98750000"),
        currency="USD", realized_pnl=None, position_reference="position-1",
        evidence=_lineage(LineageKind.ACCOUNTING), recorded_at=AT,
    )


def _fill() -> ProviderFillReferenceV1:
    return ProviderFillReferenceV1(
        schema_version=PROVIDER_FILL_REFERENCE_VERSION,
        provider_fill_id="fill-1", quantity=Decimal("0.00002000"),
        price=Decimal("50000.00"), occurred_at=AT,
    )


@pytest.mark.parametrize("factory", [_instrument, _candle, _strategy, _risk, _governance, _intent, _provider, _fill, _reconciliation, _accounting])
def test_every_contract_requires_its_exact_v1_schema(factory) -> None:
    instance = factory()
    values = instance.model_dump(mode="python")
    values.pop("schema_version")
    with pytest.raises(ValidationError):
        instance.__class__.model_validate(values)
    values["schema_version"] = "unknown/v2"
    with pytest.raises(ValidationError):
        instance.__class__.model_validate(values)


def test_serialization_hash_decimal_scale_uuid_time_and_order_are_deterministic() -> None:
    candle = _candle()
    assert candle.canonical_bytes() == _candle().canonical_bytes()
    assert b'"volume":"1.2300"' in candle.canonical_bytes()
    assert b'"open_time":"2026-07-15T12:00:00.000000Z"' in candle.canonical_bytes()
    assert str(_uuid(10)).encode() in candle.canonical_bytes()
    assert candle.integrity_hash() != candle.model_copy(update={"volume": Decimal("1.2301")}).integrity_hash()
    strategy = _strategy()
    assert strategy.explanation == ("condition_a", "condition_b")
    assert strategy.integrity_hash() != strategy.model_copy(update={"explanation": ("condition_b", "condition_a")}).integrity_hash()


def test_float_and_nonfinite_decimal_and_naive_time_fail() -> None:
    with pytest.raises(ValidationError, match="binary floating-point"):
        _candle().model_copy(update={}).__class__.model_validate({**_candle().model_dump(), "volume": 1.2})
    with pytest.raises(ValidationError, match="finite"):
        _candle().model_copy(update={}).__class__.model_validate({**_candle().model_dump(), "volume": Decimal("NaN")})
    with pytest.raises(ValidationError, match="timezone-aware"):
        _strategy().model_copy(update={}).__class__.model_validate({**_strategy().model_dump(), "evaluated_at": datetime(2026, 7, 15)})


def test_btc_xbt_product_pair_asset_uuid_and_quote_remain_distinct() -> None:
    instrument = _instrument()
    assert instrument.asset_identity.asset_id.value == _uuid(10)
    assert instrument.asset_identity.symbol == "BTC"
    assert instrument.canonical_base_asset == "BTC"
    assert instrument.provider_asset_code == "XBT"
    assert instrument.internal_product == "BTC-USD"
    assert instrument.provider_pair == "XXBTZUSD"
    assert instrument.quote_asset == "USD"


def test_strategy_actions_are_distinct_and_hold_is_not_execution_side() -> None:
    assert set(StrategyAction) == {StrategyAction.BUY, StrategyAction.SELL, StrategyAction.HOLD}
    assert set(ExecutionSide) == {ExecutionSide.BUY, ExecutionSide.SELL}
    with pytest.raises(ValidationError):
        _intent().model_copy(update={}).__class__.model_validate({**_intent().model_dump(), "side": "HOLD"})


def test_risk_approval_rejection_and_lineage_authority_cannot_be_confused() -> None:
    assert _risk().disposition is RiskDisposition.APPROVED
    assert _risk(RiskDisposition.REJECTED).approved_notional is None
    values = _risk().model_dump(mode="python")
    values["risk_event"] = _lineage(LineageKind.RISK, LineageAuthority.SYNTHETIC)
    with pytest.raises(ValidationError, match="VERIFIED"):
        RiskDecisionReferenceV1.model_validate(values)
    rejected = _risk(RiskDisposition.REJECTED).model_dump(mode="python")
    rejected["approved_notional"] = Decimal("5")
    with pytest.raises(ValidationError, match="cannot carry"):
        RiskDecisionReferenceV1.model_validate(rejected)


def test_governance_approval_rejection_and_versions_remain_distinct() -> None:
    approved = _governance()
    rejected = _governance(GovernanceDisposition.REJECTED)
    assert approved.disposition is GovernanceDisposition.AUTHORIZED
    assert rejected.disposition is GovernanceDisposition.REJECTED
    assert approved.campaign_version == 3
    assert approved.mandate_version_id == _uuid(22)
    assert approved.mandate_version_number == 7
    values = approved.model_dump(mode="python")
    values["authorization_evidence"] = _lineage(LineageKind.APPROVAL, LineageAuthority.LEGACY_UNVERIFIED)
    with pytest.raises(ValidationError, match="VERIFIED"):
        GovernanceAuthorizationReferenceV1.model_validate(values)


def test_execution_intents_preserve_side_sizing_and_grant_no_authority() -> None:
    buy = _intent(ExecutionSide.BUY)
    sell = _intent(ExecutionSide.SELL)
    assert buy.quote_notional == Decimal("5.00") and buy.base_quantity is None
    assert sell.base_quantity == Decimal("0.00010000") and sell.quote_notional is None
    assert buy.grants_authority is False and sell.grants_authority is False
    with pytest.raises(ValidationError):
        ExecutionIntentV1.model_validate({**buy.model_dump(), "base_quantity": Decimal("0.1")})


def test_provider_outcomes_and_truthful_identifiers_are_enforced() -> None:
    assert {outcome for outcome in ProviderOutcome} == {
        ProviderOutcome.ACCEPTED, ProviderOutcome.REJECTED,
        ProviderOutcome.PRE_SUBMISSION_FAILURE, ProviderOutcome.AMBIGUOUS,
    }
    assert _provider(ProviderOutcome.ACCEPTED).provider_order_id is not None
    assert _provider(ProviderOutcome.REJECTED).provider_order_id is None
    assert _provider(ProviderOutcome.PRE_SUBMISSION_FAILURE).provider_order_id is None
    ambiguous = _provider(ProviderOutcome.AMBIGUOUS)
    assert ambiguous.reconciliation_required is True
    assert ambiguous.blind_resubmission_permitted is False
    with pytest.raises(ValidationError, match="requires provider_order_id"):
        ProviderSubmissionResultV1.model_validate({**_provider().model_dump(), "provider_order_id": None})
    with pytest.raises(ValidationError, match="requires reconciliation"):
        ProviderSubmissionResultV1.model_validate({**ambiguous.model_dump(), "reconciliation_required": False})


def test_safe_error_fields_are_deeply_immutable_and_detached_from_input() -> None:
    source = {"code": "EOrder:Invalid price", "category": "provider_rejection"}
    result = ProviderSubmissionResultV1.model_validate({**_provider().model_dump(), "safe_error_fields": source})
    source["code"] = "changed"
    assert result.safe_error_fields == (("category", "provider_rejection"), ("code", "EOrder:Invalid price"))
    with pytest.raises(TypeError):
        result.safe_error_fields[0] = ("code", "changed")  # type: ignore[index]


def test_safe_error_fields_are_order_independent_hash_protected_and_json_compatible() -> None:
    first = ProviderSubmissionResultV1.model_validate({
        **_provider().model_dump(), "safe_error_fields": {"z": "last", "a": "first"},
    })
    second = ProviderSubmissionResultV1.model_validate({
        **_provider().model_dump(), "safe_error_fields": {"a": "first", "z": "last"},
    })
    changed = ProviderSubmissionResultV1.model_validate({
        **_provider().model_dump(), "safe_error_fields": {"a": "changed", "z": "last"},
    })
    changed_key = ProviderSubmissionResultV1.model_validate({
        **_provider().model_dump(), "safe_error_fields": {"b": "first", "z": "last"},
    })
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.integrity_hash() == second.integrity_hash()
    assert first.integrity_hash() != changed.integrity_hash()
    assert first.integrity_hash() != changed_key.integrity_hash()
    assert b'"safe_error_fields":[["a","first"],["z","last"]]' in first.canonical_bytes()
    assert ProviderSubmissionResultV1.model_validate({**_provider().model_dump(), "safe_error_fields": None}).safe_error_fields is None
    assert ProviderSubmissionResultV1.model_validate({**_provider().model_dump(), "safe_error_fields": {}}).safe_error_fields == ()
    for invalid in ({"": "value"}, {"key": ""}, {"key": 1}):
        with pytest.raises(ValidationError):
            ProviderSubmissionResultV1.model_validate({**_provider().model_dump(), "safe_error_fields": invalid})


def test_reconciliation_statuses_fill_order_and_contradictions_are_preserved() -> None:
    assert {ReconciliationStatus.OPEN, ReconciliationStatus.PARTIALLY_FILLED, ReconciliationStatus.FILLED}.issubset(set(ReconciliationStatus))
    partial = _reconciliation()
    assert partial.fills[0].provider_fill_id == "fill-2"
    assert partial.fills[1].provider_fill_id == "fill-1"
    filled = _reconciliation(ReconciliationStatus.FILLED)
    assert filled.remaining_quantity == Decimal("0")
    with pytest.raises(ValidationError, match="cannot have remaining"):
        ReconciliationResultReferenceV1.model_validate({**filled.model_dump(), "remaining_quantity": Decimal("1")})
    # Current delayed state is represented by OPEN/RECONCILIATION_REQUIRED, not an invented DELAYED enum.
    assert ReconciliationStatus.OPEN is not ReconciliationStatus.RECONCILIATION_REQUIRED


def test_accounting_values_sources_and_lineage_authorities_are_exact() -> None:
    accounting = _accounting()
    assert accounting.source_fill_ids == ("fill-2", "fill-1")
    assert accounting.gross_amount == Decimal("5.00000000")
    assert accounting.fee_amount == Decimal("0.01250000")
    assert accounting.fee_asset == "USD" and accounting.currency == "USD"
    assert accounting.net_amount == Decimal("4.98750000")
    assert accounting.evidence.authority is LineageAuthority.VERIFIED
    for authority in LineageAuthority:
        ref = _lineage(LineageKind.ACCOUNTING, authority)
        assert ref.authority is authority
        if authority is LineageAuthority.ABSENT:
            assert ref.value is None


def test_constructors_have_no_environment_filesystem_network_or_wall_clock_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("contract construction attempted external access")

    monkeypatch.setattr(os, "getenv", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(builtins, "open", _forbidden)
    assert _candle().open_time == AT
    assert _intent().grants_authority is False
    assert _accounting().recorded_at == AT


def test_isolated_module_import_has_no_application_side_effects() -> None:
    script = textwrap.dedent(
        """
        import builtins
        import importlib
        import importlib.abc
        import os
        import pathlib
        import socket
        import sys
        import time
        import uuid
        from pydantic import BaseModel

        import app.services.pipeline_contracts.envelope
        import app.services.pipeline_contracts.identifiers
        import app.services.pipeline_contracts.serialization

        class _WarmPydanticPluginDiscovery(BaseModel):
            value: int

        target_module = "app.services.pipeline_contracts.btc_kraken"

        def guard(original):
            def guarded(*args, **kwargs):
                if sys._getframe(1).f_globals.get("__name__") == target_module:
                    raise AssertionError("application-level access during btc_kraken import")
                return original(*args, **kwargs)
            return guarded

        class ForbiddenApplicationImport(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith(("app.db", "app.services.data", "app.services.live")):
                    raise AssertionError(f"forbidden production import: {fullname}")
                return None

        sys.meta_path.insert(0, ForbiddenApplicationImport())
        os.getenv = guard(os.getenv)
        socket.socket = guard(socket.socket)
        socket.create_connection = guard(socket.create_connection)
        time.time = guard(time.time)
        time.time_ns = guard(time.time_ns)
        uuid.uuid4 = guard(uuid.uuid4)
        builtins.open = guard(builtins.open)
        pathlib.Path.open = guard(pathlib.Path.open)
        pathlib.Path.read_text = guard(pathlib.Path.read_text)
        pathlib.Path.read_bytes = guard(pathlib.Path.read_bytes)
        sys.modules.pop(target_module, None)
        module = importlib.import_module(target_module)
        assert module.ProviderSubmissionResultV1.__module__ == module.__name__
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    assert completed.returncode == 0, completed.stderr
